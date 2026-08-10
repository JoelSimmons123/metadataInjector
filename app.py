import json
import binascii
import re
import struct
import zlib
import xml.etree.ElementTree as ET
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QPixmap, QPainter, QColor, QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QAbstractItemView
)

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.webp', '.heic', '.heif', '.avif'}
VIDEO_EXTS = {'.mov', '.mp4', '.m4v'}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS

# Groups that describe the physical file / decoded pixels rather than portable metadata.
IGNORE_GROUP_PREFIXES = ('File:', 'System:', 'Composite:')
IGNORE_KEYS = {'ExifTool:ExifToolVersion'}
CORE_STRUCTURAL_TAGS = {
    'ImageWidth', 'ImageHeight', 'BitDepth', 'ColorType', 'Compression', 'Filter', 'Interlace',
    'FileType', 'FileTypeExtension', 'MIMEType', 'ImageSize', 'Megapixels',
}

# Metadata that describes the destination's pixel layout rather than the source camera.
# These values are normalised to the target image and are therefore expected to differ
# from a reference image with different dimensions/orientation.
TARGET_LAYOUT_TAGS = {
    'Orientation',
    'ImageWidth', 'ImageHeight', 'ImageLength',
    'ExifImageWidth', 'ExifImageHeight',
    'PixelXDimension', 'PixelYDimension',
    'RelatedImageWidth', 'RelatedImageHeight', 'RelatedImageLength',
    'ImageSize', 'Megapixels',
}


# Video mode deliberately copies user/provenance metadata, not media-track facts.
# Codec, dimensions, duration, frame rate, rotation, HDR signalling, audio layout,
# handler/vendor IDs and timed metadata streams describe the target's actual media
# and must remain truthful to that target.  v2.2 also treats ALL capture/create/modify
# times as target-owned: reference timestamps are never injected into a video.
VIDEO_TRANSFER_TAGS = (
    'Keys:Make', 'Keys:Model', 'Keys:Software',
    'Keys:GPSCoordinates', 'Keys:LocationAccuracyHorizontal',
    'Keys:FullFrameRatePlaybackIntent',
)
VIDEO_TRANSFER_CANONICAL = {
    'Make', 'Model', 'Software', 'GPSCoordinates',
    'LocationAccuracyHorizontal', 'FullFrameRatePlaybackIntent',
}
VIDEO_TEMPORAL_LOCAL_TAGS = {
    'CreationDate', 'CreateDate', 'ModifyDate',
    'TrackCreateDate', 'TrackModifyDate',
    'MediaCreateDate', 'MediaModifyDate',
    'DateTimeOriginal', 'DateTimeDigitized', 'DateCreated',
}
VIDEO_STRUCTURAL_TAGS = {
    'ImageWidth', 'ImageHeight', 'Duration', 'VideoFrameRate', 'Rotation',
    'CompressorID', 'CompressorName', 'VideoCodec', 'BitDepth',
    'AudioFormat', 'AudioChannels', 'AudioSampleRate',
}


# Cross-format targets do not contain HEIC auxiliary image streams such as depth maps,
# portrait-effect mattes, semantic mattes, or HDR gain maps.  XMP fields that describe
# those streams would be stale pointers after cloning and are stripped from cross-format
# PNG output.  Normal camera/date/GPS/descriptive XMP remains intact.
AUXILIARY_XMP_NAMESPACE_HINTS = (
    'pixeldatainfo', 'portraiteffectsmatte', 'semanticsegmentationmatte',
    'hdrgainmap', 'depthdata',
)
AUXILIARY_XMP_LOCAL_NAMES = {
    'AuxiliaryImageSubType', 'AuxiliaryImageType', 'NativeFormat', 'StoredFormat',
    'PortraitEffectsMatteVersion', 'SemanticSegmentationMatteVersion',
    'HDRGainMapVersion', 'HDRGainMapHeadroom', 'DepthDataVersion',
}

APP_STYLE = r"""
QWidget {
    background: #0b1020;
    color: #e8ecf5;
    font-family: "Segoe UI";
    font-size: 10.5pt;
}
QMainWindow { background: #0b1020; }
QLabel#Title {
    font-size: 25pt;
    font-weight: 800;
    color: #f8fafc;
}
QLabel#Subtitle { color: #94a3b8; font-size: 10.5pt; }
QLabel#SectionTitle { font-size: 12pt; font-weight: 700; color: #f8fafc; }
QLabel#Muted { color: #8995aa; }
QLabel#Small { color: #94a3b8; font-size: 9pt; }
QLabel#StatusReady {
    color: #86efac;
    background: #12331f;
    border: 1px solid #245c38;
    border-radius: 11px;
    padding: 4px 9px;
    font-weight: 700;
}
QLabel#StatusIdle {
    color: #cbd5e1;
    background: #182033;
    border: 1px solid #29344b;
    border-radius: 11px;
    padding: 4px 9px;
    font-weight: 700;
}
QLabel#StatusWarn {
    color: #fde68a;
    background: #3a2e10;
    border: 1px solid #66511a;
    border-radius: 11px;
    padding: 4px 9px;
    font-weight: 700;
}
QFrame#Card {
    background: #111827;
    border: 1px solid #253047;
    border-radius: 14px;
}
QFrame#HeroCard {
    background: #10182b;
    border: 1px solid #2b3853;
    border-radius: 16px;
}
QFrame#StatCard {
    background: #0e1627;
    border: 1px solid #24314a;
    border-radius: 12px;
}
QLabel#StatValue { font-size: 19pt; font-weight: 800; color: #f8fafc; }
QLabel#StatLabel { color: #8794aa; font-size: 9pt; }
QLineEdit, QPlainTextEdit, QTableWidget, QListWidget {
    background: #0c1323;
    border: 1px solid #2b3850;
    border-radius: 9px;
    color: #e5eaf3;
    selection-background-color: #3559d5;
    selection-color: white;
}
QLineEdit { padding: 9px 10px; }
QLineEdit:disabled { color: #617086; background: #0b1120; }
QPlainTextEdit { padding: 8px; font-family: Consolas, "Cascadia Mono", monospace; font-size: 9pt; }
QListWidget { padding: 7px; outline: 0; }
QListWidget::item {
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 7px;
    margin: 2px;
}
QListWidget::item:hover { background: #151f33; border-color: #26354f; }
QListWidget::item:selected { background: #1c2b50; border-color: #3f61d8; }
QPushButton {
    background: #182238;
    border: 1px solid #31405c;
    border-radius: 9px;
    color: #eaf0f8;
    padding: 8px 13px;
    font-weight: 600;
}
QPushButton:hover { background: #202d47; border-color: #475b7f; }
QPushButton:pressed { background: #131c30; }
QPushButton:disabled { color: #5e6a7b; background: #111827; border-color: #202b3e; }
QPushButton#Primary {
    background: #4f6ff2;
    border: 1px solid #6e88f8;
    color: white;
    font-size: 11pt;
    font-weight: 800;
    padding: 11px 18px;
}
QPushButton#Primary:hover { background: #5b7bf5; }
QPushButton#Danger { color: #fca5a5; }
QPushButton#Ghost { background: transparent; border-color: #29364d; }
QCheckBox { color: #d8deea; spacing: 8px; }
QCheckBox::indicator {
    width: 17px; height: 17px; border-radius: 5px;
    border: 1px solid #475569; background: #0c1323;
}
QCheckBox::indicator:checked { background: #4f6ff2; border-color: #7089f6; }
QProgressBar {
    background: #0c1323;
    border: 1px solid #2a3750;
    border-radius: 7px;
    min-height: 12px;
    max-height: 12px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk { background: #5f7bf3; border-radius: 6px; }
QHeaderView::section {
    background: #151e30;
    color: #9ba7bb;
    border: 0;
    border-bottom: 1px solid #2b3850;
    padding: 8px;
    font-weight: 700;
}
QTableWidget { gridline-color: #202c42; }
QStatusBar { color: #8794aa; background: #0b1020; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #2b3850; border-radius: 5px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


def app_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def find_exiftool() -> str | None:
    candidates = [app_dir() / 'exiftool.exe', app_dir() / 'exiftool(-k).exe', app_dir() / 'exiftool']
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil.which('exiftool') or shutil.which('exiftool.exe')


def run_exiftool(exe: str, args: List[str]) -> subprocess.CompletedProcess:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    return subprocess.run(
        [exe, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding='utf-8', errors='replace', creationflags=creationflags,
    )




def run_exiftool_binary(exe: str, args: List[str]) -> subprocess.CompletedProcess:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    return subprocess.run(
        [exe, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=creationflags,
    )


def extract_binary_metadata_block(exe: str, reference: str, tag: str) -> bytes:
    """Extract a raw metadata block with ExifTool. Empty output means the block is absent."""
    p = run_exiftool_binary(exe, ['-b', f'-{tag}', reference])
    if p.returncode != 0:
        err = (p.stderr or b'').decode('utf-8', 'replace').strip()
        raise RuntimeError(f'Could not extract {tag}: ' + (err or 'ExifTool failed'))
    return p.stdout or b''


def normalise_exif_for_png(raw: bytes) -> bytes:
    """Return a TIFF-form EXIF payload suitable for a PNG eXIf chunk."""
    if not raw:
        return b''
    # ExifTool may return a block with a JPEG/HEIF-style prefix. PNG eXIf stores
    # the TIFF payload beginning at II*\0 or MM\0*.
    candidates = []
    for sig in (b'II*\x00', b'MM\x00*'):
        i = raw.find(sig)
        if i >= 0:
            candidates.append(i)
    if not candidates:
        raise RuntimeError('EXIF block was found, but no TIFF header could be located.')
    return raw[min(candidates):]


def read_numeric_orientation(exe: str, path: str) -> int | None:
    """Read the file's own numeric EXIF orientation (1-8), if present."""
    p = run_exiftool(exe, ['-s3', '-n', '-Orientation', path])
    if p.returncode != 0:
        return None
    text = (p.stdout or '').strip().splitlines()
    if not text:
        return None
    try:
        value = int(float(text[0].strip()))
    except ValueError:
        return None
    return value if 1 <= value <= 8 else None


def read_actual_pixel_dimensions(exe: str, path: str) -> Tuple[int, int] | None:
    """Read the destination's real encoded pixel dimensions, preferring container structure over EXIF."""
    p = Path(path)
    try:
        if p.suffix.lower() == '.png':
            raw = p.read_bytes()[:24]
            if raw[:8] == b'\x89PNG\r\n\x1a\n' and raw[12:16] == b'IHDR':
                width, height = struct.unpack('>II', raw[16:24])
                if width > 0 and height > 0:
                    return width, height
    except OSError:
        pass

    # Generic fallback for JPEG/TIFF/WebP/HEIC/etc. Use grouped JSON and prefer
    # non-EXIF/XMP container values so stale metadata cannot override real geometry.
    try:
        meta = extract_metadata(exe, path)
        width_candidates = []
        height_candidates = []
        for key, value in meta.items():
            if ':' not in key:
                continue
            group, tag = key.split(':', 1)
            if group in {'IFD0', 'ExifIFD', 'InteropIFD', 'XMP-exif', 'XMP-tiff', 'XMP-exifEX'}:
                continue
            try:
                numeric = int(float(value))
            except (TypeError, ValueError):
                continue
            if numeric <= 0:
                continue
            if tag in {'ImageWidth', 'SourceImageWidth'}:
                width_candidates.append(numeric)
            elif tag in {'ImageHeight', 'ImageLength', 'SourceImageHeight'}:
                height_candidates.append(numeric)
        if width_candidates and height_candidates:
            return width_candidates[0], height_candidates[0]
    except Exception:
        pass
    return None


def _patch_tiff_ifd_scalar(data: bytearray, endian: str, ifd_offset: int, tag_values: Dict[int, int]) -> None:
    """Patch existing SHORT/LONG scalar tags in one TIFF IFD. Does not add new tags."""
    if ifd_offset <= 0 or ifd_offset + 2 > len(data):
        return
    try:
        count = struct.unpack_from(endian + 'H', data, ifd_offset)[0]
        for i in range(count):
            off = ifd_offset + 2 + i * 12
            if off + 12 > len(data):
                break
            tag, typ, n = struct.unpack_from(endian + 'HHI', data, off)
            if tag not in tag_values or n < 1:
                continue
            value = int(tag_values[tag])
            if typ == 3:  # SHORT
                if not (0 <= value <= 0xffff):
                    continue
                struct.pack_into(endian + 'H', data, off + 8, value)
                data[off + 10:off + 12] = b'\x00\x00'
            elif typ == 4:  # LONG
                if not (0 <= value <= 0xffffffff):
                    continue
                struct.pack_into(endian + 'I', data, off + 8, value)
    except (struct.error, IndexError):
        return


def patch_tiff_target_layout(exif_tiff: bytes, orientation: int, width: int, height: int) -> bytes:
    """Normalise EXIF pixel-layout tags to the destination without changing pixel data."""
    if not exif_tiff:
        return exif_tiff
    data = bytearray(exif_tiff)
    if data[:4] == b'II*\x00':
        endian = '<'
    elif data[:4] == b'MM\x00*':
        endian = '>'
    else:
        return exif_tiff
    try:
        ifd0 = struct.unpack_from(endian + 'I', data, 4)[0]
        # IFD0: ImageWidth (0x0100), ImageLength (0x0101), Orientation (0x0112)
        _patch_tiff_ifd_scalar(data, endian, ifd0, {
            0x0100: width,
            0x0101: height,
            0x0112: orientation,
        })

        exif_ifd = None
        interop_ifd = None
        if ifd0 > 0 and ifd0 + 2 <= len(data):
            count = struct.unpack_from(endian + 'H', data, ifd0)[0]
            for i in range(count):
                off = ifd0 + 2 + i * 12
                if off + 12 > len(data):
                    break
                tag, typ, n = struct.unpack_from(endian + 'HHI', data, off)
                if tag == 0x8769 and typ == 4 and n >= 1:  # ExifIFDPointer
                    exif_ifd = struct.unpack_from(endian + 'I', data, off + 8)[0]
                    break

        if exif_ifd:
            # ExifIFD: PixelXDimension / PixelYDimension
            _patch_tiff_ifd_scalar(data, endian, exif_ifd, {
                0xA002: width,
                0xA003: height,
            })
            # Locate InteroperabilityIFDPointer if present.
            if exif_ifd + 2 <= len(data):
                count = struct.unpack_from(endian + 'H', data, exif_ifd)[0]
                for i in range(count):
                    off = exif_ifd + 2 + i * 12
                    if off + 12 > len(data):
                        break
                    tag, typ, n = struct.unpack_from(endian + 'HHI', data, off)
                    if tag == 0xA005 and typ == 4 and n >= 1:
                        interop_ifd = struct.unpack_from(endian + 'I', data, off + 8)[0]
                        break

        if interop_ifd:
            # InteropIFD: RelatedImageWidth / RelatedImageLength.
            _patch_tiff_ifd_scalar(data, endian, interop_ifd, {
                0x1001: width,
                0x1002: height,
            })
        return bytes(data)
    except (struct.error, IndexError):
        return exif_tiff


def patch_tiff_orientation(exif_tiff: bytes, orientation: int) -> bytes:
    """Patch IFD0 Orientation in a TIFF-form EXIF block without touching image pixels."""
    if not exif_tiff or not (1 <= orientation <= 8):
        return exif_tiff
    data = bytearray(exif_tiff)
    if data[:4] == b'II*\x00':
        endian = '<'
    elif data[:4] == b'MM\x00*':
        endian = '>'
    else:
        return exif_tiff
    try:
        ifd0 = struct.unpack_from(endian + 'I', data, 4)[0]
        if ifd0 + 2 > len(data):
            return exif_tiff
        count = struct.unpack_from(endian + 'H', data, ifd0)[0]
        for i in range(count):
            off = ifd0 + 2 + i * 12
            if off + 12 > len(data):
                break
            tag, typ, n = struct.unpack_from(endian + 'HHI', data, off)
            if tag == 0x0112 and typ == 3 and n >= 1:
                struct.pack_into(endian + 'H', data, off + 8, orientation)
                return bytes(data)
    except (struct.error, IndexError):
        return exif_tiff
    return exif_tiff


def patch_xmp_target_layout(xmp: bytes, orientation: int, width: int, height: int) -> bytes:
    """Keep XMP orientation/dimension fields consistent with the destination image."""
    if not xmp:
        return xmp
    text = xmp.decode('utf-8', 'replace')
    replacements = {
        'tiff:Orientation': orientation,
        'tiff:ImageWidth': width,
        'tiff:ImageLength': height,
        'exif:PixelXDimension': width,
        'exif:PixelYDimension': height,
        'exifEX:PixelXDimension': width,
        'exifEX:PixelYDimension': height,
    }
    for name, value in replacements.items():
        escaped = re.escape(name)
        text = re.sub(rf'({escaped}\s*=\s*["\'])\d+(["\'])', rf'\g<1>{value}\2', text)
        text = re.sub(rf'(<{escaped}>\s*)\d+(\s*</{escaped}>)', rf'\g<1>{value}\2', text)
    return text.encode('utf-8')


def _xml_name_parts(name: str) -> Tuple[str, str]:
    if name.startswith('{') and '}' in name:
        uri, local = name[1:].split('}', 1)
        return uri, local
    return '', name


def _is_auxiliary_xmp_name(name: str) -> bool:
    uri, local = _xml_name_parts(name)
    uri_lower = uri.lower()
    if any(hint in uri_lower for hint in AUXILIARY_XMP_NAMESPACE_HINTS):
        return True
    # These local names are only considered auxiliary when they are the explicit
    # Apple auxiliary descriptors we have seen in HEIC XMP packets.
    return local in AUXILIARY_XMP_LOCAL_NAMES


def sanitize_cross_format_xmp(xmp: bytes) -> Tuple[bytes, List[str]]:
    """Remove XMP that describes HEIC-only auxiliary pixel streams.

    Cross-format PNG targets do not carry the source HEIC's depth/matte/gain-map
    auxiliary images.  Keeping metadata that claims those streams exist makes the
    output internally inconsistent.  This function removes only those auxiliary
    properties while preserving unrelated XMP.  If the packet becomes empty, it
    returns b'' so no pointless XMP chunk is injected.
    """
    if not xmp:
        return xmp, []

    original = xmp.lstrip(b'\xef\xbb\xbf')
    removed: List[str] = []
    try:
        root = ET.fromstring(original.decode('utf-8', 'replace'))

        # Remove auxiliary attributes first.
        for elem in root.iter():
            for attr in list(elem.attrib):
                if _is_auxiliary_xmp_name(attr):
                    _, local = _xml_name_parts(attr)
                    removed.append(local)
                    del elem.attrib[attr]

        # ElementTree has no parent pointer, so walk parents explicitly.
        for parent in list(root.iter()):
            for child in list(parent):
                if _is_auxiliary_xmp_name(child.tag):
                    _, local = _xml_name_parts(child.tag)
                    removed.append(local)
                    parent.remove(child)

        rdf_uri = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
        rdf_about = f'{{{rdf_uri}}}about'
        rdf_desc = f'{{{rdf_uri}}}Description'
        rdf_rdf = f'{{{rdf_uri}}}RDF'

        # Remove RDF Description nodes left with no meaningful properties.
        for parent in list(root.iter()):
            for child in list(parent):
                if child.tag != rdf_desc:
                    continue
                meaningful_attrs = [k for k in child.attrib if k != rdf_about]
                meaningful_text = (child.text or '').strip()
                if len(child) == 0 and not meaningful_attrs and not meaningful_text:
                    parent.remove(child)

        # If the RDF packet has no descriptions/properties left at all, omit XMP.
        rdf_nodes = [e for e in root.iter() if e.tag == rdf_rdf]
        if rdf_nodes and all(len(rdf) == 0 and not (rdf.text or '').strip() for rdf in rdf_nodes):
            return b'', sorted(set(removed))

        if not removed:
            return xmp, []

        # Keep familiar namespace prefixes where possible; URI identity is what matters.
        ET.register_namespace('x', 'adobe:ns:meta/')
        ET.register_namespace('rdf', rdf_uri)
        ET.register_namespace('xmp', 'http://ns.adobe.com/xap/1.0/')
        ET.register_namespace('tiff', 'http://ns.adobe.com/tiff/1.0/')
        ET.register_namespace('exif', 'http://ns.adobe.com/exif/1.0/')
        ET.register_namespace('exifEX', 'http://cipa.jp/exif/1.0/')
        ET.register_namespace('dc', 'http://purl.org/dc/elements/1.1/')
        return ET.tostring(root, encoding='utf-8'), sorted(set(removed))
    except Exception:
        # Conservative fallback for unusual-but-readable XMP.  Find prefixes whose
        # namespace URI is one of the known auxiliary namespaces and remove only
        # elements/attributes using those prefixes.
        text = original.decode('utf-8', 'replace')
        prefixes = []
        for m in re.finditer(r'xmlns:([A-Za-z_][\w.-]*)=["\']([^"\']+)["\']', text):
            prefix, uri = m.group(1), m.group(2)
            if any(hint in uri.lower() for hint in AUXILIARY_XMP_NAMESPACE_HINTS):
                prefixes.append(prefix)
        for prefix in prefixes:
            escaped = re.escape(prefix)
            # Paired and self-closing elements.
            text, n1 = re.subn(rf'<{escaped}:([A-Za-z_][\w.-]*)\b[^>]*>.*?</{escaped}:\1\s*>', '', text, flags=re.S)
            text, n2 = re.subn(rf'<{escaped}:([A-Za-z_][\w.-]*)\b[^>]*/\s*>', '', text, flags=re.S)
            # Attributes and namespace declaration.
            text, n3 = re.subn(rf'\s+{escaped}:[A-Za-z_][\w.-]*\s*=\s*["\'][^"\']*["\']', '', text)
            text = re.sub(rf'\s+xmlns:{escaped}\s*=\s*["\'][^"\']*["\']', '', text)
            if n1 or n2 or n3:
                removed.append(prefix)
        return (text.encode('utf-8') if removed else xmp), sorted(set(removed))


def xmp_contains_stale_auxiliary_refs(xmp: bytes) -> bool:
    if not xmp:
        return False
    lower = xmp.lower()
    markers = tuple(h.encode('ascii') for h in AUXILIARY_XMP_NAMESPACE_HINTS) + (
        b'auxiliaryimagetype', b'auxiliaryimagesubtype', b'portraiteffectsmatte',
        b'semanticsegmentationmatte', b'hdrgainmap', b'depthdata',
    )
    return any(marker in lower for marker in markers)


def patch_xmp_orientation(xmp: bytes, orientation: int) -> bytes:
    """Backward-compatible orientation-only helper."""
    if not xmp or not (1 <= orientation <= 8):
        return xmp
    text = xmp.decode('utf-8', 'replace')
    escaped = re.escape('tiff:Orientation')
    text = re.sub(rf'({escaped}\s*=\s*["\'])\d+(["\'])', rf'\g<1>{orientation}\2', text)
    text = re.sub(rf'(<{escaped}>\s*)\d+(\s*</{escaped}>)', rf'\g<1>{orientation}\2', text)
    return text.encode('utf-8')

def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = binascii.crc32(kind + payload) & 0xffffffff
    return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', crc)


def inject_png_metadata_payloads(path: str, exif_tiff: bytes = b'', xmp: bytes = b'', icc: bytes = b'') -> List[str]:
    """Inject raw EXIF/XMP/ICC payloads into a PNG without touching IDAT pixel bytes."""
    p = Path(path)
    raw = p.read_bytes()
    if raw[:8] != b'\x89PNG\r\n\x1a\n':
        raise RuntimeError('Destination is not a valid PNG.')

    chunks = []
    pos = 8
    while pos + 12 <= len(raw):
        length = int.from_bytes(raw[pos:pos+4], 'big')
        kind = raw[pos+4:pos+8]
        payload = raw[pos+8:pos+8+length]
        end = pos + 12 + length
        if end > len(raw):
            raise RuntimeError('PNG chunk table is truncated.')

        # Replace only the metadata payloads we own. If we inject an ICC profile,
        # remove an sRGB declaration too because PNG forbids iCCP and sRGB together.
        is_xmp = kind == b'iTXt' and payload.startswith(b'XML:com.adobe.xmp\x00')
        if kind not in (b'eXIf', b'iCCP') and not is_xmp and not (icc and kind == b'sRGB'):
            chunks.append((kind, payload))
        pos = end
        if kind == b'IEND':
            break

    if not chunks or chunks[0][0] != b'IHDR' or chunks[-1][0] != b'IEND':
        raise RuntimeError('PNG is missing required IHDR/IEND chunks.')

    st = p.stat()
    out = bytearray(raw[:8])
    inserted = []
    done = False
    for kind, payload in chunks:
        out += _png_chunk(kind, payload)
        if kind == b'IHDR' and not done:
            # iCCP must precede PLTE/IDAT. eXIf and XMP are also placed early so
            # readers that stop scanning after image data still see them.
            if icc:
                profile_name = b'ICC Profile'
                iccp = profile_name + b'\x00\x00' + zlib.compress(icc, 9)
                out += _png_chunk(b'iCCP', iccp)
                inserted.append('iCCP')
            if exif_tiff:
                out += _png_chunk(b'eXIf', exif_tiff)
                inserted.append('eXIf')
            if xmp:
                xmp = xmp.lstrip(b'\xef\xbb\xbf')
                itxt = b'XML:com.adobe.xmp\x00\x00\x00\x00\x00' + xmp
                out += _png_chunk(b'iTXt', itxt)
                inserted.append('iTXt/XMP')
            done = True

    p.write_bytes(out)
    try:
        os.utime(p, (st.st_atime, st.st_mtime))
    except OSError:
        pass
    return inserted


def normalise_existing_png_layout(path: str, orientation: int, width: int, height: int) -> bool:
    """Patch existing PNG eXIf/XMP layout tags in place while preserving every other chunk."""
    p = Path(path)
    raw = p.read_bytes()
    if raw[:8] != b'\x89PNG\r\n\x1a\n':
        return False
    pos = 8
    out = bytearray(raw[:8])
    changed = False
    while pos + 12 <= len(raw):
        length = int.from_bytes(raw[pos:pos+4], 'big')
        kind = raw[pos+4:pos+8]
        payload = raw[pos+8:pos+8+length]
        end = pos + 12 + length
        if end > len(raw):
            raise RuntimeError('PNG chunk table is truncated.')

        new_payload = payload
        if kind == b'eXIf':
            new_payload = patch_tiff_target_layout(payload, orientation, width, height)
        elif kind == b'iTXt' and payload.startswith(b'XML:com.adobe.xmp\x00'):
            # Locate the five NUL-delimited iTXt header fields, then patch XML only.
            try:
                # XMP chunks written by this app use: keyword\0 compflag compmethod lang\0 translated\0 text
                prefix = b'XML:com.adobe.xmp\x00\x00\x00\x00\x00'
                if payload.startswith(prefix):
                    xml = payload[len(prefix):]
                    new_payload = prefix + patch_xmp_target_layout(xml, orientation, width, height)
            except Exception:
                new_payload = payload

        if new_payload != payload:
            changed = True
        out += _png_chunk(kind, new_payload)
        pos = end
        if kind == b'IEND':
            break

    if changed:
        st = p.stat()
        p.write_bytes(out)
        try:
            os.utime(p, (st.st_atime, st.st_mtime))
        except OSError:
            pass
    return changed


def validate_no_stale_auxiliary_xmp(exe: str, path: str) -> None:
    """Fail if a cross-format output still claims HEIC-only auxiliary streams exist."""
    raw_xmp = extract_binary_metadata_block(exe, path, 'XMP')
    if xmp_contains_stale_auxiliary_refs(raw_xmp):
        raise RuntimeError(
            'Auxiliary XMP verification failed: output still contains HEIC-only '
            'depth/matte/gain-map descriptors without their source auxiliary streams.'
        )


def validate_target_layout_metadata(exe: str, path: str, dimensions: Tuple[int, int] | None, orientation: int) -> None:
    """Fail if embedded layout metadata still contradicts the destination's actual pixels."""
    actual_orientation = read_numeric_orientation(exe, path)
    if actual_orientation is not None and actual_orientation != orientation:
        raise RuntimeError(
            f'Orientation verification failed: metadata says {actual_orientation}, expected {orientation}.'
        )
    if not dimensions:
        return
    width, height = dimensions
    meta = extract_metadata(exe, path)
    expected_by_key = {
        'IFD0:ImageWidth': width,
        'IFD0:ImageHeight': height,
        'IFD0:ImageLength': height,
        'ExifIFD:ExifImageWidth': width,
        'ExifIFD:ExifImageHeight': height,
        'ExifIFD:PixelXDimension': width,
        'ExifIFD:PixelYDimension': height,
        'InteropIFD:RelatedImageWidth': width,
        'InteropIFD:RelatedImageHeight': height,
        'InteropIFD:RelatedImageLength': height,
        'XMP-tiff:ImageWidth': width,
        'XMP-tiff:ImageLength': height,
        'XMP-exif:PixelXDimension': width,
        'XMP-exif:PixelYDimension': height,
        'XMP-exifEX:PixelXDimension': width,
        'XMP-exifEX:PixelYDimension': height,
    }
    bad = []
    for key, expected in expected_by_key.items():
        if key not in meta:
            continue
        value = meta[key]
        try:
            numeric = int(float(value))
        except (TypeError, ValueError):
            continue
        if numeric != expected:
            bad.append(f'{key}={numeric} (expected {expected})')
    if bad:
        raise RuntimeError('Target dimension metadata verification failed: ' + '; '.join(bad))


def clone_metadata_to_png_binary(exe: str, reference: str, dest: str, target_orientation: int = 1,
                                 target_dimensions: Tuple[int, int] | None = None) -> Tuple[List[str], str]:
    """Reliable PNG path: transplant payloads while normalising target-specific pixel-layout metadata."""
    raw_exif = extract_binary_metadata_block(exe, reference, 'EXIF')
    raw_xmp = extract_binary_metadata_block(exe, reference, 'XMP')
    raw_icc = extract_binary_metadata_block(exe, reference, 'ICC_Profile')

    # The PNG does not contain HEIC auxiliary image streams (depth/mattes/HDR gain maps),
    # so remove XMP properties that would falsely claim those streams are present.
    raw_xmp, removed_aux_xmp = sanitize_cross_format_xmp(raw_xmp)

    dims = target_dimensions or read_actual_pixel_dimensions(exe, dest)
    if not dims:
        raise RuntimeError('Could not determine the PNG target dimensions safely.')
    target_width, target_height = dims

    exif_tiff = normalise_exif_for_png(raw_exif) if raw_exif else b''
    exif_tiff = patch_tiff_target_layout(exif_tiff, target_orientation, target_width, target_height)
    raw_xmp = patch_xmp_target_layout(raw_xmp, target_orientation, target_width, target_height)
    if not any((exif_tiff, raw_xmp, raw_icc)):
        raise RuntimeError('Reference contains no raw EXIF, XMP or ICC payload that can be transplanted into PNG.')

    inserted = inject_png_metadata_payloads(dest, exif_tiff=exif_tiff, xmp=raw_xmp, icc=raw_icc)
    aux_detail = ''
    if removed_aux_xmp:
        aux_detail = f' Removed {len(removed_aux_xmp)} stale HEIC auxiliary XMP field(s): {", ".join(removed_aux_xmp)}.'
    detail = (f'Injected {", ".join(inserted)} directly into PNG; pixel IDAT bytes were not changed. '
              f'Target layout normalised to {target_width}×{target_height}, orientation {target_orientation}.'
              f'{aux_detail}')
    return inserted, detail

def extract_metadata(exe: str, path: str) -> Dict[str, object]:
    p = run_exiftool(exe, ['-j', '-G1', '-s', '-a', '-charset', 'filename=utf8', path])
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip() or 'ExifTool read failed')
    data = json.loads(p.stdout)
    return data[0] if data else {}


def portable_metadata(meta: Dict[str, object]) -> Dict[str, object]:
    result = {}
    for key, value in meta.items():
        if key == 'SourceFile' or key in IGNORE_KEYS:
            continue
        if key.startswith(IGNORE_GROUP_PREFIXES):
            continue
        result[key] = value
    return result


def embedded_payload_metadata(meta: Dict[str, object]) -> Dict[str, object]:
    """Return non-structural metadata actually embedded in the image container."""
    result = {}
    for key, value in portable_metadata(meta).items():
        tag = key.split(':', 1)[-1]
        if tag in CORE_STRUCTURAL_TAGS:
            continue
        result[key] = value
    return result


def _is_auxiliary_metadata_key(key: str) -> bool:
    lower = key.lower()
    tag = key.split(':', 1)[-1]
    if tag in AUXILIARY_XMP_LOCAL_NAMES:
        return True
    return any(hint in lower for hint in AUXILIARY_XMP_NAMESPACE_HINTS)


def _canonical_metadata(meta: Dict[str, object]) -> Dict[str, List[object]]:
    """Group-independent view used when metadata is mapped between file formats."""
    out: Dict[str, List[object]] = {}
    for key, value in portable_metadata(meta).items():
        tag = key.split(':', 1)[-1]
        if tag in TARGET_LAYOUT_TAGS or _is_auxiliary_metadata_key(key):
            continue
        out.setdefault(tag, []).append(value)
    return out


def metadata_diff(reference: Dict[str, object], target: Dict[str, object], cross_format: bool = False) -> Tuple[List[Tuple], List[Tuple], List[Tuple]]:
    if not cross_format:
        r = portable_metadata(reference)
        t = portable_metadata(target)
        missing, changed, extra = [], [], []
        for key in sorted(r):
            if key not in t:
                missing.append((key, r[key], None))
            elif t[key] != r[key]:
                changed.append((key, r[key], t[key]))
        for key in sorted(t):
            if key not in r:
                extra.append((key, None, t[key]))
        return missing, changed, extra

    # HEIC/JPEG/PNG/etc. often store the same logical tag in different metadata
    # groups.  Compare by tag name when crossing formats so a successfully
    # mapped Make/CreateDate/etc. is not incorrectly reported as missing merely
    # because its group changed.
    r = _canonical_metadata(reference)
    t = _canonical_metadata(target)
    missing, changed, extra = [], [], []
    for tag in sorted(r):
        if tag not in t:
            missing.append((tag, r[tag], None))
        elif not any(rv == tv for rv in r[tag] for tv in t[tag]):
            changed.append((tag, r[tag], t[tag]))
    for tag in sorted(t):
        if tag not in r:
            extra.append((tag, None, t[tag]))
    return missing, changed, extra


def clone_metadata(exe: str, reference: str, dest: str) -> Tuple[subprocess.CompletedProcess, str]:
    """Copy metadata, with an explicit compatibility bridge for cross-format writes."""
    ref_ext = Path(reference).suffix.lower()
    dst_ext = Path(dest).suffix.lower()
    same_format = ref_ext == dst_ext or {ref_ext, dst_ext} <= {'.jpg', '.jpeg'}

    if same_format:
        mode = 'preserve-groups'
        args = [
            '-m', '-P', '-TagsFromFile', reference, '-all:all',
            '-overwrite_original', dest
        ]
        return run_exiftool(exe, args), mode

    # Cross-format mode is deliberately more explicit than a bare "-all".
    # First ask ExifTool for its normal preferred-group mapping, then also copy
    # native EXIF/XMP/ICC payloads and bridge common EXIF/HEIC values into XMP.
    # PNG, JPEG, HEIC, TIFF, WebP and AVIF do not expose identical metadata
    # containers, so this gives compatible tags more than one valid destination.
    mode = 'mapped-cross-format-v2'
    bridge = [
        # Preserve metadata blocks that the destination supports.
        '-EXIF:All', '-XMP:All', '-ICC_Profile',

        # Official EXIF -> XMP style group mappings (mirrors ExifTool arg-file logic).
        '-XMP-exif:all<EXIF:all',
        '-XMP-exifEX:all<EXIF:all',
        '-XMP-tiff:all<EXIF:all',
        '-XMP:all<GPS:all',

        # Core descriptive / origin fields.
        '-XMP-dc:Description<ImageDescription',
        '-XMP-dc:Description<Description',
        '-XMP-dc:Title<Title',
        '-XMP-dc:Creator<Artist',
        '-XMP-dc:Rights<Copyright',
        '-XMP-xmp:CreatorTool<Software',
        '-XMP-tiff:Make<Make',
        '-XMP-tiff:Model<Model',
        '-XMP-tiff:Orientation<Orientation',

        # Dates.  Ungrouped source names allow values exposed from HEIC/QuickTime
        # or EXIF to land in standard XMP destinations when possible.
        '-XMP-xmp:CreateDate<CreateDate',
        '-XMP-xmp:ModifyDate<ModifyDate',
        '-XMP-photoshop:DateCreated<DateTimeOriginal',
        '-XMP-exif:DateTimeOriginal<DateTimeOriginal',
        '-XMP-exif:DateTimeDigitized<CreateDate',

        # Common camera/exposure values.
        '-XMP-exif:ExposureTime<ExposureTime',
        '-XMP-exif:FNumber<FNumber',
        '-XMP-exif:ExposureProgram<ExposureProgram',
        '-XMP-exif:PhotographicSensitivity<ISO',
        '-XMP-exif:ShutterSpeedValue<ShutterSpeedValue',
        '-XMP-exif:ApertureValue<ApertureValue',
        '-XMP-exif:BrightnessValue<BrightnessValue',
        '-XMP-exif:ExposureBiasValue<ExposureCompensation',
        '-XMP-exif:MeteringMode<MeteringMode',
        '-XMP-exif:Flash<Flash',
        '-XMP-exif:FocalLength<FocalLength',
        '-XMP-exif:ColorSpace<ColorSpace',
        '-XMP-exif:ExifVersion<ExifVersion',
        '-XMP-exif:LensModel<LensModel',
        '-XMP-exifEX:LensMake<LensMake',

        # GPS composites are the safest cross-container representation.
        '-XMP:GPSLatitude<Composite:GPSLatitude',
        '-XMP:GPSLongitude<Composite:GPSLongitude',
        '-XMP:GPSAltitude<GPSAltitude',
        '-XMP:GPSDateTime<Composite:GPSDateTime',
    ]
    args = [
        '-m', '-P', '-TagsFromFile', reference,
        '-all', *bridge,
        '-overwrite_original', dest
    ]
    return run_exiftool(exe, args), mode


def png_metadata_chunks(path: str) -> List[str]:
    """Return PNG ancillary chunk names.  Does not decode or inspect image pixels."""
    p = Path(path)
    if p.suffix.lower() != '.png':
        return []
    try:
        raw = p.read_bytes()
        if raw[:8] != b'\x89PNG\r\n\x1a\n':
            return []
        pos = 8
        chunks = []
        core = {'IHDR', 'IDAT', 'IEND', 'PLTE', 'tRNS'}
        while pos + 12 <= len(raw):
            length = int.from_bytes(raw[pos:pos+4], 'big')
            ctype = raw[pos+4:pos+8].decode('latin1', errors='replace')
            if ctype not in core:
                chunks.append(ctype)
            pos += 12 + length
            if ctype == 'IEND':
                break
        return chunks
    except Exception:
        return []




def is_video_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTS


def media_kind(path: str | Path) -> str:
    return 'video' if is_video_path(path) else 'image'


def canonical_values_for_tags(meta: Dict[str, object], names: set[str]) -> Dict[str, List[object]]:
    out: Dict[str, List[object]] = {}
    for key, value in meta.items():
        if key == 'SourceFile':
            continue
        tag = key.split(':', 1)[-1]
        if tag in names:
            out.setdefault(tag, []).append(value)
    return out


def video_structural_snapshot(meta: Dict[str, object]) -> Dict[str, List[object]]:
    return canonical_values_for_tags(meta, VIDEO_STRUCTURAL_TAGS)


def compare_video_structure(before: Dict[str, object], after: Dict[str, object]) -> List[str]:
    b = video_structural_snapshot(before)
    a = video_structural_snapshot(after)
    changed = []
    for tag in sorted(set(b) | set(a)):
        if b.get(tag) != a.get(tag):
            changed.append(f'{tag}: {b.get(tag)} -> {a.get(tag)}')
    return changed


def quicktime_mdat_digest(path: str) -> Tuple[str, int, int]:
    """Hash top-level mdat payload bytes without decoding media samples."""
    import hashlib
    h = hashlib.sha256()
    count = 0
    total = 0
    file_size = os.path.getsize(path)
    with open(path, 'rb') as f:
        pos = 0
        while pos + 8 <= file_size:
            f.seek(pos)
            header = f.read(8)
            if len(header) < 8:
                break
            size32, kind = struct.unpack('>I4s', header)
            header_size = 8
            if size32 == 1:
                ext = f.read(8)
                if len(ext) < 8:
                    break
                size = struct.unpack('>Q', ext)[0]
                header_size = 16
            elif size32 == 0:
                size = file_size - pos
            else:
                size = size32
            if size < header_size or pos + size > file_size:
                break
            if kind == b'mdat':
                count += 1
                remaining = size - header_size
                total += remaining
                f.seek(pos + header_size)
                while remaining:
                    chunk = f.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise RuntimeError('Unexpected end of file while hashing mdat payload.')
                    h.update(chunk)
                    remaining -= len(chunk)
            pos += size
    if not count:
        raise RuntimeError('No top-level mdat media-data box found in video.')
    return h.hexdigest(), count, total


def _first_local_tag_value(meta: Dict[str, object], tag_name: str) -> object | None:
    """Return the first value for a tag regardless of its ExifTool family-1 group."""
    for key, value in meta.items():
        if key == tag_name or key.rsplit(':', 1)[-1] == tag_name:
            return value
    return None


def video_reference_values(meta: Dict[str, object]) -> Dict[str, object]:
    """Reference-owned video metadata that v2.2 intentionally transfers."""
    vals: Dict[str, object] = {}
    preferred = {
        'Make': ('Keys:Make',),
        'Model': ('Keys:Model',),
        'Software': ('Keys:Software',),
        'GPSCoordinates': ('Keys:GPSCoordinates',),
        'LocationAccuracyHorizontal': ('Keys:LocationAccuracyHorizontal',),
        'FullFrameRatePlaybackIntent': ('Keys:FullFrameRatePlaybackIntent',),
    }
    for logical, keys in preferred.items():
        for key in keys:
            if key in meta:
                vals[logical] = meta[key]
                break
    return vals


def extract_video_reference_raw_values(exe: str, reference: str) -> Dict[str, object]:
    """Read raw reference values whose QuickTime storage representation matters."""
    p = run_exiftool(exe, [
        '-j', '-G1', '-s', '-a', '-n',
        '-Keys:FullFrameRatePlaybackIntent',
        reference,
    ])
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip() or 'Could not read reference video metadata.')
    data = json.loads(p.stdout)
    return data[0] if data else {}


def extract_video_temporal_metadata(exe: str, path: str) -> Dict[str, object]:
    """Snapshot target-owned embedded timestamps in stable/raw form.

    QuickTime movie/track/media times are left in place during repair.  Date fields
    inside metadata groups that we clear (Keys/XMP/UserData/ItemList) are restored
    from this snapshot rather than being copied from the reference.
    """
    p = run_exiftool(exe, [
        '-j', '-G1', '-s', '-a', '-n', '-api', 'QuickTimeUTC=1',
        '-time:all', path,
    ])
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip() or 'Could not read target video timestamps.')
    data = json.loads(p.stdout)
    meta = data[0] if data else {}
    out: Dict[str, object] = {}
    for key, value in meta.items():
        if key == 'SourceFile' or ':' not in key:
            continue
        group, local = key.split(':', 1)
        if group in {'File', 'System', 'ExifTool', 'Composite'}:
            continue
        # Time:All can include timezone/offset helpers. Preserve normal date/time tags
        # and any explicit target CreationDate key, but not derived Composite values.
        if (local in VIDEO_TEMPORAL_LOCAL_TAGS or
                local.endswith('CreateDate') or local.endswith('ModifyDate') or
                local.endswith('DateTimeOriginal') or local.endswith('DateTimeDigitized')):
            out[key] = value
    return out


def _restore_video_metadata_group_timestamps(exe: str, dest: str, snapshot: Dict[str, object]) -> subprocess.CompletedProcess | None:
    """Restore target date tags from metadata groups that the stripping pass clears."""
    args = ['-m', '-P', '-api', 'QuickTimeUTC=1']
    writable_prefixes = ('Keys:', 'UserData:', 'ItemList:', 'XMP-')
    for key, value in snapshot.items():
        if not key.startswith(writable_prefixes):
            continue
        if isinstance(value, (dict, list, tuple)):
            continue
        args.append(_safe_exif_assignment(key, value))
    if len(args) == 4:
        return None
    args.extend(['-overwrite_original', dest])
    return run_exiftool(exe, args)


def video_temporal_diff(before: Dict[str, object], after: Dict[str, object]) -> List[Tuple[str, object, object]]:
    """Return target-owned timestamps that changed or appeared/disappeared."""
    diffs: List[Tuple[str, object, object]] = []
    keys = sorted(set(before) | set(after))
    for key in keys:
        b = before.get(key, None)
        a = after.get(key, None)
        if b != a:
            diffs.append((key, b, a))
    return diffs


def _safe_exif_assignment(tag: str, value: object, raw_numeric: bool = False) -> str:
    # Arguments are passed directly to subprocess (no shell), so spaces/time-zone
    # characters are safe here. The # form requests raw numeric conversion where
    # ExifTool supports it; v2.2 additionally normalises the Apple playback key's
    # actual QuickTime data atom type after ExifTool writes it.
    suffix = '#' if raw_numeric else ''
    return f'-{tag}{suffix}={value}'


def _find_all_bytes(blob: bytes | bytearray, needle: bytes):
    pos = 0
    while True:
        pos = blob.find(needle, pos)
        if pos < 0:
            return
        yield pos
        pos += 1


def _parse_quicktime_keys_box(blob: bytes | bytearray, type_pos: int):
    """Parse a normal-size QuickTime keys box whose type starts at type_pos."""
    if type_pos < 4:
        return None
    size = struct.unpack('>I', blob[type_pos - 4:type_pos])[0]
    start = type_pos - 4
    if size < 16 or start + size > len(blob):
        return None
    payload = blob[type_pos + 4:start + size]
    if len(payload) < 8:
        return None
    count = struct.unpack('>I', payload[4:8])[0]
    p = 8
    names = []
    try:
        for _ in range(count):
            entry_size = struct.unpack('>I', payload[p:p + 4])[0]
            if entry_size < 8 or p + entry_size > len(payload):
                return None
            names.append(payload[p + 8:p + entry_size].decode('utf-8', 'strict'))
            p += entry_size
    except Exception:
        return None
    return start, size, names


def normalise_quicktime_integer_key(path: str, key_name: str, value: int) -> int:
    """Force a QuickTime mdta key to native signed-integer data type 21 in place.

    ExifTool currently creates this Apple key as UTF-8 when it is added to some
    MP4 targets.  The iPhone reference stores it as integer type 21.  We keep the
    existing atom sizes and encode the integer into the existing payload width,
    so chunk offsets and the media payload do not move.
    """
    p = Path(path)
    blob = bytearray(p.read_bytes())
    patched = 0
    for keys_type_pos in _find_all_bytes(blob, b'keys'):
        parsed = _parse_quicktime_keys_box(blob, keys_type_pos)
        if not parsed:
            continue
        keys_start, keys_size, names = parsed
        if key_name not in names:
            continue
        key_index = names.index(key_name) + 1
        # In Apple's mdta layout the corresponding ilst follows this keys box.
        ilst_type_pos = blob.find(b'ilst', keys_start + keys_size, min(len(blob), keys_start + keys_size + 8192))
        if ilst_type_pos < 4:
            continue
        ilst_size = struct.unpack('>I', blob[ilst_type_pos - 4:ilst_type_pos])[0]
        ilst_start = ilst_type_pos - 4
        ilst_end = ilst_start + ilst_size
        if ilst_size < 8 or ilst_end > len(blob):
            continue
        pos = ilst_type_pos + 4
        while pos + 8 <= ilst_end:
            item_size = struct.unpack('>I', blob[pos:pos + 4])[0]
            item_index = struct.unpack('>I', blob[pos + 4:pos + 8])[0]
            if item_size < 8 or pos + item_size > ilst_end:
                break
            if item_index == key_index:
                data_type_pos = blob.find(b'data', pos + 8, pos + item_size)
                if data_type_pos >= 4:
                    data_size = struct.unpack('>I', blob[data_type_pos - 4:data_type_pos])[0]
                    payload_start = data_type_pos + 12
                    payload_end = data_type_pos - 4 + data_size
                    width = payload_end - payload_start
                    if width <= 0 or width > 8:
                        raise RuntimeError(f'Unexpected payload width {width} for {key_name}.')
                    # data atom type 21 = signed integer. Keep locale and all box sizes.
                    blob[data_type_pos + 4:data_type_pos + 8] = struct.pack('>I', 21)
                    try:
                        encoded = int(value).to_bytes(width, 'big', signed=True)
                    except OverflowError as exc:
                        raise RuntimeError(f'Value {value} does not fit {width}-byte QuickTime integer payload.') from exc
                    blob[payload_start:payload_end] = encoded
                    patched += 1
            pos += item_size
    if patched:
        p.write_bytes(blob)
    return patched


def inspect_quicktime_integer_key(path: str, key_name: str) -> List[Tuple[int, int]]:
    """Return (QuickTime data type, integer value) for every matching mdta key."""
    blob = Path(path).read_bytes()
    found: List[Tuple[int, int]] = []
    for keys_type_pos in _find_all_bytes(blob, b'keys'):
        parsed = _parse_quicktime_keys_box(blob, keys_type_pos)
        if not parsed:
            continue
        keys_start, keys_size, names = parsed
        if key_name not in names:
            continue
        key_index = names.index(key_name) + 1
        ilst_type_pos = blob.find(b'ilst', keys_start + keys_size, min(len(blob), keys_start + keys_size + 8192))
        if ilst_type_pos < 4:
            continue
        ilst_size = struct.unpack('>I', blob[ilst_type_pos - 4:ilst_type_pos])[0]
        ilst_start = ilst_type_pos - 4
        ilst_end = ilst_start + ilst_size
        pos = ilst_type_pos + 4
        while pos + 8 <= ilst_end:
            item_size = struct.unpack('>I', blob[pos:pos + 4])[0]
            item_index = struct.unpack('>I', blob[pos + 4:pos + 8])[0]
            if item_size < 8 or pos + item_size > ilst_end:
                break
            if item_index == key_index:
                data_type_pos = blob.find(b'data', pos + 8, pos + item_size)
                if data_type_pos >= 4:
                    data_size = struct.unpack('>I', blob[data_type_pos - 4:data_type_pos])[0]
                    dtype = struct.unpack('>I', blob[data_type_pos + 4:data_type_pos + 8])[0]
                    payload_start = data_type_pos + 12
                    payload_end = data_type_pos - 4 + data_size
                    raw = blob[payload_start:payload_end]
                    if raw:
                        found.append((dtype, int.from_bytes(raw, 'big', signed=True)))
            pos += item_size
    return found


def video_metadata_diff(reference: Dict[str, object], target: Dict[str, object]) -> Tuple[List[Tuple], List[Tuple], List[Tuple]]:
    # v2.2 deliberately excludes date/time fields: those belong to the target and
    # are verified separately with video_temporal_diff().
    r = video_reference_values(reference)
    t = video_reference_values(target)
    missing, changed = [], []
    for tag in sorted(r):
        if tag not in t:
            missing.append((tag, r[tag], None))
        elif t[tag] != r[tag]:
            changed.append((tag, r[tag], t[tag]))
    return missing, changed, []


def clone_video_metadata(exe: str, reference: str, dest: str) -> Tuple[subprocess.CompletedProcess, str]:
    """Copy iPhone-style metadata while preserving the target's own timestamps/media facts."""
    raw_ref = extract_video_reference_raw_values(exe, reference)
    ffri = _first_local_tag_value(raw_ref, 'FullFrameRatePlaybackIntent')
    target_times = extract_video_temporal_metadata(exe, dest)

    # Strip writable user-facing metadata.  Crucially, do NOT delete QuickTime
    # movie/track/media date atoms: those are target-owned and stay untouched.
    clear_args = [
        '-m', '-P',
        '-Keys:All=', '-UserData:All=', '-ItemList:All=', '-XMP:All=',
        '-overwrite_original', dest,
    ]
    cleared = run_exiftool(exe, clear_args)
    if cleared.returncode != 0:
        return cleared, 'video-clear-failed'

    # Copy only reference-owned provenance/device fields. No reference dates.
    args = [
        '-m', '-P', '-TagsFromFile', reference,
        '-Keys:Make', '-Keys:Model', '-Keys:Software',
        '-Keys:GPSCoordinates', '-Keys:LocationAccuracyHorizontal',
        '-overwrite_original', dest,
    ]
    copied = run_exiftool(exe, args)
    if copied.returncode != 0:
        combined_out = '\n'.join(x for x in (
            (cleared.stdout or '').strip(), (cleared.stderr or '').strip(),
            (copied.stdout or '').strip(), (copied.stderr or '').strip(),
        ) if x)
        copied.stdout = combined_out
        return copied, 'video-copy-failed'

    # Restore target-owned date fields from groups that the stripping pass cleared.
    restored = _restore_video_metadata_group_timestamps(exe, dest, target_times)
    if restored is not None and restored.returncode != 0:
        combined_out = '\n'.join(x for x in (
            (cleared.stdout or '').strip(), (cleared.stderr or '').strip(),
            (copied.stdout or '').strip(), (copied.stderr or '').strip(),
            (restored.stdout or '').strip(), (restored.stderr or '').strip(),
        ) if x)
        restored.stdout = combined_out
        return restored, 'video-target-time-restore-failed'

    # Add the Apple playback-intent key, then patch its raw QuickTime data atom
    # to the same native integer representation used by iPhone files.
    exact = None
    if ffri is not None:
        exact = run_exiftool(exe, [
            '-m', '-P', _safe_exif_assignment('Keys:FullFrameRatePlaybackIntent', ffri, raw_numeric=True),
            '-overwrite_original', dest,
        ])
        if exact.returncode != 0:
            combined_out = '\n'.join(x for x in (
                (cleared.stdout or '').strip(), (copied.stdout or '').strip(),
                (exact.stdout or '').strip(), (exact.stderr or '').strip(),
            ) if x)
            exact.stdout = combined_out
            return exact, 'video-playback-intent-write-failed'
        try:
            expected = int(ffri)
            count = normalise_quicktime_integer_key(
                dest, 'com.apple.quicktime.full-frame-rate-playback-intent', expected
            )
            if count <= 0:
                raise RuntimeError('Playback-intent QuickTime key was not found after ExifTool write.')
            representations = inspect_quicktime_integer_key(
                dest, 'com.apple.quicktime.full-frame-rate-playback-intent'
            )
            if not representations or any(dtype != 21 or value != expected for dtype, value in representations):
                raise RuntimeError(f'Playback-intent native integer verification failed: {representations!r}')
        except Exception as exc:
            exact.returncode = 1
            exact.stderr = (exact.stderr or '') + f'\n{exc}'
            return exact, 'video-playback-intent-native-type-failed'

    final_proc = exact or restored or copied
    combined_out = '\n'.join(x for x in (
        (cleared.stdout or '').strip(), (cleared.stderr or '').strip(),
        (copied.stdout or '').strip(), (copied.stderr or '').strip(),
        ((restored.stdout or '').strip() if restored else ''),
        ((restored.stderr or '').strip() if restored else ''),
        ((exact.stdout or '').strip() if exact else ''),
        ((exact.stderr or '').strip() if exact else ''),
    ) if x)
    final_proc.stdout = combined_out
    return final_proc, 'video-quicktime-safe-v2.2-target-times'


def capture_filesystem_times(path: str) -> Dict[str, object]:
    """Capture target filesystem times; includes Windows creation time when available."""
    st = os.stat(path)
    snap: Dict[str, object] = {'atime_ns': st.st_atime_ns, 'mtime_ns': st.st_mtime_ns}
    if os.name != 'nt':
        return snap
    try:
        import ctypes
        from ctypes import wintypes
        class FILETIME(ctypes.Structure):
            _fields_ = [('dwLowDateTime', wintypes.DWORD), ('dwHighDateTime', wintypes.DWORD)]
        CreateFileW = ctypes.windll.kernel32.CreateFileW
        CreateFileW.restype = wintypes.HANDLE
        GetFileTime = ctypes.windll.kernel32.GetFileTime
        CloseHandle = ctypes.windll.kernel32.CloseHandle
        FILE_READ_ATTRIBUTES = 0x0080
        FILE_SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
        OPEN_EXISTING = 3
        FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
        handle = CreateFileW(str(path), FILE_READ_ATTRIBUTES, FILE_SHARE_ALL, None, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None)
        if handle == wintypes.HANDLE(-1).value:
            return snap
        try:
            c, a, w = FILETIME(), FILETIME(), FILETIME()
            if GetFileTime(handle, ctypes.byref(c), ctypes.byref(a), ctypes.byref(w)):
                snap['win_filetimes'] = (
                    (c.dwHighDateTime << 32) | c.dwLowDateTime,
                    (a.dwHighDateTime << 32) | a.dwLowDateTime,
                    (w.dwHighDateTime << 32) | w.dwLowDateTime,
                )
        finally:
            CloseHandle(handle)
    except Exception:
        pass
    return snap


def restore_filesystem_times(path: str, snap: Dict[str, object]) -> None:
    """Restore original target file times after metadata writing."""
    try:
        os.utime(path, ns=(int(snap['atime_ns']), int(snap['mtime_ns'])))
    except Exception:
        pass
    if os.name != 'nt' or 'win_filetimes' not in snap:
        return
    try:
        import ctypes
        from ctypes import wintypes
        class FILETIME(ctypes.Structure):
            _fields_ = [('dwLowDateTime', wintypes.DWORD), ('dwHighDateTime', wintypes.DWORD)]
        def ft(v: int):
            return FILETIME(v & 0xffffffff, (v >> 32) & 0xffffffff)
        CreateFileW = ctypes.windll.kernel32.CreateFileW
        CreateFileW.restype = wintypes.HANDLE
        SetFileTime = ctypes.windll.kernel32.SetFileTime
        CloseHandle = ctypes.windll.kernel32.CloseHandle
        FILE_WRITE_ATTRIBUTES = 0x0100
        FILE_SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
        OPEN_EXISTING = 3
        FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
        handle = CreateFileW(str(path), FILE_WRITE_ATTRIBUTES, FILE_SHARE_ALL, None, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None)
        if handle == wintypes.HANDLE(-1).value:
            return
        try:
            cv, av, wv = snap['win_filetimes']
            c, a, w = ft(int(cv)), ft(int(av)), ft(int(wv))
            SetFileTime(handle, ctypes.byref(c), ctypes.byref(a), ctypes.byref(w))
        finally:
            CloseHandle(handle)
    except Exception:
        pass

def unique_output_path(folder: Path, source: Path) -> Path:
    candidate = folder / source.name
    if not candidate.exists():
        return candidate
    n = 2
    while True:
        candidate = folder / f'{source.stem}_repaired_{n}{source.suffix}'
        if not candidate.exists():
            return candidate
        n += 1


def human_size(size: int) -> str:
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024
    return f'{value:.1f} GB'


def load_preview(path: str, target_size: QSize) -> QPixmap | None:
    pixmap = QPixmap(path)
    if pixmap.isNull():
        return None
    return pixmap.scaled(target_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def make_app_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor('#4f6ff2'))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 15, 15)
    p.setPen(QColor('white'))
    font = QFont('Segoe UI', 25, QFont.Bold)
    p.setFont(font)
    p.drawText(pix.rect(), Qt.AlignCenter, 'M')
    p.end()
    return QIcon(pix)


class DropList(QListWidget):
    files_dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DropOnly)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setIconSize(QSize(52, 52))
        self.setMinimumHeight(270)

    def dragEnterEvent(self, event: QDragEnterEvent):
        event.acceptProposedAction() if event.mimeData().hasUrls() else event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction() if event.mimeData().hasUrls() else event.ignore()

    def dropEvent(self, event: QDropEvent):
        paths = []
        for url in event.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.is_file() and p.suffix.lower() in MEDIA_EXTS:
                paths.append(str(p))
            elif p.is_dir():
                paths.extend(str(x) for x in p.iterdir() if x.is_file() and x.suffix.lower() in MEDIA_EXTS)
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class RepairWorker(QThread):
    progress = Signal(int, int, str, str)
    log = Signal(str)
    finished_ok = Signal(list)
    failed = Signal(str)

    def __init__(self, exe: str, reference: str, targets: List[str], output_dir: str, replace_originals: bool):
        super().__init__()
        self.exe = exe
        self.reference = reference
        self.targets = targets
        self.output_dir = output_dir
        self.replace_originals = replace_originals

    def run(self):
        results = []
        try:
            ref_meta = extract_metadata(self.exe, self.reference)
            output_folder = Path(self.output_dir)
            if not self.replace_originals:
                output_folder.mkdir(parents=True, exist_ok=True)

            for i, source_str in enumerate(self.targets, start=1):
                source = Path(source_str)
                self.progress.emit(i - 1, len(self.targets), source.name, 'Processing')
                source_fs_times = capture_filesystem_times(str(source))
                is_video = is_video_path(str(source))
                # Image orientation/dimensions are target-specific. Video target structure
                # is snapshot separately and verified byte-for-byte at the mdat level.
                target_orientation = (read_numeric_orientation(self.exe, str(source)) or 1) if not is_video else 1
                target_dimensions = read_actual_pixel_dimensions(self.exe, str(source)) if not is_video else None
                video_before_meta = extract_metadata(self.exe, str(source)) if is_video else None
                video_time_before = extract_video_temporal_metadata(self.exe, str(source)) if is_video else {}
                video_mdat_before = quicktime_mdat_digest(str(source)) if is_video else None
                dest = None
                backup = None
                clone_message = ''
                clone_mode = ''
                try:
                    if self.replace_originals:
                        dest = source
                        backup = source.with_suffix(source.suffix + '.metadatarepair_backup')
                        if not backup.exists():
                            shutil.copy2(source, backup)
                    else:
                        dest = unique_output_path(output_folder, source)
                        shutil.copy2(source, dest)

                    ref_ext = Path(self.reference).suffix.lower()
                    dst_ext = dest.suffix.lower()
                    same_format = ref_ext == dst_ext or {ref_ext, dst_ext} <= {'.jpg', '.jpeg'}

                    if is_video:
                        clone, clone_mode = clone_video_metadata(self.exe, self.reference, str(dest))
                        clone_message = '\n'.join(x for x in ((clone.stdout or '').strip(), (clone.stderr or '').strip()) if x).strip()
                        if clone.returncode != 0:
                            raise RuntimeError('Video metadata clone failed: ' + (clone_message or 'ExifTool returned an error'))
                    else:
                        strip = run_exiftool(self.exe, ['-all=', '-overwrite_original', str(dest)])
                        if strip.returncode != 0:
                            raise RuntimeError('Strip failed: ' + (strip.stderr.strip() or strip.stdout.strip()))

                    if (not is_video) and dst_ext == '.png' and not same_format:
                        clone_mode = 'png-direct-payload-v6-clean-aux-xmp'
                        _, clone_message = clone_metadata_to_png_binary(
                            self.exe, self.reference, str(dest), target_orientation, target_dimensions
                        )
                    elif not is_video:
                        clone, clone_mode = clone_metadata(self.exe, self.reference, str(dest))
                        clone_message = '\n'.join(x for x in (clone.stdout.strip(), clone.stderr.strip()) if x).strip()
                        if clone.returncode != 0:
                            raise RuntimeError('Clone failed: ' + (clone_message or 'ExifTool returned an error'))
                        # Never let the reference's Orientation rotate the destination
                        # pixels.  Restore the target's own display orientation.
                        orient_write = run_exiftool(self.exe, [
                            '-m', '-P', f'-Orientation#={target_orientation}',
                            f'-XMP-tiff:Orientation#={target_orientation}',
                            '-overwrite_original', str(dest)
                        ])
                        if orient_write.returncode != 0:
                            raise RuntimeError('Orientation restore failed: ' + (orient_write.stderr.strip() or orient_write.stdout.strip()))

                        # Pixel dimensions are destination-specific just like orientation.
                        # If we can determine the target's real geometry, rewrite common
                        # writable EXIF/XMP dimension tags instead of cloning stale values.
                        if target_dimensions:
                            tw, th = target_dimensions
                            dim_write = run_exiftool(self.exe, [
                                '-m', '-P',
                                f'-ExifIFD:ExifImageWidth#={tw}',
                                f'-ExifIFD:ExifImageHeight#={th}',
                                f'-XMP-exif:PixelXDimension#={tw}',
                                f'-XMP-exif:PixelYDimension#={th}',
                                '-overwrite_original', str(dest)
                            ])
                            if dim_write.returncode != 0:
                                raise RuntimeError('Dimension normalisation failed: ' + (dim_write.stderr.strip() or dim_write.stdout.strip()))
                            if dest.suffix.lower() == '.png':
                                normalise_existing_png_layout(str(dest), target_orientation, tw, th)

                    if is_video:
                        repaired_meta = extract_metadata(self.exe, str(dest))
                        structure_changes = compare_video_structure(video_before_meta or {}, repaired_meta)
                        if structure_changes:
                            raise RuntimeError('Video structural metadata changed unexpectedly: ' + '; '.join(structure_changes[:8]))
                        video_time_after = extract_video_temporal_metadata(self.exe, str(dest))
                        time_changes = video_temporal_diff(video_time_before, video_time_after)
                        if time_changes:
                            preview = '; '.join(f'{k}: {b!r} -> {a!r}' for k, b, a in time_changes[:8])
                            raise RuntimeError('Target video timestamps changed unexpectedly: ' + preview)
                        video_mdat_after = quicktime_mdat_digest(str(dest))
                        if video_mdat_before != video_mdat_after:
                            raise RuntimeError('Encoded media payload changed. Repair aborted because video/audio sample bytes must remain untouched.')
                    else:
                        validate_target_layout_metadata(self.exe, str(dest), target_dimensions, target_orientation)
                        if dst_ext == '.png' and not same_format:
                            validate_no_stale_auxiliary_xmp(self.exe, str(dest))
                        repaired_meta = extract_metadata(self.exe, str(dest))
                    embedded = embedded_payload_metadata(repaired_meta)
                    ref_embedded = embedded_payload_metadata(ref_meta)

                    # For PNG specifically, also verify the physical container gained at
                    # least one ancillary chunk.  This catches the exact failure mode where
                    # the target was stripped but ExifTool silently had nothing writable.
                    png_chunks = png_metadata_chunks(str(dest)) if dest.suffix.lower() == '.png' else []
                    if dest.suffix.lower() == '.png' and ref_embedded and not png_chunks:
                        raise RuntimeError(
                            'No PNG metadata chunks were written (output still contains only image-data chunks). '
                            + (clone_message or 'The HEIC metadata needs an explicit destination mapping.')
                        )
                    if ref_embedded and not embedded:
                        raise RuntimeError(
                            'ExifTool completed but wrote no transferable metadata. '
                            + (clone_message or 'The destination format accepted none of the source tags.')
                        )

                    # Metadata writes must not make the repaired file look newly-created at
                    # filesystem level either. Safe-copy mode restores the source's Windows
                    # creation/access/write FILETIMEs when available; other platforms keep
                    # access/modify times.
                    restore_filesystem_times(str(dest), source_fs_times)

                    if is_video:
                        cross_format = Path(self.reference).suffix.lower() != dst_ext
                        missing, changed, extra = video_metadata_diff(ref_meta, repaired_meta)
                        if not missing and not changed:
                            status = 'VIDEO META MATCH'
                        else:
                            status = f'{len(missing)} video tag(s) missing, {len(changed)} different'
                    else:
                        cross_format = clone_mode.startswith('mapped-cross-format') or clone_mode.startswith('png-direct-payload')
                        missing, changed, extra = metadata_diff(ref_meta, repaired_meta, cross_format=cross_format)
                        if not missing and not changed:
                            status = 'MAPPED MATCH' if cross_format else 'MATCH'
                        else:
                            status = f'{len(missing)} unavailable/missing, {len(changed)} different' if cross_format else f'{len(missing)} missing, {len(changed)} different'
                    results.append({
                        'source': str(source), 'output': str(dest), 'status': status,
                        'missing': missing, 'changed': changed, 'extra': extra,
                        'clone_mode': clone_mode, 'exiftool_output': clone_message,
                        'written_tag_count': len(embedded), 'png_chunks': png_chunks,
                        'target_orientation': target_orientation, 'target_dimensions': target_dimensions,
                        'media_kind': 'video' if is_video else 'image',
                        'video_mdat_sha256': video_mdat_before[0] if video_mdat_before else '',
                    })
                    chunk_detail = f', PNG chunks: {", ".join(sorted(set(png_chunks)))}' if png_chunks else ''
                    layout_detail = ''
                    if is_video and video_mdat_before:
                        layout_detail = f', media payload SHA-256 {video_mdat_before[0][:12]}… unchanged'
                    elif target_dimensions:
                        layout_detail = f', target {target_dimensions[0]}×{target_dimensions[1]} @ orientation {target_orientation}'
                    detail = f' [{clone_mode}, {len(embedded)} embedded tag(s){chunk_detail}{layout_detail}]'
                    self.log.emit(f'✓  {source.name}  →  {dest.name}    {status}{detail}')
                    if clone_message:
                        for line in clone_message.splitlines():
                            self.log.emit(f'   ExifTool: {line}')
                except Exception as e:
                    # Never leave a metadata-stripped file behind after a failed repair.
                    cleanup_note = ''
                    try:
                        if self.replace_originals and backup and backup.exists() and dest:
                            shutil.copy2(backup, dest)
                            cleanup_note = ' Original restored from backup.'
                        elif not self.replace_originals and dest and Path(dest).exists():
                            Path(dest).unlink()
                            cleanup_note = ' Failed output deleted.'
                    except Exception as cleanup_error:
                        cleanup_note = f' Cleanup warning: {cleanup_error}'

                    results.append({
                        'source': str(source), 'output': '', 'status': 'ERROR',
                        'error': str(e) + cleanup_note,
                        'missing': [], 'changed': [], 'extra': [],
                        'clone_mode': clone_mode, 'exiftool_output': clone_message,
                    })
                    self.log.emit(f'✗  {source.name}    {e}{cleanup_note}')
                self.progress.emit(i, len(self.targets), source.name, results[-1]['status'])

            self.finished_ok.emit(results)
        except Exception as e:
            self.failed.emit(str(e))


class CompareDialog(QDialog):
    def __init__(self, result: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Verification details')
        self.setWindowIcon(make_app_icon())
        self.resize(1120, 690)
        self.setMinimumSize(820, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(14)

        top = QHBoxLayout()
        title_wrap = QVBoxLayout()
        name = Path(result.get('output') or result.get('source', '')).name
        title = QLabel(name)
        title.setObjectName('SectionTitle')
        mode = result.get('clone_mode', 'preserve-groups')
        subtitle_text = 'Reference metadata vs repaired output'
        if mode.startswith('mapped-cross-format'):
            subtitle_text += ' • cross-format mapped copy'
        elif mode.startswith('video-quicktime'):
            subtitle_text += ' • video metadata copy; media streams preserved'
        subtitle = QLabel(subtitle_text)
        subtitle.setObjectName('Muted')
        title_wrap.addWidget(title)
        title_wrap.addWidget(subtitle)
        top.addLayout(title_wrap)
        top.addStretch()

        exact = result.get('status') in {'MATCH', 'MAPPED MATCH'}
        badge_text = '✓ EXACT MATCH' if result.get('status') == 'MATCH' else ('✓ MAPPED MATCH' if exact else '⚠ REVIEW DIFFERENCES')
        badge = QLabel(badge_text)
        badge.setObjectName('StatusReady' if exact else 'StatusWarn')
        top.addWidget(badge)
        root.addLayout(top)

        stats = QHBoxLayout()
        for value, label in [
            (len(result.get('missing', [])), 'Missing'),
            (len(result.get('changed', [])), 'Different'),
            (len(result.get('extra', [])), 'Extra after write'),
        ]:
            card = QFrame(); card.setObjectName('StatCard')
            l = QVBoxLayout(card); l.setContentsMargins(14, 10, 14, 10)
            v = QLabel(str(value)); v.setObjectName('StatValue')
            t = QLabel(label); t.setObjectName('StatLabel')
            l.addWidget(v); l.addWidget(t)
            stats.addWidget(card)
        root.addLayout(stats)

        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(['Result', 'Tag', 'Reference', 'Repaired'])
        table.setAlternatingRowColors(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)

        rows = [('Missing', *x) for x in result.get('missing', [])]
        rows += [('Different', *x) for x in result.get('changed', [])]
        rows += [('Extra', *x) for x in result.get('extra', [])]
        if not rows:
            rows = [('Match', 'All transferable metadata', 'Matches reference', 'Matches reference')]

        for row in rows:
            idx = table.rowCount(); table.insertRow(idx)
            for col, value in enumerate(row):
                text = '' if value is None else str(value)
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setTextAlignment(Qt.AlignCenter)
                table.setItem(idx, col, item)
        root.addWidget(table, 1)

        buttons = QHBoxLayout()
        output = result.get('output')
        if output:
            open_btn = QPushButton('Open output folder')
            open_btn.clicked.connect(lambda: self.open_folder(Path(output).parent))
            buttons.addWidget(open_btn)
        buttons.addStretch()
        close_btn = QPushButton('Close'); close_btn.setObjectName('Primary')
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

    def open_folder(self, folder: Path):
        try:
            if os.name == 'nt':
                os.startfile(str(folder))
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(folder)])
            else:
                subprocess.Popen(['xdg-open', str(folder)])
        except Exception as e:
            QMessageBox.warning(self, 'Could not open folder', str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Metadata Repair Tool')
        self.setWindowIcon(make_app_icon())
        self.resize(1180, 820)
        self.setMinimumSize(980, 690)

        self.targets: List[str] = []
        self.results: List[dict] = []
        self.worker = None
        self.exiftool = find_exiftool()

        root = QWidget(); self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setContentsMargins(26, 22, 26, 18)
        main.setSpacing(16)

        # Header
        header = QHBoxLayout()
        head_text = QVBoxLayout(); head_text.setSpacing(3)
        title = QLabel('Metadata Repair'); title.setObjectName('Title')
        subtitle = QLabel('Clone trusted metadata onto damaged images and videos without altering their media content.')
        subtitle.setObjectName('Subtitle')
        head_text.addWidget(title); head_text.addWidget(subtitle)
        header.addLayout(head_text)
        header.addStretch()
        self.top_status = QLabel('SETUP NEEDED' if not self.exiftool else 'READY')
        self.top_status.setObjectName('StatusWarn' if not self.exiftool else 'StatusReady')
        header.addWidget(self.top_status, 0, Qt.AlignTop)
        main.addLayout(header)

        # Main two-column area
        cols = QHBoxLayout(); cols.setSpacing(16)

        # Reference / settings column
        left_card = QFrame(); left_card.setObjectName('Card'); left_card.setMinimumWidth(360); left_card.setMaximumWidth(430)
        left = QVBoxLayout(left_card); left.setContentsMargins(18, 18, 18, 18); left.setSpacing(13)

        sec = QLabel('Reference media'); sec.setObjectName('SectionTitle'); left.addWidget(sec)
        self.ref_preview = QLabel('No reference\nselected')
        self.ref_preview.setAlignment(Qt.AlignCenter)
        self.ref_preview.setFixedHeight(190)
        self.ref_preview.setStyleSheet('background:#0a1120; border:1px dashed #34425d; border-radius:11px; color:#66748a; font-weight:600;')
        left.addWidget(self.ref_preview)

        self.ref_name = QLabel('Choose the known-good image or video')
        self.ref_name.setObjectName('Muted'); self.ref_name.setWordWrap(True)
        left.addWidget(self.ref_name)
        self.ref_meta_summary = QLabel('Metadata summary will appear here.')
        self.ref_meta_summary.setObjectName('Small'); self.ref_meta_summary.setWordWrap(True)
        left.addWidget(self.ref_meta_summary)

        ref_btn = QPushButton('Choose reference media')
        ref_btn.clicked.connect(self.pick_reference); left.addWidget(ref_btn)

        divider = QFrame(); divider.setFrameShape(QFrame.HLine); divider.setStyleSheet('color:#24314a;'); left.addWidget(divider)

        sec2 = QLabel('Repair settings'); sec2.setObjectName('SectionTitle'); left.addWidget(sec2)
        exif_label = QLabel('ExifTool'); exif_label.setObjectName('Small'); left.addWidget(exif_label)
        exif_row = QHBoxLayout(); exif_row.setSpacing(8)
        self.exif_edit = QLineEdit(self.exiftool or '')
        self.exif_edit.setPlaceholderText('Choose exiftool.exe')
        exif_row.addWidget(self.exif_edit, 1)
        exif_btn = QPushButton('Browse'); exif_btn.clicked.connect(self.pick_exiftool); exif_row.addWidget(exif_btn)
        left.addLayout(exif_row)

        out_label = QLabel('Output folder'); out_label.setObjectName('Small'); left.addWidget(out_label)
        out_row = QHBoxLayout(); out_row.setSpacing(8)
        self.output_edit = QLineEdit(str(Path.home() / 'Pictures' / 'Repaired'))
        out_row.addWidget(self.output_edit, 1)
        out_btn = QPushButton('Browse'); out_btn.clicked.connect(self.pick_output); out_row.addWidget(out_btn)
        left.addLayout(out_row)

        self.replace_box = QCheckBox('Replace originals instead')
        self.replace_box.setToolTip('Creates a .metadatarepair_backup before changing each original file.')
        self.replace_box.toggled.connect(self.on_replace_toggled)
        left.addWidget(self.replace_box)
        backup_note = QLabel('Safe mode is recommended. Replace mode creates a backup first.')
        backup_note.setObjectName('Small'); backup_note.setWordWrap(True); left.addWidget(backup_note)
        left.addStretch()
        cols.addWidget(left_card)

        # Targets column
        right_card = QFrame(); right_card.setObjectName('HeroCard')
        right = QVBoxLayout(right_card); right.setContentsMargins(18, 18, 18, 18); right.setSpacing(12)
        target_head = QHBoxLayout()
        target_titles = QVBoxLayout(); target_titles.setSpacing(2)
        st = QLabel('Files to repair'); st.setObjectName('SectionTitle')
        hint = QLabel('Drop images/videos or a whole folder below')
        hint.setObjectName('Muted')
        target_titles.addWidget(st); target_titles.addWidget(hint)
        target_head.addLayout(target_titles); target_head.addStretch()
        self.target_count_badge = QLabel('0 FILES'); self.target_count_badge.setObjectName('StatusIdle')
        target_head.addWidget(self.target_count_badge)
        right.addLayout(target_head)

        self.drop_list = DropList(); self.drop_list.files_dropped.connect(self.add_targets)
        self.drop_list.itemDoubleClicked.connect(lambda _item: self.preview_selected_target())
        right.addWidget(self.drop_list, 1)

        drop_hint = QLabel('Tip: drag JPG/PNG/HEIC images or MOV/MP4/M4V videos directly from Explorer.')
        drop_hint.setObjectName('Small'); drop_hint.setWordWrap(True); right.addWidget(drop_hint)

        target_actions = QHBoxLayout()
        add_btn = QPushButton('＋ Add files'); add_btn.clicked.connect(self.pick_targets); target_actions.addWidget(add_btn)
        remove_btn = QPushButton('Remove selected'); remove_btn.clicked.connect(self.remove_selected_targets); target_actions.addWidget(remove_btn)
        clear_btn = QPushButton('Clear all'); clear_btn.setObjectName('Danger'); clear_btn.clicked.connect(self.clear_targets); target_actions.addWidget(clear_btn)
        target_actions.addStretch()
        right.addLayout(target_actions)
        cols.addWidget(right_card, 1)
        main.addLayout(cols, 1)

        # Progress / action card
        action_card = QFrame(); action_card.setObjectName('Card')
        action = QVBoxLayout(action_card); action.setContentsMargins(17, 14, 17, 14); action.setSpacing(9)
        summary = QHBoxLayout()
        self.run_status = QLabel('Ready when you are'); self.run_status.setObjectName('SectionTitle')
        summary.addWidget(self.run_status); summary.addStretch()
        self.result_summary = QLabel('No repairs run yet'); self.result_summary.setObjectName('Muted')
        summary.addWidget(self.result_summary)
        action.addLayout(summary)
        self.progress = QProgressBar(); self.progress.setValue(0); action.addWidget(self.progress)
        action_buttons = QHBoxLayout()
        self.repair_btn = QPushButton('Repair metadata')
        self.repair_btn.setObjectName('Primary'); self.repair_btn.setMinimumHeight(46)
        self.repair_btn.clicked.connect(self.start_repair); action_buttons.addWidget(self.repair_btn, 1)
        self.compare_btn = QPushButton('Verification details')
        self.compare_btn.setEnabled(False); self.compare_btn.clicked.connect(self.compare_result); action_buttons.addWidget(self.compare_btn)
        self.open_output_btn = QPushButton('Open output')
        self.open_output_btn.setEnabled(False); self.open_output_btn.clicked.connect(self.open_output_folder); action_buttons.addWidget(self.open_output_btn)
        action.addLayout(action_buttons)
        main.addWidget(action_card)

        # Activity log, compact but useful
        log_card = QFrame(); log_card.setObjectName('Card')
        log_layout = QVBoxLayout(log_card); log_layout.setContentsMargins(14, 12, 14, 12); log_layout.setSpacing(7)
        log_head = QHBoxLayout()
        log_title = QLabel('Activity'); log_title.setObjectName('SectionTitle'); log_head.addWidget(log_title); log_head.addStretch()
        self.log_toggle = QPushButton('Show log'); self.log_toggle.setObjectName('Ghost'); self.log_toggle.clicked.connect(self.toggle_log)
        log_head.addWidget(self.log_toggle); log_layout.addLayout(log_head)
        self.log_box = QPlainTextEdit(); self.log_box.setReadOnly(True); self.log_box.setMaximumBlockCount(1000); self.log_box.setFixedHeight(130); self.log_box.hide()
        log_layout.addWidget(self.log_box)
        main.addWidget(log_card)

        self.statusBar().showMessage('Ready')
        self.update_ready_state()

    def set_badge(self, label: QLabel, text: str, state: str = 'idle'):
        label.setText(text)
        label.setObjectName({'ready': 'StatusReady', 'warn': 'StatusWarn'}.get(state, 'StatusIdle'))
        label.style().unpolish(label); label.style().polish(label)

    def update_ready_state(self):
        has_exif = bool(self.exif_edit.text().strip()) if hasattr(self, 'exif_edit') else bool(self.exiftool)
        has_ref = bool(self.ref_edit_text())
        has_targets = bool(self.targets)
        if has_exif and has_ref and has_targets:
            self.set_badge(self.top_status, 'READY', 'ready')
        elif not has_exif:
            self.set_badge(self.top_status, 'EXIFTOOL NEEDED', 'warn')
        else:
            self.set_badge(self.top_status, 'SETUP NEEDED', 'idle')

    def ref_edit_text(self) -> str:
        return getattr(self, '_reference_path', '')

    def pick_exiftool(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose ExifTool', '', 'Executable (*.exe);;All files (*)')
        if path:
            self.exif_edit.setText(path)
            self.exiftool = path
            self.update_reference_metadata()
            self.update_ready_state()

    def pick_reference(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 'Choose Reference Media', '',
            'Media (*.jpg *.jpeg *.png *.tif *.tiff *.webp *.heic *.heif *.avif *.mov *.mp4 *.m4v);;Images (*.jpg *.jpeg *.png *.tif *.tiff *.webp *.heic *.heif *.avif);;Videos (*.mov *.mp4 *.m4v);;All files (*)'
        )
        if path:
            self._reference_path = path
            self.ref_name.setText(Path(path).name)
            self.ref_name.setToolTip(path)
            preview = load_preview(path, QSize(360, 180))
            if preview:
                self.ref_preview.setPixmap(preview)
                self.ref_preview.setText('')
            else:
                self.ref_preview.setPixmap(QPixmap())
                self.ref_preview.setText('Preview unavailable\n(metadata can still be repaired)')
            self.update_reference_metadata()
            self.update_ready_state()

    def update_reference_metadata(self):
        path = self.ref_edit_text()
        exe = self.exif_edit.text().strip() if hasattr(self, 'exif_edit') else ''
        if not path:
            return
        size_text = human_size(Path(path).stat().st_size) if Path(path).exists() else 'Unknown size'
        if not exe or not Path(exe).exists():
            self.ref_meta_summary.setText(f'{Path(path).suffix.upper().lstrip(".")} • {size_text}\nChoose ExifTool to inspect metadata.')
            return
        try:
            meta = extract_metadata(exe, path)
            portable = portable_metadata(meta)
            make = meta.get('EXIF:Make') or meta.get('IFD0:Make') or meta.get('Keys:Make')
            model = meta.get('EXIF:Model') or meta.get('IFD0:Model') or meta.get('Keys:Model')
            date = meta.get('EXIF:DateTimeOriginal') or meta.get('ExifIFD:DateTimeOriginal') or meta.get('XMP:CreateDate') or meta.get('Keys:CreationDate') or meta.get('QuickTime:CreateDate')
            software = meta.get('EXIF:Software') or meta.get('IFD0:Software') or meta.get('XMP:CreatorTool') or meta.get('Keys:Software')
            parts = [f'{len(portable)} transferable tags', f'{Path(path).suffix.upper().lstrip(".")} • {size_text}']
            if make or model:
                parts.append('Camera: ' + ' '.join(str(x) for x in (make, model) if x))
            if date:
                parts.append('Captured: ' + str(date))
            if software:
                parts.append('Software: ' + str(software))
            self.ref_meta_summary.setText('\n'.join(parts[:5]))
        except Exception as e:
            self.ref_meta_summary.setText(f'{Path(path).suffix.upper().lstrip(".")} • {size_text}\nCould not read metadata: {e}')

    def pick_output(self):
        path = QFileDialog.getExistingDirectory(self, 'Choose Output Folder', self.output_edit.text())
        if path:
            self.output_edit.setText(path)

    def pick_targets(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, 'Choose Broken Media', '',
            'Media (*.jpg *.jpeg *.png *.tif *.tiff *.webp *.heic *.heif *.avif *.mov *.mp4 *.m4v);;Images (*.jpg *.jpeg *.png *.tif *.tiff *.webp *.heic *.heif *.avif);;Videos (*.mov *.mp4 *.m4v);;All files (*)'
        )
        self.add_targets(paths)

    def add_targets(self, paths):
        for p in paths:
            p = str(Path(p).resolve())
            if p not in self.targets:
                self.targets.append(p)
                path = Path(p)
                size = human_size(path.stat().st_size) if path.exists() else ''
                item = QListWidgetItem(f'{path.name}\n{path.parent}   •   {size}')
                item.setData(Qt.UserRole, p)
                preview = load_preview(p, QSize(52, 52))
                if preview:
                    canvas = QPixmap(52, 52); canvas.fill(QColor('#0c1323'))
                    painter = QPainter(canvas)
                    x = (52 - preview.width()) // 2; y = (52 - preview.height()) // 2
                    painter.drawPixmap(x, y, preview); painter.end()
                    item.setIcon(QIcon(canvas))
                self.drop_list.addItem(item)
        self.update_target_count()
        self.update_ready_state()

    def update_target_count(self):
        count = len(self.targets)
        self.set_badge(self.target_count_badge, f'{count} FILE' + ('' if count == 1 else 'S'), 'ready' if count else 'idle')
        if count:
            total_size = 0
            for p in self.targets:
                try: total_size += Path(p).stat().st_size
                except OSError: pass
            self.result_summary.setText(f'{count} file' + ('' if count == 1 else 's') + f' queued • {human_size(total_size)}')
        else:
            self.result_summary.setText('No repairs run yet')

    def remove_selected_targets(self):
        rows = sorted({self.drop_list.row(item) for item in self.drop_list.selectedItems()}, reverse=True)
        for row in rows:
            item = self.drop_list.takeItem(row)
            path = item.data(Qt.UserRole)
            if path in self.targets:
                self.targets.remove(path)
        self.results.clear(); self.compare_btn.setEnabled(False); self.open_output_btn.setEnabled(False)
        self.update_target_count(); self.update_ready_state()

    def clear_targets(self):
        self.targets.clear(); self.results.clear(); self.drop_list.clear()
        self.compare_btn.setEnabled(False); self.open_output_btn.setEnabled(False)
        self.progress.setValue(0); self.run_status.setText('Ready when you are')
        self.update_target_count(); self.update_ready_state()

    def preview_selected_target(self):
        # Double-click intentionally does nothing destructive; Explorer preview is enough.
        item = self.drop_list.currentItem()
        if not item: return
        path = item.data(Qt.UserRole)
        try:
            if os.name == 'nt': os.startfile(path)
            elif sys.platform == 'darwin': subprocess.Popen(['open', path])
            else: subprocess.Popen(['xdg-open', path])
        except Exception:
            pass

    def on_replace_toggled(self, checked):
        self.output_edit.setEnabled(not checked)
        self.open_output_btn.setEnabled(bool(self.results) and not checked)

    def toggle_log(self):
        visible = self.log_box.isVisible()
        self.log_box.setVisible(not visible)
        self.log_toggle.setText('Show log' if visible else 'Hide log')
        self.adjustSize() if False else None

    def validate(self):
        exe = self.exif_edit.text().strip()
        ref = self.ref_edit_text()
        if not exe or not Path(exe).exists():
            QMessageBox.warning(self, 'ExifTool required', 'Choose exiftool.exe first. The README includes the official download instructions.')
            return None
        if not ref or not Path(ref).is_file():
            QMessageBox.warning(self, 'Reference required', 'Choose the known-good reference image or video.')
            return None
        if not self.targets:
            QMessageBox.warning(self, 'No targets', 'Drag in at least one broken image or video.')
            return None
        if any(Path(x).resolve() == Path(ref).resolve() for x in self.targets):
            QMessageBox.warning(self, 'Reference included', 'The reference file cannot also be one of the repair targets.')
            return None
        ref_kind = media_kind(ref)
        mismatched = [Path(x).name for x in self.targets if media_kind(x) != ref_kind]
        if mismatched:
            QMessageBox.warning(
                self, 'Media type mismatch',
                'Use an image reference for image targets or a video reference for video targets.\n\n'
                'Mismatched: ' + ', '.join(mismatched[:5])
            )
            return None
        return exe, ref

    def start_repair(self):
        valid = self.validate()
        if not valid: return
        exe, ref = valid
        output = self.output_edit.text().strip()
        if not self.replace_box.isChecked() and not output:
            QMessageBox.warning(self, 'Output required', 'Choose an output folder.')
            return

        ref_ext = Path(ref).suffix.lower()
        if is_video_path(ref):
            response = QMessageBox.question(
                self, 'Video metadata mode',
                'Video repair copies writable QuickTime/iPhone device/location metadata such as make/model/software and GPS. The target video\'s own creation/modify/track/media timestamps are preserved and reference dates are never injected.\n\n'
                'It intentionally keeps each target video\'s real resolution, duration, frame rate, codec, rotation, HDR signalling, audio layout and media tracks. '
                'Those are structural facts, not metadata that should be cloned from another video.\n\nContinue?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )
            if response != QMessageBox.Yes:
                return
            cross_targets = []
        else:
            cross_targets = [Path(x).name for x in self.targets if not (Path(x).suffix.lower() == ref_ext or {Path(x).suffix.lower(), ref_ext} <= {'.jpg', '.jpeg'})]
        if cross_targets:
            preview = ', '.join(cross_targets[:3]) + ('…' if len(cross_targets) > 3 else '')
            response = QMessageBox.question(
                self, 'Cross-format metadata mapping',
                f'The reference is {ref_ext.upper().lstrip(".")} but {len(cross_targets)} target(s) use another format ({preview}).\n\n'
                'The app will map compatible metadata into the target format instead of preserving HEIC/JPEG/PNG-specific container groups. '
                'Some source-format-only fields cannot exist in the destination format.\n\nContinue?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )
            if response != QMessageBox.Yes:
                return

        if self.replace_box.isChecked():
            response = QMessageBox.question(
                self, 'Replace originals?',
                'This will modify the original files after creating .metadatarepair_backup copies.\n\nContinue?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if response != QMessageBox.Yes: return

        self.results = []; self.log_box.clear(); self.repair_btn.setEnabled(False)
        self.compare_btn.setEnabled(False); self.open_output_btn.setEnabled(False)
        self.progress.setMaximum(len(self.targets)); self.progress.setValue(0)
        self.run_status.setText('Preparing repair…'); self.result_summary.setText(f'0 / {len(self.targets)} complete')
        self.set_badge(self.top_status, 'WORKING', 'warn')
        self.log_box.appendPlainText(f'Reference: {ref}')
        self.log_box.appendPlainText(f'Targets: {len(self.targets)}')
        self.log_box.appendPlainText(f'Mode: {"Replace originals + backup" if self.replace_box.isChecked() else "Safe copies"}')
        self.log_box.appendPlainText('')

        self.worker = RepairWorker(exe, ref, list(self.targets), output, self.replace_box.isChecked())
        self.worker.log.connect(self.log_box.appendPlainText)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished_ok.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    def on_progress(self, current, total, source, status):
        self.progress.setMaximum(total); self.progress.setValue(current)
        if current < total:
            self.run_status.setText(f'Repairing {source}')
            self.result_summary.setText(f'{current} / {total} complete')
        self.statusBar().showMessage(f'{source}: {status}')

    def on_finished(self, results):
        self.results = results; self.repair_btn.setEnabled(True)
        self.compare_btn.setEnabled(bool(results))
        self.open_output_btn.setEnabled(bool(results) and not self.replace_box.isChecked())
        ok = sum(1 for x in results if x['status'] != 'ERROR')
        exact = sum(1 for x in results if x['status'] == 'MATCH')
        mapped = sum(1 for x in results if x['status'] == 'MAPPED MATCH')
        video_match = sum(1 for x in results if x['status'] == 'VIDEO META MATCH')
        errors = len(results) - ok
        self.progress.setValue(len(results))
        self.run_status.setText('Repair complete')
        pieces = []
        if exact: pieces.append(f'{exact} exact')
        if mapped: pieces.append(f'{mapped} mapped')
        if video_match: pieces.append(f'{video_match} video metadata match')
        match_text = ' • '.join(pieces) if pieces else 'completed'
        self.result_summary.setText(f'{match_text} • {ok} written' + (f' • {errors} error' + ('s' if errors != 1 else '') if errors else ''))
        self.log_box.appendPlainText('')
        self.log_box.appendPlainText(f'Done. {ok}/{len(results)} written; {exact} exact image match(es); {mapped} mapped image match(es); {video_match} video metadata match(es).')
        self.statusBar().showMessage('Finished')
        self.set_badge(self.top_status, 'COMPLETE' if not errors else 'REVIEW', 'ready' if not errors else 'warn')

        if errors:
            QMessageBox.warning(self, 'Finished with issues', f'{ok} of {len(results)} file(s) were written.\n{exact} exact image match(es); {mapped} mapped image match(es); {video_match} video metadata match(es).\n\nOpen Verification details or the Activity log for more information.')
        else:
            QMessageBox.information(self, 'Repair complete', f'{ok} file(s) repaired successfully.\n{exact} exact image match(es); {mapped} mapped image match(es); {video_match} video metadata match(es).')

    def on_failed(self, message):
        self.repair_btn.setEnabled(True); self.run_status.setText('Repair failed')
        self.result_summary.setText('No files were completed')
        self.log_box.appendPlainText('FATAL: ' + message)
        self.set_badge(self.top_status, 'FAILED', 'warn')
        QMessageBox.critical(self, 'Repair failed', message)

    def compare_result(self):
        if not self.results: return
        # Prefer the selected target's result when possible; otherwise last successful result.
        selected = self.drop_list.currentItem()
        selected_path = selected.data(Qt.UserRole) if selected else None
        result = next((x for x in self.results if x.get('source') == selected_path), None)
        if not result:
            result = next((x for x in reversed(self.results) if x.get('output')), self.results[-1])
        CompareDialog(result, self).exec()

    def open_output_folder(self):
        if self.replace_box.isChecked(): return
        folder = Path(self.output_edit.text().strip())
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if os.name == 'nt': os.startfile(str(folder))
            elif sys.platform == 'darwin': subprocess.Popen(['open', str(folder)])
            else: subprocess.Popen(['xdg-open', str(folder)])
        except Exception as e:
            QMessageBox.warning(self, 'Could not open folder', str(e))


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setApplicationName('Metadata Repair Tool')
    app.setStyle('Fusion')
    app.setStyleSheet(APP_STYLE)
    app.setWindowIcon(make_app_icon())
    win = MainWindow(); win.show()
    sys.exit(app.exec())
