from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

import app as core
import app_plus as topaz
import captioning


CAPTION_CONFIG = {
    "enabled": True,
    "model": "distil-large-v3",
    "device": "Auto",
    "language": "en",
    "font": "Arial",
    "words_per_caption": 4,
    "max_chars": 30,
    "highlight": True,
    "uppercase": False,
}


def _probe_audio_codec(source: str) -> str:
    try:
        _ffmpeg, ffprobe = core.find_ffmpeg()
        proc = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=codec_name",
                "-of", "json",
                source,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=topaz._creationflags(),
        )
        if proc.returncode != 0:
            return ""
        streams = json.loads(proc.stdout or "{}").get("streams", [])
        return str(streams[0].get("codec_name", "")).lower() if streams else ""
    except Exception:
        return ""


def _safe_unlink(path: Path | None) -> None:
    if not path:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass




def _unique_caption_output(folder: Path, source: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / f"{source.stem}_captioned.mp4"
    if not candidate.exists():
        return candidate
    n = 2
    while True:
        candidate = folder / f"{source.stem}_captioned_{n}.mp4"
        if not candidate.exists():
            return candidate
        n += 1


class CaptionOnlyWorker(QThread):
    """Caption existing finished videos without running Topaz.

    The result is an intermediate MP4 that immediately feeds the normal metadata
    repair worker. Audio is stream-copied; only the video stream is re-encoded.
    """

    progress = Signal(int, int, str, str)
    log = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, targets: list[str], output_dir: str, config: dict):
        super().__init__()
        self.targets = list(targets)
        self.output_dir = output_dir
        self.config = dict(config)

    def run(self):
        successes = []
        failures = []
        try:
            ffmpeg, _ffprobe = core.find_ffmpeg()
            if not captioning.filter_available(ffmpeg, "ass"):
                raise RuntimeError(
                    "The normal FFmpeg build does not expose the ASS/libass subtitle filter. "
                    "Install a full FFmpeg build or place ffmpeg.exe/ffprobe.exe beside the app."
                )

            output_folder = Path(self.output_dir)
            output_folder.mkdir(parents=True, exist_ok=True)
            transcriber = captioning.Transcriber(
                model_name=str(self.config.get("model") or "distil-large-v3"),
                device_pref=str(self.config.get("device") or "Auto"),
                language=str(self.config.get("language") or "en"),
                log=self.log.emit,
            )

            for index, source_text in enumerate(self.targets, start=1):
                source = Path(source_text)
                destination = None
                ass_path = None
                self.progress.emit(index - 1, len(self.targets), source.name, "Transcribing captions")
                try:
                    width, height = topaz._probe_video_dimensions(ffmpeg, str(source), None)
                    self.log.emit(f"CC  {source.name}: transcribing captions…")
                    words = transcriber.transcribe(source)

                    if not words:
                        # Keep a real intermediate so the normal metadata workflow can
                        # treat this batch exactly like a captioned one. No media changes.
                        destination = _unique_caption_output(output_folder, source)
                        shutil.copy2(source, destination)
                        self.log.emit(f"CC  {source.name}: no speech detected; media copied unchanged for metadata repair.")
                        mode = "no-speech-copy"
                    else:
                        destination = _unique_caption_output(output_folder, source)
                        ass_path = output_folder / f".{destination.stem}.ass"
                        captioning.make_ass(
                            words,
                            width,
                            height,
                            ass_path,
                            font=str(self.config.get("font") or "Arial"),
                            words_per_caption=int(self.config.get("words_per_caption") or 4),
                            max_chars=int(self.config.get("max_chars") or 30),
                            highlight=bool(self.config.get("highlight", True)),
                            uppercase=bool(self.config.get("uppercase", False)),
                        )
                        self.progress.emit(index - 1, len(self.targets), source.name, "Burning captions")
                        self.log.emit(
                            f"CC  {source.name}: burning captions at CQ10 high quality; audio is stream-copied bit-for-bit."
                        )
                        captioning.burn_subtitles_high_quality(
                            ffmpeg,
                            source,
                            ass_path,
                            destination,
                            self.log.emit,
                        )
                        out_w, out_h = topaz._probe_video_dimensions(ffmpeg, str(destination), None)
                        if (out_w, out_h) != (width, height):
                            _safe_unlink(destination)
                            raise RuntimeError(
                                f"Caption output size changed: expected {width}×{height}, got {out_w}×{out_h}."
                            )
                        mode = "hq-cq10"

                    successes.append({
                        "source": str(source),
                        "output": str(destination),
                        "original_name": source.name,
                        "caption_mode": mode,
                    })
                    self.log.emit(f"✓  {source.name}  →  {destination.name}")
                    self.progress.emit(index, len(self.targets), source.name, "Captioned")
                except Exception as exc:
                    _safe_unlink(destination)
                    failures.append({"source": str(source), "error": str(exc)})
                    self.log.emit(f"✗  {source.name} — captioning failed")
                    for line in str(exc).splitlines():
                        self.log.emit(f"   {line}")
                    self.progress.emit(index, len(self.targets), source.name, "Failed")
                finally:
                    _safe_unlink(ass_path)

            self.finished_ok.emit({"successes": successes, "failures": failures})
        except Exception as exc:
            self.failed.emit(str(exc))


class CaptionedVideoUpscaleWorker(topaz.VideoUpscaleWorker):
    """Topaz worker with optional Whisper captions and higher-quality encoding.

    Best case: the Topaz FFmpeg exposes libass, so captions are part of the same
    filter graph and there is only one video encode.

    Fallback: Topaz renders first, then standard FFmpeg performs a deliberately
    very-high-quality CQ10 caption pass while stream-copying audio.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.caption_config = dict(CAPTION_CONFIG)

    def run(self):
        successes = []
        failures = []
        try:
            output_folder = Path(self.output_dir)
            output_folder.mkdir(parents=True, exist_ok=True)
            env = topaz._topaz_env(self.model_dir)
            use_nvenc = topaz._supports_encoder(self.ffmpeg_path, "h264_nvenc", self.model_dir)

            captions_enabled = bool(self.caption_config.get("enabled"))
            transcriber = None
            topaz_has_ass = False
            fallback_ffmpeg = None
            caption_temp = output_folder / ".caption-temp"

            if captions_enabled:
                transcriber = captioning.Transcriber(
                    model_name=str(self.caption_config.get("model") or "distil-large-v3"),
                    device_pref=str(self.caption_config.get("device") or "Auto"),
                    language=str(self.caption_config.get("language") or "en"),
                    log=self.log.emit,
                )
                topaz_has_ass = captioning.filter_available(self.ffmpeg_path, "ass", env=env)
                if topaz_has_ass:
                    self.log.emit("Captions: Topaz FFmpeg supports libass — captions will be burned in the SAME encode.")
                else:
                    try:
                        fallback_ffmpeg, _ffprobe = core.find_ffmpeg()
                    except Exception as exc:
                        raise RuntimeError(
                            "Captioning is enabled, Topaz FFmpeg has no ASS filter, and a normal FFmpeg build was not found. "
                            "Put ffmpeg.exe/ffprobe.exe beside the app or on PATH."
                        ) from exc
                    self.log.emit(
                        "Captions: Topaz FFmpeg has no libass filter — using HQ CQ10 NVENC fallback pass with audio stream-copy."
                    )
                caption_temp.mkdir(parents=True, exist_ok=True)

            for index, source_str in enumerate(self.targets, start=1):
                if self.stop_requested:
                    self.log.emit("Stop requested. No new videos will be started.")
                    break

                source = Path(source_str)
                destination = None
                working_destination = None
                ass_path = None
                self.progress.emit(index - 1, len(self.targets), source.name, "Preparing")

                try:
                    width, height = topaz._probe_video_dimensions(
                        self.ffmpeg_path, str(source), self.exiftool
                    )
                    out_w, out_h = topaz._target_dimensions(width, height, self.short_side)

                    fps_suffix = "60" if self.to_60fps else "origfps"
                    vid_num = topaz._reserve_video_number()
                    destination = output_folder / f"vid{vid_num}_{self.short_side}_{fps_suffix}.mp4"
                    while destination.exists():
                        vid_num = topaz._reserve_video_number()
                        destination = output_folder / f"vid{vid_num}_{self.short_side}_{fps_suffix}.mp4"

                    caption_words = []
                    if captions_enabled and transcriber is not None:
                        self.progress.emit(index - 1, len(self.targets), source.name, "Transcribing captions")
                        self.log.emit(f"CC  {source.name}: transcribing captions…")
                        caption_words = transcriber.transcribe(source)
                        if caption_words:
                            ass_path = caption_temp / f"{destination.stem}.ass"
                            captioning.make_ass(
                                caption_words,
                                out_w,
                                out_h,
                                ass_path,
                                font=str(self.caption_config.get("font") or "Arial"),
                                words_per_caption=int(self.caption_config.get("words_per_caption") or 4),
                                max_chars=int(self.caption_config.get("max_chars") or 30),
                                highlight=bool(self.caption_config.get("highlight", True)),
                                uppercase=bool(self.caption_config.get("uppercase", False)),
                            )
                            self.log.emit(f"CC  {source.name}: {len(caption_words)} timed word(s) ready.")
                        else:
                            self.log.emit(f"CC  {source.name}: no speech detected; continuing without captions.")

                    same_pass_captions = bool(ass_path and topaz_has_ass)
                    needs_fallback_caption_pass = bool(ass_path and not topaz_has_ass)
                    working_destination = (
                        destination.with_name(f".{destination.stem}_topaz_uncaptioned.mp4")
                        if needs_fallback_caption_pass
                        else destination
                    )

                    filters = []
                    if self.to_60fps:
                        rdt = "0.01" if self.replace_duplicates else "-0.000001"
                        filters.append(
                            f"tvai_fi=model={self.fi_model}:slowmo=1:rdt={rdt}:fps=60:"
                            "device=-2:vram=1:instances=1"
                        )
                    filters.append(
                        f"tvai_up=model={self.enhance_model}:scale=0:w={out_w}:h={out_h}:"
                        "preblur=0:noise=0:details=0:halo=0:blur=0:compression=0:"
                        "estimate=8:blend=0.2:device=-2:vram=1:instances=1"
                    )
                    filters.append(
                        f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:"
                        "flags=spline,"
                        f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2"
                    )
                    if same_pass_captions:
                        filters.append(f"ass='{captioning.ffmpeg_filter_path(ass_path)}'")

                    filter_graph = f"[0:v:0]{','.join(filters)}[vout]"
                    cmd = [
                        self.ffmpeg_path,
                        "-hide_banner",
                        "-nostdin",
                        "-y",
                        "-stats",
                        "-i", str(source),
                        "-sws_flags", "spline+accurate_rnd+full_chroma_int",
                        "-filter_complex", filter_graph,
                        "-map", "[vout]",
                        "-map", "0:a?",
                    ]

                    if use_nvenc:
                        cmd += [
                            "-c:v", "h264_nvenc",
                            "-profile:v", "high",
                            "-level:v", "4.2",
                            "-preset", "p7",
                            "-tune", "hq",
                            "-rc", "vbr",
                            "-cq", "14",
                            "-b:v", "0",
                            "-pix_fmt", "yuv420p",
                            "-tag:v", "avc1",
                        ]
                    else:
                        cmd += [
                            "-c:v", "libx264",
                            "-profile:v", "high",
                            "-level:v", "4.2",
                            "-preset", "slow",
                            "-crf", "14",
                            "-pix_fmt", "yuv420p",
                            "-tag:v", "avc1",
                        ]

                    audio_codec = _probe_audio_codec(str(source))
                    if audio_codec == "aac":
                        cmd += ["-c:a", "copy"]
                        audio_note = "AAC copied bit-for-bit"
                    else:
                        cmd += ["-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "256k"]
                        audio_note = f"audio converted from {audio_codec or 'unknown codec'}"

                    cmd += [
                        "-map_metadata", "0",
                        "-metadata:s:v:0", "encoder=",
                        "-metadata:s:a:0", "encoder=",
                    ]
                    if self.to_60fps:
                        cmd += ["-fps_mode:v", "cfr", "-r", "60"]
                    else:
                        cmd += ["-fps_mode:v", "passthrough"]
                    cmd += ["-movflags", "+faststart", str(working_destination)]

                    caption_note = ""
                    if same_pass_captions:
                        caption_note = ", captions same-pass"
                    elif needs_fallback_caption_pass:
                        caption_note = ", captions HQ fallback"

                    self.log.emit(
                        f"▶  {source.name}  →  {destination.name}  "
                        f"[{width}×{height} → {out_w}×{out_h}"
                        + (", 60 FPS" if self.to_60fps else "")
                        + f", CQ14{caption_note}, {audio_note}]"
                    )
                    self.progress.emit(index - 1, len(self.targets), source.name, "Topaz processing")
                    proc = subprocess.run(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=env,
                        creationflags=topaz._creationflags(),
                    )
                    if proc.returncode != 0:
                        _safe_unlink(working_destination)
                        tail = "\n".join((proc.stdout or "").splitlines()[-35:])
                        command_text = subprocess.list2cmdline(cmd) if os.name == "nt" else " ".join(cmd)
                        raise RuntimeError(
                            f"Topaz failed for {source.name}.\n\nCommand:\n{command_text}\n\n"
                            f"{tail or 'ffmpeg returned an error.'}"
                        )

                    actual_w, actual_h = topaz._probe_video_dimensions(
                        self.ffmpeg_path, str(working_destination), self.exiftool
                    )
                    if (actual_w, actual_h) != (out_w, out_h):
                        _safe_unlink(working_destination)
                        raise RuntimeError(
                            f"Topaz output orientation/size changed: expected {out_w}×{out_h}, "
                            f"got {actual_w}×{actual_h}. The incorrect output was deleted."
                        )

                    if needs_fallback_caption_pass:
                        self.progress.emit(index - 1, len(self.targets), source.name, "Burning captions")
                        self.log.emit(
                            f"CC  {source.name}: burning captions with CQ10 high-quality fallback; audio remains stream-copied."
                        )
                        captioning.burn_subtitles_high_quality(
                            fallback_ffmpeg,
                            working_destination,
                            ass_path,
                            destination,
                            self.log.emit,
                        )
                        final_w, final_h = topaz._probe_video_dimensions(
                            fallback_ffmpeg, str(destination), self.exiftool
                        )
                        if (final_w, final_h) != (out_w, out_h):
                            _safe_unlink(destination)
                            raise RuntimeError(
                                f"Caption output size changed: expected {out_w}×{out_h}, got {final_w}×{final_h}."
                            )
                        _safe_unlink(working_destination)
                        working_destination = destination

                    successes.append({
                        "source": str(source),
                        "output": str(destination),
                        "original_name": source.name,
                        "output_name": destination.name,
                        "captioned": bool(ass_path),
                        "caption_mode": (
                            "same-pass" if same_pass_captions
                            else "hq-fallback" if needs_fallback_caption_pass
                            else "no-speech" if captions_enabled
                            else "disabled"
                        ),
                    })
                    self.log.emit(f"✓  {source.name}  →  {destination.name}")
                    self.progress.emit(index, len(self.targets), source.name, "Complete")
                except Exception as exc:
                    _safe_unlink(destination)
                    if working_destination and working_destination != destination:
                        _safe_unlink(working_destination)

                    message = str(exc)
                    failed_folder = output_folder / "failed upscale"
                    failed_copy = ""
                    copy_error = ""
                    try:
                        failed_folder.mkdir(parents=True, exist_ok=True)
                        target = topaz._failed_copy_path(failed_folder, source)
                        shutil.copy2(source, target)
                        failed_copy = str(target)
                    except Exception as copy_exc:
                        copy_error = str(copy_exc)

                    failures.append({
                        "source": str(source),
                        "original_name": source.name,
                        "failed_copy": failed_copy,
                        "copy_error": copy_error,
                        "error": message,
                    })
                    self.log.emit(f"✗  {source.name}  —  FAILED")
                    if failed_copy:
                        self.log.emit(f"   Copied failed source to: {failed_copy}")
                    elif copy_error:
                        self.log.emit(f"   WARNING: could not copy failed source: {copy_error}")
                    for line in message.splitlines():
                        self.log.emit(f"   {line}")
                    self.progress.emit(index, len(self.targets), source.name, "Failed")
                finally:
                    _safe_unlink(ass_path)

                if self.stop_requested:
                    self.log.emit("Stop requested. Halting after current video.")
                    break

            try:
                if captions_enabled and caption_temp.exists() and not any(caption_temp.iterdir()):
                    caption_temp.rmdir()
            except OSError:
                pass

            self.finished_ok.emit({
                "successes": successes,
                "failures": failures,
                "stopped": bool(self.stop_requested),
                "attempted": len(successes) + len(failures),
                "total": len(self.targets),
            })
        except Exception as exc:
            self.failed.emit(str(exc))


# app_plus.MainWindow.start_video_upscale resolves this name from the app_plus
# module when the button is clicked, so replacing it here keeps the existing
# queue/manifest/retry/cleanup workflow intact.
topaz.VideoUpscaleWorker = CaptionedVideoUpscaleWorker


class CaptionMixin:
    """Caption controls for both Topaz and already-finished video workflows."""

    def __init__(self):
        super().__init__()
        self.caption_only_worker = None
        self._caption_direct_original_targets = None
        self._caption_direct_map = {}
        self._caption_direct_output_dir = None
        self._add_caption_card()

    def _current_caption_config(self) -> dict:
        return {
            "enabled": bool(self.caption_enabled_box.isChecked()),
            "model": self.caption_model_combo.currentText().strip() or "distil-large-v3",
            "device": "Auto",
            "language": "en",
            "font": self.caption_font_edit.text().strip() or "Arial",
            "words_per_caption": int(self.caption_words_combo.currentData() or 4),
            "max_chars": 30,
            "highlight": bool(self.caption_highlight_box.isChecked()),
            "uppercase": bool(self.caption_uppercase_box.isChecked()),
        }

    def _add_caption_card(self):
        main = self.centralWidget().layout()
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(17, 14, 17, 14)
        layout.setSpacing(9)

        title = QLabel("Automatic video captions")
        title.setObjectName("SectionTitle")
        subtitle = QLabel(
            "Two independent paths: Topaz → captions → metadata repair, OR captions → metadata repair "
            "for videos that are already upscaled/ready. The captions-only path never runs Topaz."
        )
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(7)

        self.caption_enabled_box = QCheckBox("Burn captions on Topaz video outputs")
        self.caption_enabled_box.setChecked(True)
        grid.addWidget(self.caption_enabled_box, 0, 0, 1, 5)

        self.caption_direct_box = QCheckBox("Caption videos before normal Process / metadata repair (NO UPSCALE)")
        self.caption_direct_box.setChecked(False)
        self.caption_direct_box.setToolTip(
            "Use this for videos that are already final resolution/FPS. Clicking the normal Process button will "
            "burn captions first, then run the existing metadata repair. Topaz is not launched."
        )
        grid.addWidget(self.caption_direct_box, 1, 0, 1, 5)

        grid.addWidget(QLabel("Whisper model"), 2, 0)
        self.caption_model_combo = QComboBox()
        self.caption_model_combo.addItems([
            "distil-large-v3",
            "large-v3",
            "medium.en",
            "small.en",
        ])
        self.caption_model_combo.setCurrentText("distil-large-v3")
        grid.addWidget(self.caption_model_combo, 2, 1)

        grid.addWidget(QLabel("Font"), 2, 2)
        self.caption_font_edit = QLineEdit("Arial")
        grid.addWidget(self.caption_font_edit, 2, 3)

        grid.addWidget(QLabel("Words / caption"), 3, 0)
        self.caption_words_combo = QComboBox()
        for n in (2, 3, 4, 5, 6):
            self.caption_words_combo.addItem(str(n), n)
        self.caption_words_combo.setCurrentText("4")
        grid.addWidget(self.caption_words_combo, 3, 1)

        self.caption_highlight_box = QCheckBox("Highlight current word")
        self.caption_highlight_box.setChecked(True)
        grid.addWidget(self.caption_highlight_box, 3, 2, 1, 2)

        self.caption_uppercase_box = QCheckBox("UPPERCASE")
        self.caption_uppercase_box.setChecked(False)
        grid.addWidget(self.caption_uppercase_box, 3, 4)

        note = QLabel(
            "GPU preferred automatically (CUDA float16). Captions-only uses CQ10 NVENC and copies AAC audio "
            "without re-encoding. Metadata repair always runs after the captions are burned."
        )
        note.setObjectName("Small")
        note.setWordWrap(True)
        grid.addWidget(note, 4, 0, 1, 5)

        layout.addLayout(grid)
        main.insertWidget(3, card)

    def start_video_upscale(self):
        global CAPTION_CONFIG
        CAPTION_CONFIG = self._current_caption_config()
        return super().start_video_upscale()

    def start_repair(self):
        """Normal Process button: optionally caption video targets first, without Topaz."""
        if not self.caption_direct_box.isChecked():
            return super().start_repair()

        if self.replace_box.isChecked():
            QMessageBox.warning(
                self,
                "Safe copies required",
                "Captions-only processing is intentionally available in Safe copies mode only. "
                "Turn off Replace originals, choose an output folder, and retry. Your existing finished videos will not be overwritten."
            )
            return

        if self.caption_only_worker and self.caption_only_worker.isRunning():
            return

        videos = [path for path in self.targets if core.is_video_path(path)]
        if not videos:
            return super().start_repair()

        # Validate references / ExifTool before spending time transcribing and encoding.
        if not self.validate():
            return
        output_text = self.output_edit.text().strip() if hasattr(self, "output_edit") else ""
        if not output_text:
            QMessageBox.warning(self, "Output required", "Choose an output folder first.")
            return

        self._caption_direct_original_targets = list(self.targets)
        self._caption_direct_map = {}
        self._caption_direct_output_dir = str(Path(output_text) / "Captioned")

        self.results = []
        self.log_box.clear()
        self.log_box.show()
        self.log_toggle.setText("Hide log")
        self.repair_btn.setEnabled(False)
        if hasattr(self, "topaz_run_btn"):
            self.topaz_run_btn.setEnabled(False)
        self.progress.setMaximum(len(videos))
        self.progress.setValue(0)
        self.run_status.setText("Preparing captions…")
        self.result_summary.setText(f"0 / {len(videos)} video(s) captioned")
        self.set_badge(self.top_status, "CAPTIONING", "warn")
        self.log_box.appendPlainText("Captions-only workflow: NO UPSCALE will run.")
        self.log_box.appendPlainText(f"Caption intermediates: {self._caption_direct_output_dir}")

        config = self._current_caption_config()
        config["enabled"] = True
        self.caption_only_worker = CaptionOnlyWorker(videos, self._caption_direct_output_dir, config)
        self.caption_only_worker.log.connect(self.log_box.appendPlainText)
        self.caption_only_worker.progress.connect(self._on_caption_direct_progress)
        self.caption_only_worker.finished_ok.connect(self._on_caption_direct_finished)
        self.caption_only_worker.failed.connect(self._on_caption_direct_failed)
        self.caption_only_worker.start()

    def _on_caption_direct_progress(self, current, total, source, status):
        self.progress.setMaximum(total)
        self.progress.setValue(current)
        self.run_status.setText(f"{source}: {status}")
        self.result_summary.setText(f"{current} / {total} video(s) captioned")
        self.statusBar().showMessage(f"{source}: {status}")

    def _restore_direct_queue(self):
        if self._caption_direct_original_targets is None:
            return
        originals = list(self._caption_direct_original_targets)
        self.targets = []
        self.drop_list.clear()
        self.add_targets(originals)
        self.refresh_target_labels()

    def _clear_direct_state(self):
        self._caption_direct_original_targets = None
        self._caption_direct_map = {}
        self._caption_direct_output_dir = None
        self.caption_only_worker = None

    def _on_caption_direct_finished(self, summary):
        successes = list(summary.get("successes", []))
        failures = list(summary.get("failures", []))
        if failures:
            self._restore_direct_queue()
            self.repair_btn.setEnabled(True)
            if hasattr(self, "topaz_run_btn"):
                self.topaz_run_btn.setEnabled(True)
            detail = "\n\n".join(
                f"{Path(item.get('source', '')).name}: {item.get('error', '')}" for item in failures[:8]
            )
            self._clear_direct_state()
            QMessageBox.critical(
                self,
                "Captioning failed",
                f"Captioning failed for {len(failures)} video(s), so metadata repair was NOT started.\n\n{detail}"
            )
            return

        mapping = {item["source"]: item["output"] for item in successes}
        self._caption_direct_map = {
            topaz._norm_key(item["output"]): item["source"] for item in successes
        }
        original_targets = list(self._caption_direct_original_targets or self.targets)
        replaced = [mapping.get(path, path) for path in original_targets]
        self.targets = []
        self.drop_list.clear()
        self.add_targets(replaced)
        self.refresh_target_labels()

        self.log_box.appendPlainText("")
        self.log_box.appendPlainText(
            f"Captioning complete for {len(successes)} video(s). Starting normal metadata repair now."
        )
        prior_worker = self.worker
        super().start_repair()
        launched = self.worker is not None and self.worker is not prior_worker and self.worker.isRunning()
        if not launched:
            self._restore_direct_queue()
            self.repair_btn.setEnabled(True)
            if hasattr(self, "topaz_run_btn"):
                self.topaz_run_btn.setEnabled(True)
            self._clear_direct_state()

    def _on_caption_direct_failed(self, message):
        self._restore_direct_queue()
        self.repair_btn.setEnabled(True)
        if hasattr(self, "topaz_run_btn"):
            self.topaz_run_btn.setEnabled(True)
        self.run_status.setText("Captioning failed")
        self.log_box.appendPlainText("CAPTIONING ERROR: " + message)
        self._clear_direct_state()
        QMessageBox.critical(self, "Captioning failed", message)

    def on_finished(self, results):
        """Clean direct-caption intermediates only after metadata repair has finished."""
        direct_active = bool(self._caption_direct_original_targets is not None and self._caption_direct_map)
        if direct_active:
            failed_dir = Path(self._caption_direct_output_dir or (core.app_dir() / "Repaired")) / "failed metadata repair"
            failed_dir.mkdir(parents=True, exist_ok=True)

            for result in results:
                source_text = result.get("source") or ""
                key = topaz._norm_key(source_text)
                original = self._caption_direct_map.get(key)
                if not original:
                    continue
                intermediate = Path(source_text)
                result["caption_original_source"] = original
                result["caption_only"] = True

                if result.get("status") != "ERROR" and result.get("output") and Path(result["output"]).is_file():
                    try:
                        intermediate.unlink(missing_ok=True)
                        self.log_box.appendPlainText(
                            f"🧹  Deleted captions-only intermediate after successful metadata repair: {intermediate.name}"
                        )
                    except OSError as exc:
                        self.log_box.appendPlainText(f"WARNING: could not delete {intermediate.name}: {exc}")
                else:
                    try:
                        preserved = topaz._failed_copy_path(failed_dir, intermediate)
                        shutil.move(str(intermediate), str(preserved))
                        result["caption_intermediate_preserved"] = str(preserved)
                        self.log_box.appendPlainText(
                            f"⚠  Metadata repair failed; preserved captioned intermediate: {preserved}"
                        )
                    except Exception as exc:
                        self.log_box.appendPlainText(
                            f"WARNING: could not preserve failed captioned intermediate {intermediate.name}: {exc}"
                        )

                # Present/report the actual user source rather than the temporary MP4.
                result["source"] = original

        super().on_finished(results)

        if direct_active:
            self._restore_direct_queue()
            self.repair_btn.setEnabled(True)
            if hasattr(self, "topaz_run_btn"):
                self.topaz_run_btn.setEnabled(True)
            self._clear_direct_state()
