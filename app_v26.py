"""Metadata Repair Tool v2.6 entry point.

This wrapper leaves the proven v2.5.1 metadata engine in ``app.py`` untouched and
adds an optional image-only AI pixel cleanup stage in front of it.

The cleanup stage shells out to the separately-installed ``remove-ai-watermarks``
CLI. Heavy CUDA / diffusion dependencies therefore stay outside the PyInstaller
bundle, while the normal metadata-only workflow remains exactly as lightweight as
before when the option is disabled.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

import app as core


APP_VERSION = "2.6.0"


def find_ai_scrubber(explicit: str | None = None) -> str:
    """Locate the remove-ai-watermarks launcher without importing its heavy stack."""
    candidates: List[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())

    env_path = os.environ.get("REMOVE_AI_WATERMARKS_EXE", "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())

    which = shutil.which("remove-ai-watermarks") or shutil.which("remove-ai-watermarks.exe")
    if which:
        candidates.append(Path(which))

    # uv's normal per-user tool launcher location on Windows/macOS/Linux.
    candidates.extend([
        Path.home() / ".local" / "bin" / "remove-ai-watermarks.exe",
        Path.home() / ".local" / "bin" / "remove-ai-watermarks",
    ])

    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        candidates.append(Path(appdata) / "uv" / "tools" / "remove-ai-watermarks" / "Scripts" / "remove-ai-watermarks.exe")

    candidates.extend([
        core.app_dir() / "remove-ai-watermarks.exe",
        core.app_dir() / "remove-ai-watermarks",
    ])

    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        if resolved.is_file():
            return str(resolved)
    return ""


def _cleanup_error_message(returncode: int, output: str) -> str:
    text = output or ""
    lower = text.lower()

    if returncode in (-1073741819, 3221225477) or "0xc0000005" in lower:
        return (
            "The AI scrubber crashed inside native GPU code (Windows 0xC0000005). "
            "On this machine, use the SDXL + Z-Image pipeline rather than Qwen + Z-Image."
        )
    if "incomplete metadata, file not fully covered" in lower or "safetensorerror" in lower:
        cache = Path.home() / "models" / "Tongyi-MAI" / "Z-Image-Turbo"
        return (
            "The Z-Image Turbo model cache appears incomplete or corrupted. "
            f"Delete '{cache}' and retry so the model can be downloaded again."
        )
    if "paging file is too small" in lower or "os error 1455" in lower:
        return (
            "Windows ran out of committed memory while loading the diffusion model. "
            "Increase the Windows paging file and retry."
        )
    if "no working cuda backend" in lower or "cuda-only" in lower and "cpu" in lower:
        return (
            "The scrubber can see an NVIDIA GPU but its PyTorch environment does not have a working CUDA backend. "
            "Repair the CUDA PyTorch installation used by remove-ai-watermarks and retry."
        )
    if "not recognized as the name" in lower or "no such file or directory" in lower:
        return "The remove-ai-watermarks executable could not be launched. Re-detect or browse to the executable."

    lines = [line.strip() for line in text.replace("\r", "\n").splitlines() if line.strip()]
    tail = "\n".join(lines[-12:])
    if tail:
        return f"AI cleanup failed with exit code {returncode}.\n\n{tail}"
    return f"AI cleanup failed with exit code {returncode}."


class AICleanupWorker(QThread):
    """Run one reusable-model batch scrub before the normal metadata repair worker."""

    progress = Signal(int, int, str, str)
    log = Signal(str)
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(
        self,
        scrubber: str,
        exiftool: str,
        image_targets: List[str],
        pipeline: str,
        force: bool,
        temp_root: str,
    ):
        super().__init__()
        self.scrubber = scrubber
        self.exiftool = exiftool
        self.image_targets = list(image_targets)
        self.pipeline = pipeline
        self.force = force
        self.temp_root = Path(temp_root)

    def run(self):
        try:
            batch_input = self.temp_root / "batch_input"
            batch_output = self.temp_root / "batch_output"
            cleaned_root = self.temp_root / "cleaned"
            batch_input.mkdir(parents=True, exist_ok=True)
            batch_output.mkdir(parents=True, exist_ok=True)
            cleaned_root.mkdir(parents=True, exist_ok=True)

            staged: Dict[str, dict] = {}
            total = len(self.image_targets)
            for index, source_str in enumerate(self.image_targets):
                source = Path(source_str)
                staged_name = f"{index:04d}__{source.name}"
                staged_path = batch_input / staged_name
                shutil.copy2(source, staged_path)
                staged[staged_name] = {
                    "original": str(source),
                    "fs_times": core.capture_filesystem_times(str(source)),
                    "orientation": core.read_numeric_orientation(self.exiftool, str(source)) or 1,
                    "dimensions": core.read_actual_pixel_dimensions(self.exiftool, str(source)),
                    "index": index,
                }

            command = [
                self.scrubber,
                "-v",
                "batch",
                str(batch_input),
                "-o",
                str(batch_output),
                "--mode",
                "invisible",
                "--pipeline",
                self.pipeline,
                "--cpu-offload",
            ]
            if self.force:
                command.append("--force")

            env = os.environ.copy()

            # Force the child CLI to use UTF-8 when stdout/stderr are redirected into
            # the GUI. Without this, Windows may fall back to cp1252/charmap and crash
            # when DiffSynth/Rich/tqdm prints Unicode progress characters.
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"

            # Keep the model cache in the same location as the user's successful CLI
            # setup instead of downloading tens of GB beside the application EXE.
            env.setdefault("DIFFSYNTH_DOWNLOAD_SOURCE", "HuggingFace")
            cwd = str(Path.home())
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

            self.progress.emit(0, total, "AI cleanup", "Loading diffusion models")
            self.log.emit(f"AI cleanup pipeline: {self.pipeline}")
            self.log.emit(f"AI scrubber: {self.scrubber}")
            self.log.emit(f"AI batch: {total} image(s)")

            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=cwd,
                env=env,
                creationflags=creationflags,
            )

            captured: List[str] = []
            completed_indices = set()
            assert process.stdout is not None
            for raw_line in process.stdout:
                line = raw_line.rstrip("\r\n")
                if not line:
                    continue
                captured.append(line)
                self.log.emit("AI: " + line)

                # Verbose batch progress contains the staged filename. The API guarantees
                # exactly one terminal done/failed event per image.
                match = re.search(r"(\d{4})__.+?:\s+(done|failed)\b", line, flags=re.IGNORECASE)
                if match:
                    idx = int(match.group(1))
                    completed_indices.add(idx)
                    original = Path(self.image_targets[idx]).name if idx < total else "image"
                    self.progress.emit(
                        len(completed_indices), total, original,
                        "AI cleanup complete" if match.group(2).lower() == "done" else "AI cleanup failed"
                    )
                elif "loading" in line.lower():
                    self.progress.emit(len(completed_indices), total, "AI cleanup", line.strip()[:120])

            returncode = process.wait()
            output_text = "\n".join(captured)
            if returncode != 0:
                raise RuntimeError(_cleanup_error_message(returncode, output_text))

            cleaned_map: Dict[str, str] = {}
            for staged_name, info in staged.items():
                generated = batch_output / staged_name
                if not generated.is_file():
                    raise RuntimeError(
                        "The AI scrubber exited successfully but did not create an output for "
                        f"{Path(info['original']).name}."
                    )

                source = Path(info["original"])
                item_dir = cleaned_root / f"{int(info['index']):04d}"
                item_dir.mkdir(parents=True, exist_ok=True)
                clean_path = item_dir / source.name
                shutil.copy2(generated, clean_path)

                clean_dims = core.read_actual_pixel_dimensions(self.exiftool, str(clean_path))
                original_dims = info["dimensions"]
                if original_dims and clean_dims and tuple(clean_dims) != tuple(original_dims):
                    raise RuntimeError(
                        f"AI cleanup changed {source.name} dimensions from "
                        f"{original_dims[0]}x{original_dims[1]} to {clean_dims[0]}x{clean_dims[1]}; repair stopped."
                    )

                orientation = int(info["orientation"])
                if orientation != 1:
                    orient_write = core.run_exiftool(self.exiftool, [
                        "-m", "-P",
                        f"-Orientation#={orientation}",
                        f"-XMP-tiff:Orientation#={orientation}",
                        "-overwrite_original",
                        str(clean_path),
                    ])
                    if orient_write.returncode != 0:
                        raise RuntimeError(
                            f"Could not preserve the original orientation for {source.name}: "
                            + ((orient_write.stderr or orient_write.stdout or "ExifTool failed").strip())
                        )

                core.restore_filesystem_times(str(clean_path), info["fs_times"])
                cleaned_map[str(source)] = str(clean_path)

            self.progress.emit(total, total, "AI cleanup", "Completed")
            self.finished_ok.emit(cleaned_map)
        except Exception as exc:
            self.failed.emit(str(exc))


class EnhancedCompareDialog(core.CompareDialog):
    """Add AI cleanup provenance to the existing verification dialog."""

    def __init__(self, result: dict, parent=None):
        super().__init__(result, parent)
        if result.get("ai_cleanup") != "COMPLETED":
            return

        card = QFrame()
        card.setObjectName("StatCard")
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 10, 14, 10)

        left = QVBoxLayout()
        title = QLabel("AI pixel cleanup")
        title.setObjectName("SectionTitle")
        detail = QLabel(
            f"Completed before metadata repair • {result.get('ai_cleanup_pipeline', 'unknown pipeline')}"
        )
        detail.setObjectName("Small")
        left.addWidget(title)
        left.addWidget(detail)
        row.addLayout(left)
        row.addStretch()

        badge = QLabel("✓ COMPLETED")
        badge.setObjectName("StatusReady")
        row.addWidget(badge)

        root = self.layout()
        if root is not None:
            root.insertWidget(1, card)


class MainWindow(core.MainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Metadata Repair Tool v{APP_VERSION}")
        self.resize(1220, 860)

        self.ai_worker = None
        self._ai_tempdir: tempfile.TemporaryDirectory | None = None
        self._ai_original_targets: List[str] | None = None
        self._ai_reverse_map: Dict[str, str] = {}
        self._ai_log_cache: List[str] = []
        self._ai_pipeline_used = ""
        self._ai_scrubber_path = find_ai_scrubber()

        for label in self.findChildren(QLabel):
            if label.text().startswith("Clone trusted metadata onto damaged images and videos"):
                label.setText(
                    "Repair trusted metadata on images and videos, with optional AI image pixel cleanup before metadata restoration."
                )
                break

        self._install_ai_cleanup_panel()
        self.replace_box.toggled.connect(self._on_replace_mode_changed)

    def _install_ai_cleanup_panel(self):
        ai_card = QFrame()
        ai_card.setObjectName("StatCard")
        outer = QVBoxLayout(ai_card)
        outer.setContentsMargins(13, 10, 13, 10)
        outer.setSpacing(7)

        header = QHBoxLayout()
        title = QLabel("AI image cleanup")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch()
        self.ai_scrubber_status = QLabel()
        self.ai_scrubber_status.setObjectName("StatusReady" if self._ai_scrubber_path else "StatusWarn")
        header.addWidget(self.ai_scrubber_status)
        outer.addLayout(header)

        first = QHBoxLayout()
        self.ai_cleanup_box = QCheckBox("Remove invisible AI watermark before metadata repair")
        self.ai_cleanup_box.setToolTip(
            "Image-only, opt-in pixel regeneration. The cleaned pixels are passed into the normal metadata repair stage."
        )
        first.addWidget(self.ai_cleanup_box, 1)
        self.ai_pipeline = QComboBox()
        self.ai_pipeline.addItem("SDXL + Z-Image (recommended)", "sdxl-zimage")
        self.ai_pipeline.addItem("Qwen + Z-Image (experimental)", "qwen-zimage")
        self.ai_pipeline.setMinimumWidth(235)
        self.ai_pipeline.setStyleSheet(
            "QComboBox { background:#0c1323; border:1px solid #2b3850; border-radius:8px; "
            "padding:7px 9px; color:#e5eaf3; }"
        )
        first.addWidget(self.ai_pipeline)
        outer.addLayout(first)

        second = QHBoxLayout()
        self.ai_force_box = QCheckBox("Force scrub for known AI images")
        self.ai_force_box.setChecked(True)
        self.ai_force_box.setToolTip(
            "Runs pixel regeneration even when no local invisible-watermark proxy is detectable."
        )
        second.addWidget(self.ai_force_box)
        second.addStretch()
        locate_btn = QPushButton("Locate scrubber")
        locate_btn.clicked.connect(self._pick_scrubber)
        second.addWidget(locate_btn)
        outer.addLayout(second)

        self.ai_path_label = QLabel()
        self.ai_path_label.setObjectName("Small")
        self.ai_path_label.setWordWrap(True)
        outer.addWidget(self.ai_path_label)
        self._refresh_ai_scrubber_status()

        # MainWindow's second top-level item is the two-column area; the right-side
        # HeroCard is its second widget. Add the compact image-cleanup panel below the
        # target action row without crowding the already-dense reference/settings column.
        main_layout = self.centralWidget().layout()
        columns = main_layout.itemAt(1).layout() if main_layout and main_layout.count() > 1 else None
        right_card = columns.itemAt(1).widget() if columns and columns.count() > 1 else None
        if right_card is not None and right_card.layout() is not None:
            right_card.layout().addWidget(ai_card)
        else:
            # Defensive fallback for future layout refactors.
            if main_layout is not None:
                main_layout.insertWidget(max(0, main_layout.count() - 2), ai_card)

    def _refresh_ai_scrubber_status(self):
        detected = bool(self._ai_scrubber_path and Path(self._ai_scrubber_path).is_file())
        self.ai_scrubber_status.setText("SCRUBBER READY" if detected else "SCRUBBER NOT FOUND")
        self.ai_scrubber_status.setObjectName("StatusReady" if detected else "StatusWarn")
        self.ai_scrubber_status.style().unpolish(self.ai_scrubber_status)
        self.ai_scrubber_status.style().polish(self.ai_scrubber_status)
        if detected:
            self.ai_path_label.setText(self._ai_scrubber_path)
            self.ai_path_label.setToolTip(self._ai_scrubber_path)
        else:
            self.ai_path_label.setText(
                "Install with uv, or click Locate scrubber. AI cleanup stays optional; normal metadata repair is unaffected."
            )

    def _pick_scrubber(self):
        start = str(Path(self._ai_scrubber_path).parent) if self._ai_scrubber_path else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose remove-ai-watermarks executable",
            start,
            "Executable (*.exe);;All files (*)",
        )
        if path:
            self._ai_scrubber_path = find_ai_scrubber(path) or path
            self._refresh_ai_scrubber_status()

    def _on_replace_mode_changed(self, checked: bool):
        if checked and self.ai_cleanup_box.isChecked():
            self.ai_cleanup_box.setChecked(False)
        self.ai_cleanup_box.setEnabled(not checked)
        if checked:
            self.ai_cleanup_box.setToolTip(
                "AI pixel cleanup is intentionally disabled in Replace originals mode in v2.6. Use Safe copies."
            )
        else:
            self.ai_cleanup_box.setToolTip(
                "Image-only, opt-in pixel regeneration. The cleaned pixels are passed into the normal metadata repair stage."
            )

    def start_repair(self):
        if not self.ai_cleanup_box.isChecked():
            super().start_repair()
            return

        if self.replace_box.isChecked():
            QMessageBox.warning(
                self,
                "Safe copies required",
                "AI image cleanup is only available in Safe copies mode in v2.6. Turn off Replace originals and retry.",
            )
            return

        valid = self.validate()
        if not valid:
            return
        exe, _image_ref, _video_ref = valid

        output = self.output_edit.text().strip()
        if not output:
            QMessageBox.warning(self, "Output required", "Choose an output folder.")
            return

        image_targets = [path for path in self.targets if core.is_image_path(path)]
        if not image_targets:
            QMessageBox.information(
                self,
                "No image targets",
                "AI image cleanup only applies to images. The queued files contain no image targets.",
            )
            return

        self._ai_scrubber_path = find_ai_scrubber(self._ai_scrubber_path)
        self._refresh_ai_scrubber_status()
        if not self._ai_scrubber_path:
            QMessageBox.warning(
                self,
                "AI scrubber not installed",
                "remove-ai-watermarks could not be found.\n\n"
                "Install it first, for example:\n"
                "uv tool install --force \"remove-ai-watermarks[qwen-zimage]\"\n\n"
                "Then click Locate scrubber or restart the app.",
            )
            return

        reply = QMessageBox.question(
            self,
            "Run AI pixel cleanup?",
            f"This will regenerate pixels for {len(image_targets)} image(s) with "
            f"{self.ai_pipeline.currentText()} before the normal metadata repair.\n\n"
            "The original files are not changed. The scrubbed temporary images are deleted after repair.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        self._ai_original_targets = list(self.targets)
        self._ai_reverse_map = {}
        self._ai_log_cache = []
        self._ai_pipeline_used = str(self.ai_pipeline.currentData())
        self._ai_tempdir = tempfile.TemporaryDirectory(prefix="metadatarepair_ai_")

        self.results = []
        self.log_box.clear()
        self.log_box.show()
        self.log_toggle.setText("Hide log")
        self.repair_btn.setEnabled(False)
        self.compare_btn.setEnabled(False)
        self.open_output_btn.setEnabled(False)
        self.progress.setMaximum(len(image_targets))
        self.progress.setValue(0)
        self.run_status.setText("Preparing AI image cleanup…")
        self.result_summary.setText(f"0 / {len(image_targets)} image(s) cleaned")
        self.set_badge(self.top_status, "AI CLEANUP", "warn")

        self.ai_worker = AICleanupWorker(
            self._ai_scrubber_path,
            exe,
            image_targets,
            self._ai_pipeline_used,
            self.ai_force_box.isChecked(),
            self._ai_tempdir.name,
        )
        self.ai_worker.log.connect(self._on_ai_log)
        self.ai_worker.progress.connect(self._on_ai_progress)
        self.ai_worker.finished_ok.connect(self._on_ai_finished)
        self.ai_worker.failed.connect(self._on_ai_failed)
        self.ai_worker.start()

    def _on_ai_log(self, line: str):
        self._ai_log_cache.append(line)
        self.log_box.appendPlainText(line)

    def _on_ai_progress(self, current: int, total: int, source: str, status: str):
        self.progress.setMaximum(total)
        self.progress.setValue(current)
        self.run_status.setText(status if source == "AI cleanup" else f"{status}: {source}")
        self.result_summary.setText(f"{current} / {total} image(s) cleaned")
        self.statusBar().showMessage(f"{source}: {status}")

    def _on_ai_finished(self, cleaned_map: dict):
        if not self._ai_original_targets:
            self._on_ai_failed("Internal error: original target list was lost before metadata repair.")
            return

        self._ai_reverse_map = {cleaned: original for original, cleaned in cleaned_map.items()}
        self.targets = [cleaned_map.get(path, path) for path in self._ai_original_targets]

        prior_worker = self.worker
        super().start_repair()

        # The base method can still be cancelled at one of its confirmation prompts.
        # If no metadata worker was launched, restore the original queue immediately.
        launched = self.worker is not None and self.worker is not prior_worker and self.worker.isRunning()
        if not launched:
            self._restore_original_targets_and_cleanup()
            self.repair_btn.setEnabled(True)
            self.run_status.setText("Repair cancelled")
            self.update_ready_state()
            return

        self.log_box.appendPlainText("")
        self.log_box.appendPlainText(
            f"AI pixel cleanup completed for {len(cleaned_map)} image(s) using {self._ai_pipeline_used}."
        )
        self.log_box.appendPlainText("Temporary scrubbed pixels are now entering the normal metadata repair stage.")

    def _on_ai_failed(self, message: str):
        self._restore_original_targets_and_cleanup()
        self.repair_btn.setEnabled(True)
        self.run_status.setText("AI cleanup failed")
        self.result_summary.setText("Metadata repair was not started")
        self.set_badge(self.top_status, "FAILED", "warn")
        self.log_box.appendPlainText("")
        self.log_box.appendPlainText("AI CLEANUP FAILED: " + message)
        QMessageBox.critical(self, "AI image cleanup failed", message)

    def _restore_original_targets_and_cleanup(self):
        if self._ai_original_targets is not None:
            self.targets = list(self._ai_original_targets)
        self._ai_original_targets = None
        self._ai_reverse_map = {}
        if self._ai_tempdir is not None:
            try:
                self._ai_tempdir.cleanup()
            except Exception:
                pass
            self._ai_tempdir = None
        self.refresh_target_labels()

    def on_finished(self, results):
        used_cleanup = bool(self._ai_original_targets is not None and self._ai_reverse_map)
        if used_cleanup:
            for result in results:
                temp_source = result.get("source", "")
                original = self._ai_reverse_map.get(temp_source)
                if original:
                    result["source"] = original
                    result["ai_cleanup"] = "COMPLETED"
                    result["ai_cleanup_pipeline"] = self._ai_pipeline_used

            original_targets = list(self._ai_original_targets or [])
            self.targets = original_targets

        super().on_finished(results)

        if used_cleanup:
            cleaned_count = sum(1 for item in results if item.get("ai_cleanup") == "COMPLETED")
            self.log_box.appendPlainText(
                f"AI pixel cleanup: {cleaned_count} image(s) completed with {self._ai_pipeline_used} before metadata repair."
            )
            self._restore_original_targets_and_cleanup()

    def on_failed(self, message):
        had_cleanup = self._ai_original_targets is not None
        if had_cleanup:
            self._restore_original_targets_and_cleanup()
        super().on_failed(message)


# Base methods resolve CompareDialog through the app module's globals. Replacing that
# symbol here upgrades verification details without modifying the stable v2.5.1 engine.
core.CompareDialog = EnhancedCompareDialog


def main() -> int:
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("Metadata Repair Tool")
    qt_app.setStyle("Fusion")
    qt_app.setStyleSheet(core.APP_STYLE)
    qt_app.setWindowIcon(core.make_app_icon())
    window = MainWindow()
    window.show()
    return qt_app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
