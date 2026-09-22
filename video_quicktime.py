"""Lossless video remux and verification for the one-click MOV workflow."""

from __future__ import annotations

import json
import os
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


def verify_streams(source: str, dest: str, ffprobe: str) -> str:
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
    if original_hashes != remuxed_hashes:
        raise RuntimeError('Encoded video/audio packets changed during the MOV remux.')
    return f'{sum(map(len, original_hashes.values()))} encoded packets unchanged'


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
    return verify_streams(source, dest, ffprobe)
