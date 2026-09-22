"""Stream-copy MOV remux and decoded-media verification."""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import struct
import subprocess
from collections import defaultdict
from pathlib import Path


def find_ffmpeg() -> tuple[str, str]:
    root = Path(__file__).resolve().parent
    if getattr(__import__('sys'), 'frozen', False):
        root = Path(__import__('sys').executable).resolve().parent
    for candidate in (root / 'ffmpeg.exe', root / 'ffmpeg', shutil.which('ffmpeg')):
        if candidate and Path(candidate).is_file():
            ffmpeg = str(candidate)
            probe = str(Path(ffmpeg).with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe'))
            if Path(probe).is_file():
                return ffmpeg, probe
    raise RuntimeError('Video processing requires ffmpeg and ffprobe beside the app or on PATH.')


def _run(args: list[str]) -> str:
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', creationflags=flags)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'Video command failed.')
    return result.stdout


def probe(path: str, ffprobe: str) -> dict:
    return json.loads(_run([ffprobe, '-v', 'error', '-show_streams', '-show_format',
                            '-of', 'json', path]))


def packet_hashes(path: str, ffprobe: str) -> dict[int, list[str]]:
    """Hash each encoded packet, grouped by track, independent of MOV interleaving."""
    data = json.loads(_run([ffprobe, '-v', 'error', '-show_packets',
                            '-show_data_hash', 'sha256', '-show_entries',
                            'packet=stream_index,data_hash', '-of', 'json', path]))
    hashes: dict[int, list[str]] = defaultdict(list)
    for packet in data.get('packets', []):
        hashes[packet['stream_index']].append(packet['data_hash'])
    return dict(hashes)


_AAC_LAVC_ID = re.compile(rb'Lavc\d+\.\d+\.\d+\x00')


def _aac_packet_locations(path: str, ffprobe: str) -> list[dict]:
    data = json.loads(_run([ffprobe, '-v', 'error', '-show_packets',
                            '-show_entries', 'packet=stream_index,pos,size',
                            '-of', 'json', path]))
    return data.get('packets', [])


def _redact_aac_packet(packet: bytes) -> tuple[bytes, bool]:
    """Neutralise only the FFmpeg identifier in the observed AAC fill element."""
    match = _AAC_LAVC_ID.match(packet, 2) if packet.startswith(b'\xdc\x00') else None
    if not match:
        return packet, False
    return packet[:2] + b'\0' * (match.end() - 2) + packet[match.end():], True


def clear_lavc_aac_identifiers(path: str, ffprobe: str) -> int:
    """Edit AAC fill bytes in place; sizes, sample offsets, and audio data stay put."""
    audio_indices = {s['index'] for s in probe(path, ffprobe)['streams']
                     if s['codec_name'] == 'aac' and s['codec_type'] == 'audio'}
    changed = 0
    with open(path, 'r+b') as file:
        for packet in _aac_packet_locations(path, ffprobe):
            if packet['stream_index'] not in audio_indices:
                continue
            pos, size = int(packet['pos']), int(packet['size'])
            file.seek(pos)
            original = file.read(size)
            replacement, found = _redact_aac_packet(original)
            if found:
                file.seek(pos)
                file.write(replacement)
                changed += 1
        file.flush()
        os.fsync(file.fileno())
    return changed


def _normalised_aac_hashes(path: str, ffprobe: str) -> dict[int, list[str]]:
    """Expected packet hashes after redacting the known AAC fill identifier."""
    audio_indices = {s['index'] for s in probe(path, ffprobe)['streams']
                     if s['codec_name'] == 'aac' and s['codec_type'] == 'audio'}
    result: dict[int, list[str]] = defaultdict(list)
    with open(path, 'rb') as file:
        for packet in _aac_packet_locations(path, ffprobe):
            if packet['stream_index'] not in audio_indices:
                continue
            file.seek(int(packet['pos']))
            raw = file.read(int(packet['size']))
            cleaned, _ = _redact_aac_packet(raw)
            result[packet['stream_index']].append('SHA256:' + hashlib.sha256(cleaned).hexdigest())
    return dict(result)


def _decoded_audio_hash(path: str, ffmpeg: str, audio_index: int) -> str:
    return _run([ffmpeg, '-v', 'error', '-nostdin', '-i', path,
                 '-map', f'0:{audio_index}', '-c:a', 'pcm_f32le',
                 '-f', 'hash', '-hash', 'SHA256', '-']).strip()


def verify_streams(source: str, dest: str, ffprobe: str,
                   allow_aac_identifier_cleanup: bool = False) -> str:
    before, after = probe(source, ffprobe), probe(dest, ffprobe)
    if after['format'].get('tags', {}).get('major_brand', '').strip() != 'qt':
        raise RuntimeError('Output does not have the QuickTime qt brand.')
    bs, ds = before['streams'], after['streams']
    if len(bs) != len(ds):
        raise RuntimeError('A media stream was lost during the MOV remux.')
    if abs(float(before['format']['duration']) - float(after['format']['duration'])) > 0.05:
        raise RuntimeError('Video duration changed by more than 50 ms during the MOV remux.')
    fields = ('codec_type', 'codec_name', 'width', 'height', 'sample_rate',
              'channels', 'sample_aspect_ratio', 'pix_fmt', 'color_space',
              'color_transfer', 'color_primaries', 'field_order')
    for a, b in zip(bs, ds):
        for field in fields:
            if a.get(field) != b.get(field):
                raise RuntimeError(f'Media stream {a["index"]} changed {field}: '
                                   f'{a.get(field)} -> {b.get(field)}')
        if a.get('codec_type') == 'video' and a.get('avg_frame_rate') != b.get('avg_frame_rate'):
            raise RuntimeError('Video frame rate changed during the MOV remux.')
        a_rotation = [x.get('rotation') for x in a.get('side_data_list', []) if 'rotation' in x]
        b_rotation = [x.get('rotation') for x in b.get('side_data_list', []) if 'rotation' in x]
        if a_rotation != b_rotation:
            raise RuntimeError('Video display rotation changed during the MOV remux.')
    original_hashes = packet_hashes(source, ffprobe)
    remuxed_hashes = packet_hashes(dest, ffprobe)
    if allow_aac_identifier_cleanup:
        ffmpeg, _ = find_ffmpeg()
        normalised = _normalised_aac_hashes(source, ffprobe)
        for index, hashes in normalised.items():
            if original_hashes.get(index) != hashes:
                if _decoded_audio_hash(source, ffmpeg, index) != _decoded_audio_hash(dest, ffmpeg, index):
                    raise RuntimeError('Decoded audio changed while clearing its encoder identifier.')
            original_hashes[index] = hashes
    if original_hashes != remuxed_hashes:
        raise RuntimeError('Encoded media packets changed beyond the identified AAC fill bytes.')
    return f'{sum(map(len, original_hashes.values()))} packets verified; decoded audio unchanged'


def clear_ffmpeg_video_vendor(path: str) -> int:
    """Clear FFmpeg's video sample-entry vendor code, leaving it unspecified.

    Only the four-byte vendor slot in a well-formed video ``stsd`` entry is
    changed. Other occurrences of the same bytes are never touched.
    """
    file_size = os.path.getsize(path)
    video_entries = {b'avc1', b'avc3', b'hvc1', b'hev1', b'mp4v',
                     b'apch', b'apcn', b'apcs', b'apco', b'ap4h'}
    container_path = (b'moov', b'trak', b'mdia', b'minf', b'stbl')
    changed = 0

    def boxes(file, start: int, end: int):
        pos = start
        while pos + 8 <= end:
            file.seek(pos)
            header = file.read(8)
            size, kind = struct.unpack('>I4s', header)
            header_size = 8
            if size == 1:
                extended = file.read(8)
                if len(extended) != 8:
                    raise RuntimeError('Truncated MOV box header.')
                size = struct.unpack('>Q', extended)[0]
                header_size = 16
            elif size == 0:
                size = end - pos
            if size < header_size or pos + size > end:
                raise RuntimeError('Invalid MOV box size while checking vendor field.')
            yield pos, pos + size, pos + header_size, kind
            pos += size
        if pos != end:
            raise RuntimeError('Invalid trailing bytes in MOV box.')

    with open(path, 'r+b') as file:
        def walk(start: int, end: int, depth: int):
            nonlocal changed
            for _, box_end, payload, kind in boxes(file, start, end):
                if depth < len(container_path) and kind == container_path[depth]:
                    walk(payload, box_end, depth + 1)
                elif depth == len(container_path) and kind == b'stsd':
                    if box_end - payload < 8:
                        raise RuntimeError('Truncated MOV sample description.')
                    file.seek(payload + 4)  # version and flags
                    count = struct.unpack('>I', file.read(4))[0]
                    entries_start = payload + 8
                    entries = list(boxes(file, entries_start, box_end))
                    if count != len(entries):
                        raise RuntimeError('MOV sample description count mismatch.')
                    for entry_start, entry_end, _, codec in entries:
                        if codec not in video_entries or entry_end - entry_start < 24:
                            continue
                        vendor_offset = entry_start + 20
                        file.seek(vendor_offset)
                        if file.read(4) == b'FFMP':
                            file.seek(vendor_offset)
                            file.write(b'\0\0\0\0')
                            changed += 1
        walk(0, file_size, 0)
        file.flush()
        os.fsync(file.fileno())
    return changed


def remux_to_mov(source: str, dest: str) -> str:
    ffmpeg, ffprobe = find_ffmpeg()
    _run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
          '-i', source, '-map', '0', '-map_metadata', '-1', '-map_chapters', '-1',
          '-c', 'copy', '-metadata:s:v:0', 'handler_name=Core Media Video',
          '-metadata:s:a:0', 'handler_name=Core Media Audio',
          '-movflags', '+faststart', '-f', 'mov', dest])
    clear_ffmpeg_video_vendor(dest)
    clear_lavc_aac_identifiers(dest, ffprobe)
    if b'Lavc' in Path(dest).read_bytes():
        raise RuntimeError('An unrecognised Lavc identifier remains in the output.')
    return verify_streams(source, dest, ffprobe, allow_aac_identifier_cleanup=True)
