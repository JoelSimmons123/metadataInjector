import json
import os
import shutil
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
    ffprobe_name = 'ffprobe.exe' if os.name == 'nt' else 'ffprobe'
    ffprobe = Path(ffmpeg_path).with_name(ffprobe_name)
    if ffprobe.exists():
        p = subprocess.run(
            [str(ffprobe), '-v', 'error', '-select_streams', 'v:0',
             '-show_entries', 'stream=width,height', '-of', 'json', source],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding='utf-8', errors='replace', creationflags=_creationflags(),
        )
        if p.returncode == 0:
            try:
                data = json.loads(p.stdout)
                stream = (data.get('streams') or [{}])[0]
                w, h = int(stream['width']), int(stream['height'])
                if w > 0 and h > 0:
                    return w, h
            except Exception:
                pass

    if exiftool and Path(exiftool).exists():
        meta = core.extract_metadata(exiftool, source)
        preferred_pairs = [
            ('QuickTime:ImageWidth', 'QuickTime:ImageHeight'),
            ('Track1:ImageWidth', 'Track1:ImageHeight'),
            ('Composite:ImageWidth', 'Composite:ImageHeight'),
        ]
        for wk, hk in preferred_pairs:
            if wk in meta and hk in meta:
                try:
                    w, h = int(float(meta[wk])), int(float(meta[hk]))
                    if w > 0 and h > 0:
                        return w, h
                except (TypeError, ValueError):
                    pass
        widths, heights = [], []
        for key, value in meta.items():
            local = key.rsplit(':', 1)[-1]
            try:
                numeric = int(float(value))
            except (TypeError, ValueError):
                continue
            if numeric <= 0:
                continue
            if local == 'ImageWidth':
                widths.append(numeric)
            elif local in {'ImageHeight', 'ImageLength'}:
                heights.append(numeric)
        if widths and heights:
            return widths[0], heights[0]
    raise RuntimeError('Could not determine the source video dimensions.')


def _target_dimensions(width: int, height: int, short_side: int) -> tuple[int, int]:
    """Return exact HD/FHD dimensions while preserving portrait vs landscape orientation.

    The earlier implementation preserved the source aspect ratio exactly, which meant a
    nominal 1080p portrait preset could become 1080x1922 (or, because Topaz internally
    chose a 2x scale, 960x1708).  The presets now mean exactly 720x1280 / 1080x1920 for
    portrait and 1280x720 / 1920x1080 for landscape.
    """
    if short_side not in {720, 1080}:
        raise ValueError(f'Unsupported output preset: {short_side}p')
    long_side = 1280 if short_side == 720 else 1920
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
    finished_ok = Signal(list)
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

    def run(self):
        outputs = []
        try:
            output_folder = Path(self.output_dir)
            output_folder.mkdir(parents=True, exist_ok=True)
            env = _topaz_env(self.model_dir)
            use_nvenc = _supports_encoder(self.ffmpeg_path, 'h264_nvenc', self.model_dir)

            for index, source_str in enumerate(self.targets, start=1):
                source = Path(source_str)
                self.progress.emit(index - 1, len(self.targets), source.name, 'Preparing')
                width, height = _probe_video_dimensions(self.ffmpeg_path, str(source), self.exiftool)
                out_w, out_h = _target_dimensions(width, height, self.short_side)

                fps_suffix = '60' if self.to_60fps else 'origfps'
                # Use a persistent app-wide sequence. The counter lives in the user's
                # app-data folder, so moving/deleting finished videos or choosing a new
                # output folder does not reset/reuse vid numbers.
                # Example: vid11_1080_60.mp4 -> metadata repair -> vid11_1080_60_cleaned.mp4
                vid_num = _reserve_video_number()
                destination = output_folder / f'vid{vid_num}_{self.short_side}_{fps_suffix}.mp4'
                # A destination can still already exist (for example after manually
                # copying an older file back into this folder). Reserve another permanent
                # number rather than overwriting it or reusing the number later.
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
                # Topaz may internally choose a model-native scale (for example 2x)
                # instead of the requested final frame size.  Force the final canvas to
                # the exact preset dimensions after AI enhancement.  Keep aspect ratio
                # and use tiny letterbox/pillarbox padding instead of stretching.
                filters.append(
                    f'scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:'
                    'flags=spline,'
                    f'pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2'
                )

                # Explicitly label the filtered video output.  The previous build also
                # mapped 0:v:0 directly, which consumed the only input stream before the
                # Topaz filter graph could use it and caused:
                # "Cannot find an unused video input stream to feed the unlabeled input pad".
                filter_graph = f'[0:v:0]{",".join(filters)}[vout]'
                cmd = [
                    self.ffmpeg_path, '-hide_banner', '-nostdin', '-y', '-stats',
                    '-i', str(source), '-sws_flags', 'spline+accurate_rnd+full_chroma_int',
                    '-filter_complex', filter_graph,
                    '-map', '[vout]', '-map', '0:a?',
                ]
                # Edits/iPhone compatibility: H.264/AVC in an MP4 is substantially
                # less picky than HEVC produced by desktop encoders. Keep 8-bit 4:2:0 and
                # explicitly tag the stream as avc1 for broad Apple/social-app support.
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
                    # Re-encode audio to a conventional social/iPhone delivery format rather
                    # than preserving unusual source rates such as 32 kHz.
                    '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-b:a', '192k',
                    '-map_metadata', '0',
                    # Do not leave Libavcodec/NVENC provenance in the stream tags.
                    '-metadata:s:v:0', 'encoder=',
                    '-metadata:s:a:0', 'encoder=',
                ]
                if self.to_60fps:
                    # The Topaz interpolation filter creates the extra frames; CFR + -r 60
                    # gives the muxer an exact 60/1 timeline instead of ~60.2 FPS.
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

                outputs.append(str(destination))
                self.log.emit(f'✓  {destination.name}')
                self.progress.emit(index, len(self.targets), source.name, 'Complete')

            self.finished_ok.emit(outputs)
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(core.MainWindow):
    def __init__(self):
        super().__init__()
        self.upscale_worker = None
        self._add_topaz_video_card()

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

        # Keep the underlying controls visible so advanced users can tweak the preset.
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

        # Apply the selected output preset only after the frame-interpolation
        # controls exist; otherwise startup calls _toggle_fi_controls() before
        # topaz_fi_combo has been created.
        self._apply_output_preset()

        self.topaz_replace_queue = QCheckBox('Replace queued video entries with enhanced outputs')
        self.topaz_replace_queue.setChecked(True)
        self.topaz_replace_queue.setToolTip(
            'After Topaz finishes, original queued video targets are swapped for the enhanced files. Images stay queued.'
        )
        grid.addWidget(self.topaz_replace_queue, 4, 0, 1, 5)
        layout.addLayout(grid)

        bottom = QHBoxLayout()
        self.topaz_progress = QProgressBar(); self.topaz_progress.setValue(0); bottom.addWidget(self.topaz_progress, 1)
        self.topaz_status = QLabel('Ready'); self.topaz_status.setObjectName('Muted'); bottom.addWidget(self.topaz_status)
        self.topaz_run_btn = QPushButton('Upscale queued videos')
        self.topaz_run_btn.clicked.connect(self.start_video_upscale)
        bottom.addWidget(self.topaz_run_btn)
        layout.addLayout(bottom)

        # Existing main layout: header, file queue, repair card, activity card.
        # Put the Topaz card between the file queue and repair card.
        main.insertWidget(2, card)

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
        # Prefer Proteus Natural when the installed Topaz build actually has it;
        # otherwise use prob-4, which is present in Video AI 7.x.
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

        base_output = self.output_edit.text().strip() or str(core.app_dir() / 'Repaired')
        output_dir = str(Path(base_output) / 'Topaz')
        exiftool = self.exif_edit.text().strip() if hasattr(self, 'exif_edit') else None

        self.topaz_run_btn.setEnabled(False)
        self.repair_btn.setEnabled(False)
        self.topaz_progress.setMaximum(len(videos))
        self.topaz_progress.setValue(0)
        self.topaz_status.setText('Starting…')
        self.log_box.appendPlainText('')
        self.log_box.appendPlainText('Topaz video enhancement')
        self.log_box.appendPlainText(f'Output: {output_dir}')

        self.upscale_worker = VideoUpscaleWorker(
            ffmpeg_path, model_dir, exiftool, videos, output_dir,
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

    def on_upscale_finished(self, outputs):
        self.topaz_progress.setValue(len(outputs))
        self.topaz_status.setText(f'{len(outputs)} complete')
        self.topaz_run_btn.setEnabled(True)
        self.repair_btn.setEnabled(True)

        if self.topaz_replace_queue.isChecked() and outputs:
            image_targets = [x for x in self.targets if not core.is_video_path(x)]
            self.targets = []
            self.drop_list.clear()
            self.add_targets(image_targets + list(outputs))
            self.results.clear()
            self.compare_btn.setEnabled(False)
            self.open_output_btn.setEnabled(False)
            self.log_box.appendPlainText(
                'Queued video targets replaced with Topaz outputs. Metadata repair can now run on the enhanced files.'
            )

        QMessageBox.information(
            self, 'Topaz enhancement complete',
            f'{len(outputs)} video(s) enhanced successfully.\n\n' +
            ('The enhanced files are now queued for metadata repair.'
             if self.topaz_replace_queue.isChecked()
             else 'The enhanced files were saved in the Topaz output folder.')
        )

    def on_upscale_failed(self, message):
        self.topaz_run_btn.setEnabled(True)
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
