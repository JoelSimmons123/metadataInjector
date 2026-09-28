import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import app as core


def _counter_state_path() -> Path:
    """Persistent app-wide video counter, independent of any output folder."""
    if os.name == 'nt':
        base = Path(os.environ.get('APPDATA') or os.environ.get('LOCALAPPDATA') or Path.home())
        folder = base / 'MetadataRepairTool'
    else:
        folder = Path.home() / '.metadata_repair_tool'
    folder.mkdir(parents=True, exist_ok=True)
    return folder / 'state.json'


def _reserve_video_number() -> int:
    """Atomically reserve the next permanent vid number.

    The number is advanced before rendering starts, so moving/deleting outputs or a
    failed render can never make a future run reuse a previously reserved name.
    """
    state_path = _counter_state_path()
    next_number = 1
    try:
        data = json.loads(state_path.read_text(encoding='utf-8'))
        next_number = max(1, int(data.get('next_video_number', 1)))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        next_number = 1

    tmp = state_path.with_suffix('.tmp')
    tmp.write_text(
        json.dumps({'next_video_number': next_number + 1}, indent=2) + '\n',
        encoding='utf-8',
    )
    os.replace(tmp, state_path)
    return next_number


def _cleaned_output_path(folder: Path, source: Path) -> Path:
    """Name metadata-repaired safe copies with a _cleaned suffix."""
    suffix = '.mov' if core.is_video_path(source) else source.suffix
    candidate = folder / f"{source.stem}_cleaned{suffix}"
    if not candidate.exists():
        return candidate
    n = 2
    while True:
        candidate = folder / f"{source.stem}_cleaned_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


# The base metadata worker calls core.unique_output_path() for safe-copy outputs.
# Override only the naming policy; all repair/verification logic remains unchanged.
core.unique_output_path = _cleaned_output_path
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QVBoxLayout,
)

TOPAZ_MODEL_CHOICES = [
    ('Proteus Natural', 'pnat-1'),
    ('Proteus (recommended on Video AI 7.x)', 'prob-4'),
    ('Iris MQ (faces)', 'iris-2'),
    ('Iris LQ (very low quality)', 'iris-3'),
    ('Rhea', 'rhea-1'),
]

TOPAZ_FI_CHOICES = [
    ('Chronos Fast (recommended for FPS conversion)', 'chf-3'),
    ('Chronos', 'chr-2'),
    ('Apollo', 'apo-8'),
]


def _human_bytes(size: int) -> str:
    value = float(max(0, int(size)))
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024
    return f'{value:.1f} TB'


def _norm_key(path: str | Path) -> str:
    try:
        return os.path.normcase(os.path.abspath(str(path)))
    except Exception:
        return str(path)


def _failed_copy_path(folder: Path, source: Path) -> Path:
    candidate = folder / source.name
    if not candidate.exists():
        return candidate
    n = 2
    while True:
        candidate = folder / f'{source.stem}_{n}{source.suffix}'
        if not candidate.exists():
            return candidate
        n += 1


def _topaz_manifest_path(output_root: Path) -> Path:
    return output_root / 'Topaz' / 'topaz_manifest.json'


def _load_topaz_manifest(output_root: Path) -> dict:
    path = _topaz_manifest_path(output_root)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            data.setdefault('version', 1)
            data.setdefault('items', {})
            data.setdefault('last_batch', {})
            return data
    except Exception:
        pass
    return {'version': 1, 'items': {}, 'last_batch': {}}


def _save_topaz_manifest(output_root: Path, manifest: dict) -> None:
    path = _topaz_manifest_path(output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def _manifest_item(manifest: dict, original_path: str) -> dict:
    items = manifest.setdefault('items', {})
    key = _norm_key(original_path)
    item = items.setdefault(key, {
        'original': original_path,
        'last_status': '',
        'last_upscale_output': '',
        'last_repaired_output': '',
        'last_failed_upscale_copy': '',
        'last_failed_metadata_copy': '',
        'last_error': '',
    })
    item['original'] = original_path
    return item


def _write_topaz_batch_report(output_root: Path, batch: dict) -> None:
    topaz_dir = output_root / 'Topaz'
    topaz_dir.mkdir(parents=True, exist_ok=True)

    json_path = topaz_dir / 'last_topaz_batch_report.json'
    txt_path = topaz_dir / 'last_topaz_batch_report.txt'
    json_path.write_text(json.dumps(batch, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    lines = []
    lines.append('Topaz batch report')
    lines.append('=' * 60)
    lines.append(f"Requested originals: {batch.get('total_requested', 0)}")
    lines.append(f"Skipped existing completions: {batch.get('skipped_existing_count', 0)}")
    lines.append(f"Upscale succeeded: {batch.get('upscale_success_count', 0)}")
    lines.append(f"Upscale failed: {batch.get('upscale_failure_count', 0)}")
    lines.append(f"Metadata repair succeeded: {batch.get('repair_success_count', 0)}")
    lines.append(f"Metadata repair failed: {batch.get('repair_failure_count', 0)}")
    lines.append(f"Stopped early: {'yes' if batch.get('stopped') else 'no'}")
    lines.append('')
    lines.append(
        f"Accounted for: {batch.get('accounted_count', 0)} / {batch.get('total_requested', 0)}"
    )
    lines.append('')

    if batch.get('skipped_existing'):
        lines.append('Skipped (already completed):')
        for item in batch['skipped_existing']:
            lines.append(f"  - {Path(item.get('source', '')).name} -> {item.get('repaired_output', '')}")
        lines.append('')

    if batch.get('successes'):
        lines.append('Successful upscales:')
        for item in batch['successes']:
            lines.append(f"  - {item.get('original_name', '')} -> {item.get('output', '')}")
        lines.append('')

    if batch.get('upscale_failures'):
        lines.append('Failed upscales:')
        for item in batch['upscale_failures']:
            extra = f" [copy: {item.get('failed_copy', '')}]" if item.get('failed_copy') else ''
            lines.append(f"  - {Path(item.get('source', '')).name}{extra}")
            if item.get('error'):
                lines.append(f"      {item['error']}")
        lines.append('')

    if batch.get('repair_successes'):
        lines.append('Metadata-repaired outputs:')
        for item in batch['repair_successes']:
            lines.append(
                f"  - {Path(item.get('original', '')).name} -> {item.get('repaired_output', '')}"
            )
        lines.append('')

    if batch.get('repair_failures'):
        lines.append('Failed metadata repairs:')
        for item in batch['repair_failures']:
            extra = f" [saved: {item.get('saved_copy', '')}]" if item.get('saved_copy') else ''
            lines.append(f"  - {Path(item.get('original', '')).name}{extra}")
            if item.get('error'):
                lines.append(f"      {item['error']}")
        lines.append('')

    txt_path.write_text('\n'.join(lines).rstrip() + '\n', encoding='utf-8')


def _is_generated_topaz_intermediate(source: Path, output_root: Path) -> bool:
    """Return True only for MP4s generated by this app in <output>/Topaz."""
    try:
        source_resolved = source.resolve()
        topaz_folder = (output_root / 'Topaz').resolve()
    except OSError:
        return False

    if source_resolved.parent != topaz_folder:
        return False
    if source_resolved.suffix.lower() != '.mp4':
        return False

    parts = source_resolved.stem.split('_')
    if len(parts) != 3:
        return False
    number, resolution, fps = parts
    return (
        number.startswith('vid')
        and number[3:].isdigit()
        and resolution in {'720', '1080'}
        and fps in {'60', 'origfps'}
    )


def find_topaz_ffmpeg() -> str | None:
    candidates = []
    if os.name == 'nt':
        program_files = Path(os.environ.get('ProgramFiles', r'C:\Program Files'))
        local = Path(os.environ.get('LOCALAPPDATA', '')) if os.environ.get('LOCALAPPDATA') else None
        candidates.extend([
            program_files / 'Topaz Labs LLC' / 'Topaz Video AI' / 'ffmpeg.exe',
            program_files / 'Topaz Labs LLC' / 'Topaz Video' / 'ffmpeg.exe',
        ])
        if local:
            candidates.extend([
                local / 'Programs' / 'Topaz Labs LLC' / 'Topaz Video AI' / 'ffmpeg.exe',
                local / 'Programs' / 'Topaz Labs LLC' / 'Topaz Video' / 'ffmpeg.exe',
            ])
    else:
        candidates.extend([
            Path('/Applications/Topaz Video AI.app/Contents/MacOS/ffmpeg'),
            Path('/Applications/Topaz Video.app/Contents/MacOS/ffmpeg'),
        ])
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def find_topaz_model_dir() -> str | None:
    candidates = []
    if os.name == 'nt':
        program_data = Path(os.environ.get('ProgramData', r'C:\ProgramData'))
        candidates.extend([
            program_data / 'Topaz Labs LLC' / 'Topaz Video AI' / 'models',
            program_data / 'Topaz Labs LLC' / 'Topaz Video' / 'models',
        ])
    else:
        candidates.extend([
            Path('/Applications/Topaz Video AI.app/Contents/Resources/models'),
            Path('/Applications/Topaz Video.app/Contents/Resources/models'),
        ])
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _creationflags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def _topaz_env(model_dir: str) -> dict:
    env = os.environ.copy()
    env['TVAI_MODEL_DIR'] = model_dir
    env['TVAI_MODEL_DATA_DIR'] = model_dir
    return env


def _probe_video_dimensions(ffmpeg_path: str, source: str, exiftool: str | None) -> tuple[int, int]:
    # The Topaz build's ffprobe can report a different stored orientation from
    # the frame that its decoder actually hands to tvai_up. Probe that frame,
    # which is what the output canvas must follow.
    candidates = [ffmpeg_path]
    try:
        standard_ffmpeg, _ = core.find_ffmpeg()
        if standard_ffmpeg != ffmpeg_path:
            candidates.append(standard_ffmpeg)
    except RuntimeError:
        pass
    decoded = []
    for candidate in candidates:
        p = subprocess.run(
            [candidate, '-v', 'error', '-nostdin', '-i', source,
             '-map', '0:v:0', '-frames:v', '1', '-c:v', 'png', '-f', 'image2pipe', '-'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_creationflags(),
        )
        if p.returncode == 0 and p.stdout.startswith(b'\x89PNG\r\n\x1a\n') and len(p.stdout) >= 24:
            w, h = struct.unpack('>II', p.stdout[16:24])
            if w > 0 and h > 0:
                decoded.append((w, h))
    if len(set(decoded)) > 1:
        raise RuntimeError(
            f'Video decoders disagree about display orientation: {decoded}. '
            'Upscale stopped to avoid a rotated or letterboxed output.'
        )
    if decoded:
        return decoded[0]
    raise RuntimeError('Could not decode a source frame to verify its display orientation.')


def _target_dimensions(width: int, height: int, short_side: int) -> tuple[int, int]:
    """Use the selected short side and retain the source display aspect ratio."""
    if short_side not in {720, 1080}:
        raise ValueError(f'Unsupported output preset: {short_side}p')
    if width <= 0 or height <= 0:
        raise ValueError('Invalid source video dimensions.')
    # H.264 4:2:0 requires even dimensions. Canonical 9:16 and 16:9 frames
    # remain exactly 1080x1920 / 1920x1080 under this calculation.
    long_side = max(short_side, 2 * round((short_side * max(width, height) / min(width, height)) / 2))
    return (short_side, long_side) if width <= height else (long_side, short_side)


def _supports_encoder(ffmpeg_path: str, encoder: str, model_dir: str) -> bool:
    try:
        p = subprocess.run(
            [ffmpeg_path, '-hide_banner', '-encoders'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding='utf-8', errors='replace', env=_topaz_env(model_dir),
            creationflags=_creationflags(), timeout=20,
        )
        return encoder in (p.stdout or '')
    except Exception:
        return False


def _validate_topaz_filters(ffmpeg_path: str, model_dir: str) -> tuple[bool, str]:
    try:
        p = subprocess.run(
            [ffmpeg_path, '-hide_banner', '-filters'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding='utf-8', errors='replace', env=_topaz_env(model_dir),
            creationflags=_creationflags(), timeout=30,
        )
        text = p.stdout or ''
        if 'tvai_up' not in text:
            return False, 'The selected ffmpeg does not expose the Topaz tvai_up filter.'
        if 'tvai_fi' not in text:
            return False, 'The selected ffmpeg does not expose the Topaz tvai_fi filter.'
        return True, ''
    except Exception as exc:
        return False, str(exc)


class VideoUpscaleWorker(QThread):
    progress = Signal(int, int, str, str)
    log = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, ffmpeg_path, model_dir, exiftool, targets, output_dir,
                 short_side, enhance_model, to_60fps, fi_model, replace_duplicates):
        super().__init__()
        self.ffmpeg_path = ffmpeg_path
        self.model_dir = model_dir
        self.exiftool = exiftool
        self.targets = targets
        self.output_dir = output_dir
        self.short_side = short_side
        self.enhance_model = enhance_model
        self.to_60fps = to_60fps
        self.fi_model = fi_model
        self.replace_duplicates = replace_duplicates
        self.stop_requested = False

    def request_stop(self):
        self.stop_requested = True

    def run(self):
        successes = []
        failures = []
        try:
            output_folder = Path(self.output_dir)
            output_folder.mkdir(parents=True, exist_ok=True)
            env = _topaz_env(self.model_dir)
            use_nvenc = _supports_encoder(self.ffmpeg_path, 'h264_nvenc', self.model_dir)

            for index, source_str in enumerate(self.targets, start=1):
                if self.stop_requested:
                    self.log.emit('Stop requested. No new videos will be started.')
                    break

                source = Path(source_str)
                destination = None
                self.progress.emit(index - 1, len(self.targets), source.name, 'Preparing')

                try:
                    width, height = _probe_video_dimensions(self.ffmpeg_path, str(source), self.exiftool)
                    out_w, out_h = _target_dimensions(width, height, self.short_side)

                    fps_suffix = '60' if self.to_60fps else 'origfps'
                    vid_num = _reserve_video_number()
                    destination = output_folder / f'vid{vid_num}_{self.short_side}_{fps_suffix}.mp4'
                    while destination.exists():
                        vid_num = _reserve_video_number()
                        destination = output_folder / f'vid{vid_num}_{self.short_side}_{fps_suffix}.mp4'

                    filters = []
                    if self.to_60fps:
                        rdt = '0.01' if self.replace_duplicates else '-0.000001'
                        filters.append(
                            f'tvai_fi=model={self.fi_model}:slowmo=1:rdt={rdt}:fps=60:'
                            'device=-2:vram=1:instances=1'
                        )
                    filters.append(
                        f'tvai_up=model={self.enhance_model}:scale=0:w={out_w}:h={out_h}:'
                        'preblur=0:noise=0:details=0:halo=0:blur=0:compression=0:'
                        'estimate=8:blend=0.2:device=-2:vram=1:instances=1'
                    )
                    filters.append(
                        f'scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:'
                        'flags=spline,'
                        f'pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2'
                    )

                    filter_graph = f'[0:v:0]{",".join(filters)}[vout]'
                    cmd = [
                        self.ffmpeg_path, '-hide_banner', '-nostdin', '-y', '-stats',
                        '-i', str(source), '-sws_flags', 'spline+accurate_rnd+full_chroma_int',
                        '-filter_complex', filter_graph,
                        '-map', '[vout]', '-map', '0:a?',
                    ]
                    if use_nvenc:
                        cmd += [
                            '-c:v', 'h264_nvenc', '-profile:v', 'high', '-level:v', '4.2',
                            '-preset', 'p6', '-tune', 'hq', '-rc', 'vbr', '-cq', '18', '-b:v', '0',
                            '-pix_fmt', 'yuv420p', '-tag:v', 'avc1',
                        ]
                    else:
                        cmd += [
                            '-c:v', 'libx264', '-profile:v', 'high', '-level:v', '4.2',
                            '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p', '-tag:v', 'avc1',
                        ]
                    cmd += [
                        '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-b:a', '192k',
                        '-map_metadata', '0',
                        '-metadata:s:v:0', 'encoder=',
                        '-metadata:s:a:0', 'encoder=',
                    ]
                    if self.to_60fps:
                        cmd += ['-fps_mode:v', 'cfr', '-r', '60']
                    else:
                        cmd += ['-fps_mode:v', 'passthrough']
                    cmd += [
                        '-movflags', '+faststart',
                        str(destination),
                    ]

                    self.log.emit(
                        f'▶  {source.name}  →  {destination.name}  '
                        f'[{width}×{height} → {out_w}×{out_h}'
                        + (', 60 FPS' if self.to_60fps else '') + ']'
                    )
                    self.progress.emit(index - 1, len(self.targets), source.name, 'Topaz processing')
                    proc = subprocess.run(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding='utf-8', errors='replace', env=env,
                        creationflags=_creationflags(),
                    )
                    if proc.returncode != 0:
                        try:
                            if destination.exists():
                                destination.unlink()
                        except OSError:
                            pass
                        tail = '\n'.join((proc.stdout or '').splitlines()[-35:])
                        command_text = subprocess.list2cmdline(cmd) if os.name == 'nt' else ' '.join(cmd)
                        raise RuntimeError(
                            f'Topaz failed for {source.name}.\n\nCommand:\n{command_text}\n\n'
                            f'{tail or "ffmpeg returned an error."}'
                        )

                    actual_w, actual_h = _probe_video_dimensions(
                        self.ffmpeg_path, str(destination), self.exiftool
                    )
                    if (actual_w, actual_h) != (out_w, out_h):
                        destination.unlink(missing_ok=True)
                        raise RuntimeError(
                            f'Topaz output orientation/size changed: expected {out_w}×{out_h}, '
                            f'got {actual_w}×{actual_h}. The incorrect output was deleted.'
                        )

                    successes.append({
                        'source': str(source),
                        'output': str(destination),
                        'original_name': source.name,
                        'output_name': destination.name,
                    })
                    self.log.emit(f'✓  {source.name}  →  {destination.name}')
                    self.progress.emit(index, len(self.targets), source.name, 'Complete')
                except Exception as exc:
                    try:
                        if destination is not None and destination.exists():
                            destination.unlink()
                    except OSError:
                        pass

                    message = str(exc)
                    failed_folder = output_folder / 'failed upscale'
                    failed_copy = ''
                    copy_error = ''
                    try:
                        failed_folder.mkdir(parents=True, exist_ok=True)
                        target = _failed_copy_path(failed_folder, source)
                        shutil.copy2(source, target)
                        failed_copy = str(target)
                    except Exception as copy_exc:
                        copy_error = str(copy_exc)

                    failures.append({
                        'source': str(source),
                        'original_name': source.name,
                        'failed_copy': failed_copy,
                        'copy_error': copy_error,
                        'error': message,
                    })
                    self.log.emit(f'✗  {source.name}  —  FAILED')
                    if failed_copy:
                        self.log.emit(f'   Copied failed source to: {failed_copy}')
                    elif copy_error:
                        self.log.emit(f'   WARNING: could not copy failed source: {copy_error}')
                    for line in message.splitlines():
                        self.log.emit(f'   {line}')
                    self.progress.emit(index, len(self.targets), source.name, 'Failed')

                if self.stop_requested:
                    self.log.emit('Stop requested. Halting after current video.')
                    break

            self.finished_ok.emit({
                'successes': successes,
                'failures': failures,
                'stopped': bool(self.stop_requested),
                'attempted': len(successes) + len(failures),
                'total': len(self.targets),
            })
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(core.MainWindow):
    def __init__(self):
        super().__init__()
        self.upscale_worker = None
        self._last_failed_upscale_sources = []
        self._last_failed_upscale_records = []
        self._last_failed_metadata_sources = []
        self._last_topaz_output_root = None
        self._topaz_generated_to_original = {}
        self._topaz_live_success_count = 0
        self._topaz_live_failure_count = 0
        self._topaz_live_total = 0
        self._topaz_live_skipped = 0
        self._topaz_current_skipped_existing = []
        self._topaz_last_batch = None
        self._auto_repair_context = None
        self._cleanup_checked_once = False
        self._add_topaz_video_card()
        self._cleanup_abandoned_topaz_partials(silent=True)

    def _output_root(self) -> Path:
        text = self.output_edit.text().strip() if hasattr(self, 'output_edit') else ''
        return Path(text) if text else (core.app_dir() / 'Repaired')

    def _topaz_dir(self) -> Path:
        return self._output_root() / 'Topaz'

    def _failed_upscale_dir(self) -> Path:
        return self._topaz_dir() / 'failed upscale'

    def _failed_metadata_dir(self) -> Path:
        return self._topaz_dir() / 'failed metadata repair'

    def _abandoned_partials_dir(self) -> Path:
        return self._topaz_dir() / 'abandoned partials'

    def _open_folder(self, folder: Path):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if os.name == 'nt':
                os.startfile(str(folder))
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(folder)])
            else:
                subprocess.Popen(['xdg-open', str(folder)])
        except Exception as exc:
            QMessageBox.warning(self, 'Could not open folder', str(exc))

    def _refresh_aux_buttons(self):
        self.topaz_retry_failed_btn.setEnabled(bool(self._last_failed_upscale_sources))
        failed_folder_exists = self._failed_upscale_dir().exists()
        self.topaz_open_failed_btn.setEnabled(failed_folder_exists)

    def _set_batch_summary(self, text: str):
        self.topaz_batch_summary.setText(text)

    def _update_live_batch_summary(self):
        if self._topaz_live_total <= 0:
            self._set_batch_summary('Ready')
            return
        processed = self._topaz_live_success_count + self._topaz_live_failure_count
        self._set_batch_summary(
            f'{processed} / {self._topaz_live_total} processed • '
            f'{self._topaz_live_success_count} succeeded • '
            f'{self._topaz_live_failure_count} failed'
            + (f' • {self._topaz_live_skipped} skipped' if self._topaz_live_skipped else '')
        )

    def _cleanup_abandoned_topaz_partials(self, silent: bool = True):
        folder = self._topaz_dir()
        if not folder.exists():
            return
        partials = []
        for path in folder.iterdir():
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix in {'.tmp', '.part', '.partial'}:
                partials.append(path)
                continue
            if suffix == '.mp4' and _is_generated_topaz_intermediate(path, self._output_root()):
                try:
                    if path.stat().st_size == 0:
                        partials.append(path)
                except OSError:
                    pass
        if not partials:
            return
        abandoned = self._abandoned_partials_dir()
        abandoned.mkdir(parents=True, exist_ok=True)
        moved = 0
        for path in partials:
            try:
                dest = _failed_copy_path(abandoned, path)
                shutil.move(str(path), str(dest))
                moved += 1
            except Exception:
                pass
        if moved and not silent:
            self.log_box.appendPlainText(
                f'Cleaned {moved} abandoned Topaz partial file(s) into {abandoned}.'
            )
        if moved:
            self.statusBar().showMessage(f'Cleaned {moved} abandoned Topaz partial file(s)', 6000)

    def _lookup_completed_outputs(self, videos: list[str]) -> tuple[list[dict], list[str]]:
        skipped = []
        remaining = []
        manifest = _load_topaz_manifest(self._output_root())
        for video in videos:
            item = manifest.get('items', {}).get(_norm_key(video))
            if (
                self.topaz_skip_completed_box.isChecked()
                and item
                and item.get('last_status') == 'repair_succeeded'
                and item.get('last_repaired_output')
                and Path(item.get('last_repaired_output')).is_file()
            ):
                skipped.append({
                    'source': video,
                    'repaired_output': item.get('last_repaired_output'),
                    'original_name': Path(video).name,
                })
            else:
                remaining.append(video)
        return skipped, remaining

    def _warn_if_low_disk_space(self, videos: list[str]) -> bool:
        try:
            usage = shutil.disk_usage(str(self._output_root()))
        except Exception:
            return True

        total_size = 0
        for video in videos:
            try:
                total_size += Path(video).stat().st_size
            except OSError:
                pass

        estimated_need = max(int(total_size * 2.5), 10 * 1024 ** 3)
        if usage.free >= estimated_need:
            return True

        response = QMessageBox.question(
            self,
            'Low disk space warning',
            'The selected batch may need a lot of temporary disk space.\n\n'
            f'Queued video input size: {_human_bytes(total_size)}\n'
            f'Estimated recommended free space: {_human_bytes(estimated_need)}\n'
            f'Currently free: {_human_bytes(usage.free)}\n\n'
            'Continue anyway?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return response == QMessageBox.Yes

    def _apply_queue_after_upscale(self, successes: list[dict], skipped_existing: list[dict]):
        if not self.topaz_replace_queue.isChecked():
            return

        replacement_map = {}
        for item in successes:
            replacement_map[_norm_key(item['source'])] = item['output']
            self._topaz_generated_to_original[_norm_key(item['output'])] = item['source']
        for item in skipped_existing:
            replacement_map[_norm_key(item['source'])] = item['repaired_output']

        updated_targets = []
        changed = False
        for target in self.targets:
            key = _norm_key(target)
            if core.is_video_path(target) and key in replacement_map:
                updated_targets.append(replacement_map[key])
                changed = True
            else:
                updated_targets.append(target)

        if changed:
            self.targets = []
            self.drop_list.clear()
            self.add_targets(updated_targets)
            self.results.clear()
            self.compare_btn.setEnabled(False)
            self.open_output_btn.setEnabled(False)
            self.log_box.appendPlainText(
                'Queue updated: successful originals were replaced with Topaz outputs, '
                'and skipped originals were replaced with their existing repaired outputs.'
            )

    def _persist_upscale_batch_state(self, successes: list[dict], failures: list[dict], skipped_existing: list[dict]):
        output_root = self._output_root()
        manifest = _load_topaz_manifest(output_root)
        for item in skipped_existing:
            entry = _manifest_item(manifest, item['source'])
            entry['last_status'] = 'repair_succeeded'
            entry['last_repaired_output'] = item['repaired_output']
            entry['last_error'] = ''
        for item in successes:
            entry = _manifest_item(manifest, item['source'])
            entry['last_status'] = 'upscaled_only'
            entry['last_upscale_output'] = item['output']
            entry['last_error'] = ''
        for item in failures:
            entry = _manifest_item(manifest, item['source'])
            entry['last_status'] = 'upscale_failed'
            entry['last_failed_upscale_copy'] = item.get('failed_copy', '')
            entry['last_error'] = item.get('error', '')
        manifest['last_batch'] = {
            'total_requested': self._topaz_last_batch.get('total_requested', 0) if self._topaz_last_batch else 0,
            'skipped_existing_count': len(skipped_existing),
            'upscale_success_count': len(successes),
            'upscale_failure_count': len(failures),
        }
        _save_topaz_manifest(output_root, manifest)

    def _finalize_topaz_report_without_repair(self):
        if not self._topaz_last_batch:
            return
        batch = self._topaz_last_batch
        batch['repair_success_count'] = 0
        batch['repair_failure_count'] = 0
        batch['repair_successes'] = []
        batch['repair_failures'] = []
        batch['accounted_count'] = (
            batch.get('skipped_existing_count', 0)
            + batch.get('upscale_success_count', 0)
            + batch.get('upscale_failure_count', 0)
        )
        _write_topaz_batch_report(self._output_root(), batch)

    def _start_auto_repair(self, success_outputs: list[str]):
        if not success_outputs:
            return
        full_targets = list(self.targets)
        self._auto_repair_context = {
            'full_targets': full_targets,
            'repair_inputs': list(success_outputs),
            'batch': dict(self._topaz_last_batch or {}),
        }

        self.targets = list(success_outputs)
        self.drop_list.clear()
        self.add_targets(self.targets)
        self.refresh_target_labels()
        self.log_box.appendPlainText('')
        self.log_box.appendPlainText(
            f'Auto-starting metadata repair for {len(success_outputs)} successful Topaz output(s).'
        )

        prior_worker = self.worker
        super().start_repair()
        launched = self.worker is not None and self.worker is not prior_worker and self.worker.isRunning()
        if not launched:
            self.targets = full_targets
            self.drop_list.clear()
            self.add_targets(self.targets)
            self.refresh_target_labels()
            self._auto_repair_context = None
            self.log_box.appendPlainText('Auto metadata repair was cancelled before starting.')

    def retry_failed_upscales(self):
        if not self._last_failed_upscale_sources:
            QMessageBox.information(self, 'No failed upscales', 'There are no failed upscale originals to retry.')
            return
        self.targets = []
        self.drop_list.clear()
        self.add_targets(list(self._last_failed_upscale_sources))
        self.results.clear()
        self.compare_btn.setEnabled(False)
        self.open_output_btn.setEnabled(False)
        self.log_box.appendPlainText(
            f'Reloaded {len(self._last_failed_upscale_sources)} failed original video(s) for retry.'
        )

    def open_failed_upscale_folder(self):
        self._open_folder(self._failed_upscale_dir())

    def request_stop_after_current(self):
        if not self.upscale_worker or not self.upscale_worker.isRunning():
            return
        self.upscale_worker.request_stop()
        self.topaz_stop_btn.setEnabled(False)
        self.topaz_stop_btn.setText('Stopping…')
        self.topaz_status.setText('Will stop after current video')
        self.log_box.appendPlainText('Stop requested: the current video will finish, then the batch will stop.')

    def _add_topaz_video_card(self):
        main = self.centralWidget().layout()
        card = QFrame()
        card.setObjectName('Card')
        layout = QVBoxLayout(card)
        layout.setContentsMargins(17, 14, 17, 14)
        layout.setSpacing(9)

        head = QHBoxLayout()
        title_wrap = QVBoxLayout()
        title_wrap.setSpacing(2)
        title = QLabel('Video enhancement')
        title.setObjectName('SectionTitle')
        subtitle = QLabel(
            'Topaz Video local upscale + optional 60 FPS conversion. '
            'Results can replace queued video entries before metadata repair.'
        )
        subtitle.setObjectName('Muted')
        subtitle.setWordWrap(True)
        title_wrap.addWidget(title)
        title_wrap.addWidget(subtitle)
        head.addLayout(title_wrap)
        head.addStretch()
        layout.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(7)

        lbl = QLabel('Topaz ffmpeg'); lbl.setObjectName('Small'); grid.addWidget(lbl, 0, 0)
        self.topaz_ffmpeg_edit = QLineEdit(find_topaz_ffmpeg() or '')
        self.topaz_ffmpeg_edit.setPlaceholderText(r'C:\Program Files\Topaz Labs LLC\Topaz Video AI\ffmpeg.exe')
        grid.addWidget(self.topaz_ffmpeg_edit, 0, 1, 1, 3)
        b = QPushButton('Browse'); b.clicked.connect(self.pick_topaz_ffmpeg); grid.addWidget(b, 0, 4)

        lbl = QLabel('Model folder'); lbl.setObjectName('Small'); grid.addWidget(lbl, 1, 0)
        self.topaz_model_dir_edit = QLineEdit(find_topaz_model_dir() or '')
        self.topaz_model_dir_edit.setPlaceholderText(r'C:\ProgramData\Topaz Labs LLC\Topaz Video AI\models')
        grid.addWidget(self.topaz_model_dir_edit, 1, 1, 1, 3)
        b2 = QPushButton('Browse'); b2.clicked.connect(self.pick_topaz_model_dir); grid.addWidget(b2, 1, 4)

        grid.addWidget(QLabel('Enhancement'), 2, 0)
        self.topaz_model_combo = QComboBox()
        self._refresh_topaz_model_choices()
        grid.addWidget(self.topaz_model_combo, 2, 1)

        grid.addWidget(QLabel('Output preset'), 2, 2)
        self.topaz_preset_combo = QComboBox()
        self.topaz_preset_combo.addItem('720p60', (720, True))
        self.topaz_preset_combo.addItem('1080p60', (1080, True))
        self.topaz_preset_combo.addItem('720p (original FPS)', (720, False))
        self.topaz_preset_combo.addItem('1080p (original FPS)', (1080, False))
        self.topaz_preset_combo.setCurrentIndex(1)
        self.topaz_preset_combo.currentIndexChanged.connect(self._apply_output_preset)
        grid.addWidget(self.topaz_preset_combo, 2, 3)

        self.topaz_resolution_combo = QComboBox()
        self.topaz_resolution_combo.addItem('720p', 720)
        self.topaz_resolution_combo.addItem('1080p', 1080)
        self.topaz_resolution_combo.hide()

        self.topaz_60fps_box = QCheckBox('Convert to 60 FPS')
        self.topaz_60fps_box.setChecked(True)
        self.topaz_60fps_box.toggled.connect(self._toggle_fi_controls)
        self.topaz_60fps_box.hide()

        self.topaz_fi_combo = QComboBox()
        for label, model_id in TOPAZ_FI_CHOICES:
            self.topaz_fi_combo.addItem(label, model_id)
        grid.addWidget(self.topaz_fi_combo, 3, 2, 1, 2)

        self.topaz_replace_duplicates = QCheckBox('Replace duplicate frames')
        self.topaz_replace_duplicates.setChecked(True)
        grid.addWidget(self.topaz_replace_duplicates, 3, 4)

        self._apply_output_preset()

        self.topaz_replace_queue = QCheckBox('Replace queued video entries with enhanced outputs')
        self.topaz_replace_queue.setChecked(True)
        self.topaz_replace_queue.setToolTip(
            'After Topaz finishes, original queued video targets are swapped for the enhanced files. Images stay queued.'
        )
        grid.addWidget(self.topaz_replace_queue, 4, 0, 1, 5)

        self.topaz_auto_repair_box = QCheckBox('Automatically run metadata repair on successful upscales')
        self.topaz_auto_repair_box.setChecked(True)
        grid.addWidget(self.topaz_auto_repair_box, 5, 0, 1, 5)

        self.topaz_skip_completed_box = QCheckBox('Skip originals that already have a completed repaired output')
        self.topaz_skip_completed_box.setChecked(True)
        grid.addWidget(self.topaz_skip_completed_box, 6, 0, 1, 5)

        layout.addLayout(grid)

        bottom = QHBoxLayout()
        self.topaz_progress = QProgressBar()
        self.topaz_progress.setValue(0)
        bottom.addWidget(self.topaz_progress, 1)
        self.topaz_status = QLabel('Ready')
        self.topaz_status.setObjectName('Muted')
        bottom.addWidget(self.topaz_status)
        self.topaz_run_btn = QPushButton('Upscale queued videos')
        self.topaz_run_btn.clicked.connect(self.start_video_upscale)
        bottom.addWidget(self.topaz_run_btn)
        self.topaz_stop_btn = QPushButton('Stop after current')
        self.topaz_stop_btn.setEnabled(False)
        self.topaz_stop_btn.clicked.connect(self.request_stop_after_current)
        bottom.addWidget(self.topaz_stop_btn)
        layout.addLayout(bottom)

        footer = QHBoxLayout()
        self.topaz_batch_summary = QLabel('Ready')
        self.topaz_batch_summary.setObjectName('Small')
        footer.addWidget(self.topaz_batch_summary, 1)
        self.topaz_retry_failed_btn = QPushButton('Retry failed upscales')
        self.topaz_retry_failed_btn.setEnabled(False)
        self.topaz_retry_failed_btn.clicked.connect(self.retry_failed_upscales)
        footer.addWidget(self.topaz_retry_failed_btn)
        self.topaz_open_failed_btn = QPushButton('Open failed upscale folder')
        self.topaz_open_failed_btn.setEnabled(False)
        self.topaz_open_failed_btn.clicked.connect(self.open_failed_upscale_folder)
        footer.addWidget(self.topaz_open_failed_btn)
        layout.addLayout(footer)

        main.insertWidget(2, card)
        self._refresh_aux_buttons()

    def _refresh_topaz_model_choices(self):
        current = self.topaz_model_combo.currentData() if hasattr(self, 'topaz_model_combo') else None
        model_dir = Path(self.topaz_model_dir_edit.text().strip()) if hasattr(self, 'topaz_model_dir_edit') and self.topaz_model_dir_edit.text().strip() else None
        available = []
        for label, model_id in TOPAZ_MODEL_CHOICES:
            if model_dir and (model_dir / f'{model_id}.json').exists():
                available.append((label, model_id))
        if not available:
            available = list(TOPAZ_MODEL_CHOICES)
        self.topaz_model_combo.clear()
        for label, model_id in available:
            self.topaz_model_combo.addItem(label, model_id)
        preferred = 'pnat-1' if any(mid == 'pnat-1' for _, mid in available) else 'prob-4'
        if current and any(mid == current for _, mid in available):
            preferred = current
        for i in range(self.topaz_model_combo.count()):
            if self.topaz_model_combo.itemData(i) == preferred:
                self.topaz_model_combo.setCurrentIndex(i)
                break

    def _toggle_fi_controls(self, enabled):
        self.topaz_fi_combo.setEnabled(enabled)
        self.topaz_replace_duplicates.setEnabled(enabled)

    def _apply_output_preset(self):
        if not hasattr(self, 'topaz_preset_combo'):
            return
        data = self.topaz_preset_combo.currentData()
        if not data:
            return
        short_side, to_60fps = data
        for i in range(self.topaz_resolution_combo.count()):
            if self.topaz_resolution_combo.itemData(i) == short_side:
                self.topaz_resolution_combo.setCurrentIndex(i)
                break
        self.topaz_60fps_box.setChecked(bool(to_60fps))
        self._toggle_fi_controls(bool(to_60fps))

    def pick_topaz_ffmpeg(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 'Choose Topaz ffmpeg', self.topaz_ffmpeg_edit.text() or '',
            'FFmpeg (ffmpeg.exe);;Executables (*.exe);;All files (*)'
        )
        if path:
            self.topaz_ffmpeg_edit.setText(path)

    def pick_topaz_model_dir(self):
        path = QFileDialog.getExistingDirectory(
            self, 'Choose Topaz model folder', self.topaz_model_dir_edit.text() or ''
        )
        if path:
            self.topaz_model_dir_edit.setText(path)
            self._refresh_topaz_model_choices()

    def start_video_upscale(self):
        if self.upscale_worker and self.upscale_worker.isRunning():
            return
        self._cleanup_abandoned_topaz_partials(silent=False)

        videos = [x for x in self.targets if core.is_video_path(x)]
        if not videos:
            QMessageBox.information(self, 'No videos queued', 'Add at least one MOV/MP4/M4V video first.')
            return

        ffmpeg_path = self.topaz_ffmpeg_edit.text().strip()
        model_dir = self.topaz_model_dir_edit.text().strip()
        if not ffmpeg_path or not Path(ffmpeg_path).is_file():
            QMessageBox.warning(self, 'Topaz ffmpeg required', 'Choose the ffmpeg executable installed with Topaz Video.')
            return
        if not model_dir or not Path(model_dir).is_dir():
            QMessageBox.warning(self, 'Topaz model folder required', 'Choose the Topaz Video models folder.')
            return

        ok, detail = _validate_topaz_filters(ffmpeg_path, model_dir)
        if not ok:
            QMessageBox.warning(
                self, 'Topaz CLI not detected', detail +
                '\n\nUse the ffmpeg executable from the Topaz Video installation, not a normal FFmpeg build.'
            )
            return

        skipped_existing, process_videos = self._lookup_completed_outputs(videos)
        if process_videos and not self._warn_if_low_disk_space(process_videos):
            return

        base_output = self._output_root()
        output_dir = str(base_output / 'Topaz')
        exiftool = self.exif_edit.text().strip() if hasattr(self, 'exif_edit') else None

        self._topaz_last_batch = {
            'total_requested': len(videos),
            'requested_sources': list(videos),
            'skipped_existing_count': len(skipped_existing),
            'skipped_existing': skipped_existing,
            'successes': [],
            'upscale_failures': [],
            'repair_successes': [],
            'repair_failures': [],
            'upscale_success_count': 0,
            'upscale_failure_count': 0,
            'repair_success_count': 0,
            'repair_failure_count': 0,
            'stopped': False,
            'auto_repair': bool(self.topaz_auto_repair_box.isChecked()),
            'accounted_count': 0,
        }
        self._topaz_current_skipped_existing = list(skipped_existing)
        self._last_topaz_output_root = str(base_output)
        self._last_failed_upscale_sources = []
        self._last_failed_upscale_records = []
        self._last_failed_metadata_sources = []

        if not process_videos:
            self._apply_queue_after_upscale([], skipped_existing)
            self._persist_upscale_batch_state([], [], skipped_existing)
            self._finalize_topaz_report_without_repair()
            self._topaz_live_total = len(videos)
            self._topaz_live_skipped = len(skipped_existing)
            self._topaz_live_success_count = 0
            self._topaz_live_failure_count = 0
            self._update_live_batch_summary()
            self._refresh_aux_buttons()
            QMessageBox.information(
                self,
                'Nothing to process',
                f'All {len(videos)} queued video(s) were skipped because a completed repaired output already exists.'
            )
            return

        self.topaz_run_btn.setEnabled(False)
        self.topaz_stop_btn.setEnabled(True)
        self.topaz_stop_btn.setText('Stop after current')
        self.repair_btn.setEnabled(False)
        self.topaz_progress.setMaximum(len(process_videos))
        self.topaz_progress.setValue(0)
        self.topaz_status.setText('Starting…')
        self._topaz_live_total = len(process_videos)
        self._topaz_live_skipped = len(skipped_existing)
        self._topaz_live_success_count = 0
        self._topaz_live_failure_count = 0
        self._update_live_batch_summary()
        self.log_box.appendPlainText('')
        self.log_box.appendPlainText('Topaz video enhancement')
        self.log_box.appendPlainText(f'Output: {output_dir}')
        if skipped_existing:
            self.log_box.appendPlainText(
                f'Skipping {len(skipped_existing)} original video(s) that already have a completed repaired output.'
            )

        self.upscale_worker = VideoUpscaleWorker(
            ffmpeg_path, model_dir, exiftool, process_videos, output_dir,
            int(self.topaz_resolution_combo.currentData()),
            str(self.topaz_model_combo.currentData()),
            self.topaz_60fps_box.isChecked(),
            str(self.topaz_fi_combo.currentData()),
            self.topaz_replace_duplicates.isChecked(),
        )
        self.upscale_worker.log.connect(self.log_box.appendPlainText)
        self.upscale_worker.progress.connect(self.on_upscale_progress)
        self.upscale_worker.finished_ok.connect(self.on_upscale_finished)
        self.upscale_worker.failed.connect(self.on_upscale_failed)
        self.upscale_worker.start()

    def on_upscale_progress(self, current, total, source, status):
        self.topaz_progress.setMaximum(total)
        self.topaz_progress.setValue(current)
        self.topaz_status.setText(f'{source}: {status}')
        if status == 'Complete':
            self._topaz_live_success_count += 1
        elif status == 'Failed':
            self._topaz_live_failure_count += 1
        self._update_live_batch_summary()

    def on_upscale_finished(self, summary):
        successes = list(summary.get('successes', []))
        failures = list(summary.get('failures', []))
        stopped = bool(summary.get('stopped'))
        success_count = len(successes)
        failure_count = len(failures)

        self._last_failed_upscale_records = failures
        self._last_failed_upscale_sources = [item['source'] for item in failures]
        self._refresh_aux_buttons()

        self.topaz_progress.setValue(summary.get('attempted', 0))
        if failure_count:
            self.topaz_status.setText(f'{success_count} complete, {failure_count} failed')
        else:
            self.topaz_status.setText(f'{success_count} complete')
        self.topaz_run_btn.setEnabled(True)
        self.topaz_stop_btn.setEnabled(False)
        self.topaz_stop_btn.setText('Stop after current')
        self.repair_btn.setEnabled(True)

        self._apply_queue_after_upscale(successes, self._topaz_current_skipped_existing)
        self._persist_upscale_batch_state(successes, failures, self._topaz_current_skipped_existing)

        if self._topaz_last_batch is not None:
            self._topaz_last_batch['successes'] = successes
            self._topaz_last_batch['upscale_failures'] = failures
            self._topaz_last_batch['upscale_success_count'] = success_count
            self._topaz_last_batch['upscale_failure_count'] = failure_count
            self._topaz_last_batch['stopped'] = stopped

        self._set_batch_summary(
            f'{success_count + failure_count} / {summary.get("total", success_count + failure_count)} processed • '
            f'{success_count} succeeded • {failure_count} failed'
            + (f' • {len(self._topaz_current_skipped_existing)} skipped' if self._topaz_current_skipped_existing else '')
            + (' • stopped early' if stopped else '')
        )

        success_outputs = [item['output'] for item in successes]

        if self.topaz_auto_repair_box.isChecked() and success_outputs:
            self.log_box.appendPlainText(
                f'Auto repair enabled: starting metadata repair for {len(success_outputs)} successful Topaz output(s).'
            )
            self._start_auto_repair(success_outputs)
            return

        self._finalize_topaz_report_without_repair()

        if failure_count:
            QMessageBox.warning(
                self, 'Topaz enhancement finished with errors',
                f'{success_count} video(s) enhanced successfully.\n'
                f'{failure_count} video(s) failed.\n\n'
                f'Copies of failed originals were saved to:\n{self._failed_upscale_dir()}\n\n'
                'The rest of the batch was not stopped.'
                + ('\n\nProcessing was stopped after the current video.' if stopped else '')
            )
        else:
            QMessageBox.information(
                self, 'Topaz enhancement complete',
                f'{success_count} video(s) enhanced successfully.\n\n' +
                ('The enhanced files are now queued for metadata repair.'
                 if self.topaz_replace_queue.isChecked()
                 else 'The enhanced files were saved in the Topaz output folder.')
            )

    def on_finished(self, results):
        output_root = self._output_root()
        manifest = _load_topaz_manifest(output_root)

        deleted_intermediates = {}
        moved_failed_repairs = {}
        delete_warnings = []
        repair_successes = []
        repair_failures = []

        for result in results:
            source_text = result.get('source') or ''
            repaired_text = result.get('output') or ''
            source_path = Path(source_text) if source_text else None
            original_source = self._topaz_generated_to_original.get(_norm_key(source_text), source_text)
            result['topaz_original_source'] = original_source

            if not source_path or not _is_generated_topaz_intermediate(source_path, output_root):
                continue

            entry = _manifest_item(manifest, original_source)

            if result.get('status') != 'ERROR':
                if repaired_text and Path(repaired_text).is_file():
                    entry['last_status'] = 'repair_succeeded'
                    entry['last_repaired_output'] = repaired_text
                    entry['last_upscale_output'] = source_text
                    entry['last_error'] = ''
                    repair_successes.append({
                        'original': original_source,
                        'intermediate': source_text,
                        'repaired_output': repaired_text,
                    })
                    try:
                        source_path.unlink()
                        deleted_intermediates[_norm_key(source_text)] = repaired_text
                        result['topaz_intermediate_deleted'] = True
                        self.log_box.appendPlainText(
                            f'🧹  Deleted Topaz intermediate after successful repair: {source_path.name} '
                            f'(original: {Path(original_source).name})'
                        )
                    except OSError as exc:
                        warning = f'Could not delete Topaz intermediate {source_path.name}: {exc}'
                        delete_warnings.append(warning)
                        result['topaz_intermediate_deleted'] = False
                        result['topaz_intermediate_delete_error'] = str(exc)
                        self.log_box.appendPlainText('WARNING: ' + warning)
            else:
                failed_folder = self._failed_metadata_dir()
                failed_folder.mkdir(parents=True, exist_ok=True)
                moved_path = None
                move_error = ''
                try:
                    moved_path = _failed_copy_path(failed_folder, source_path)
                    shutil.move(str(source_path), str(moved_path))
                    moved_failed_repairs[_norm_key(source_text)] = str(moved_path)
                    self._last_failed_metadata_sources.append(str(moved_path))
                    result['failed_metadata_saved'] = str(moved_path)
                    self.log_box.appendPlainText(
                        f'⚠  Metadata repair failed for {Path(original_source).name}. '
                        f'Saved the Topaz intermediate to {moved_path}'
                    )
                except Exception as exc:
                    move_error = str(exc)
                    self.log_box.appendPlainText(
                        f'WARNING: could not move failed metadata-repair source {source_path.name}: {move_error}'
                    )

                entry['last_status'] = 'repair_failed'
                entry['last_upscale_output'] = source_text
                entry['last_failed_metadata_copy'] = str(moved_path) if moved_path else ''
                entry['last_error'] = result.get('error', '')
                repair_failures.append({
                    'original': original_source,
                    'intermediate': source_text,
                    'saved_copy': str(moved_path) if moved_path else '',
                    'error': result.get('error', '') + (f' | move warning: {move_error}' if move_error else ''),
                })

        _save_topaz_manifest(output_root, manifest)

        super().on_finished(results)

        updated_targets_base = list(self.targets)
        if self._auto_repair_context:
            updated_targets_base = list(self._auto_repair_context.get('full_targets', updated_targets_base))

        updated_targets = []
        changed = False
        for target in updated_targets_base:
            key = _norm_key(target)
            if key in deleted_intermediates:
                updated_targets.append(deleted_intermediates[key])
                changed = True
            elif key in moved_failed_repairs:
                updated_targets.append(moved_failed_repairs[key])
                changed = True
            else:
                updated_targets.append(target)

        if changed or self._auto_repair_context is not None:
            self.targets = []
            self.drop_list.clear()
            self.add_targets(updated_targets)
            self.refresh_target_labels()

        if deleted_intermediates:
            self.log_box.appendPlainText(
                f'Cleanup: removed {len(deleted_intermediates)} successfully repaired Topaz intermediate video(s). '
                'Original source videos were not touched.'
            )

        if self._topaz_last_batch is not None and self._auto_repair_context is not None:
            batch = self._topaz_last_batch
            batch['repair_successes'] = repair_successes
            batch['repair_failures'] = repair_failures
            batch['repair_success_count'] = len(repair_successes)
            batch['repair_failure_count'] = len(repair_failures)
            batch['accounted_count'] = (
                batch.get('skipped_existing_count', 0)
                + batch.get('upscale_failure_count', 0)
                + batch.get('repair_success_count', 0)
                + batch.get('repair_failure_count', 0)
            )
            _write_topaz_batch_report(output_root, batch)

            self.log_box.appendPlainText(
                'Batch accounting: '
                f"{batch['accounted_count']} / {batch['total_requested']} original video(s) accounted for "
                f"({batch.get('repair_success_count', 0)} repaired, "
                f"{batch.get('repair_failure_count', 0)} metadata-repair failures, "
                f"{batch.get('upscale_failure_count', 0)} upscale failures, "
                f"{batch.get('skipped_existing_count', 0)} skipped completed)."
            )
            if batch['accounted_count'] != batch['total_requested']:
                QMessageBox.warning(
                    self,
                    'Batch accounting warning',
                    f"Only {batch['accounted_count']} of {batch['total_requested']} original video(s) were accounted for.\n\n"
                    f'Check the activity log and the report file:\n{self._topaz_dir() / "last_topaz_batch_report.txt"}'
                )
            self._set_batch_summary(
                f"Accounted for {batch['accounted_count']} / {batch['total_requested']} • "
                f"{batch.get('repair_success_count', 0)} repaired • "
                f"{batch.get('repair_failure_count', 0)} repair failed • "
                f"{batch.get('upscale_failure_count', 0)} upscale failed • "
                f"{batch.get('skipped_existing_count', 0)} skipped"
            )

        if delete_warnings:
            self.log_box.appendPlainText(
                f'Cleanup warning: {len(delete_warnings)} Topaz intermediate(s) could not be removed.'
            )

        self._auto_repair_context = None
        self._refresh_aux_buttons()

    def on_upscale_failed(self, message):
        self.topaz_run_btn.setEnabled(True)
        self.topaz_stop_btn.setEnabled(False)
        self.topaz_stop_btn.setText('Stop after current')
        self.repair_btn.setEnabled(True)
        self.topaz_status.setText('Failed')
        self.log_box.appendPlainText('Topaz ERROR: ' + message)
        QMessageBox.critical(self, 'Topaz enhancement failed', message)


if __name__ == '__main__':
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName('Metadata Repair Tool')
    qt_app.setStyle('Fusion')
    qt_app.setStyleSheet(core.APP_STYLE)
    qt_app.setWindowIcon(core.make_app_icon())
    win = MainWindow()
    win.show()
    sys.exit(qt_app.exec())
