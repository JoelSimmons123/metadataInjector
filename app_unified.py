"""Metadata Repair Tool v2.7.1 unified entry point.

Combines:
- app_v26.py: optional image SynthID / invisible-watermark cleanup
- app_plus.py: Topaz Video enhancement + cleaned-output naming

Also auto-loads trusted default reference media from a sibling "good images" folder.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

import app as core
import app_plus as topaz
import app_v26 as ai

APP_VERSION = "2.8.4"

IMAGE_EXTS = set(getattr(core, "IMAGE_EXTS", {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif"
}))
VIDEO_EXTS = set(getattr(core, "VIDEO_EXTS", {
    ".mov", ".mp4", ".m4v", ".avi", ".mkv", ".webm"
}))


def _reference_sort_key(path: Path, kind: str) -> tuple[int, str]:
    ext = path.suffix.lower()
    if kind == "image":
        priority = {
            ".heic": 0,
            ".heif": 1,
            ".jpg": 2,
            ".jpeg": 3,
            ".png": 4,
            ".webp": 5,
            ".tif": 6,
            ".tiff": 7,
            ".avif": 8,
            ".bmp": 9,
        }
    else:
        priority = {
            ".mov": 0,
            ".mp4": 1,
            ".m4v": 2,
            ".avi": 3,
            ".mkv": 4,
            ".webm": 5,
        }
    return priority.get(ext, 99), path.name.lower()


class MainWindow(ai.MainWindow, topaz.MainWindow):
    """One window containing metadata repair, optional AI image cleanup, and Topaz tools."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Metadata Repair Tool v{APP_VERSION}")
        self._autoload_good_references()

    def _autoload_good_references(self):
        folder = core.app_dir() / "good images"
        if not folder.is_dir():
            return

        try:
            files = [p for p in folder.iterdir() if p.is_file()]
        except OSError:
            return

        image_candidates = sorted(
            (p for p in files if p.suffix.lower() in IMAGE_EXTS),
            key=lambda p: _reference_sort_key(p, "image"),
        )
        video_candidates = sorted(
            (p for p in files if p.suffix.lower() in VIDEO_EXTS),
            key=lambda p: _reference_sort_key(p, "video"),
        )

        loaded = []

        if image_candidates and not self.reference_for_kind("image"):
            path = image_candidates[0]
            self._image_reference_path = str(path)
            self.image_ref_name.setText(path.name)
            self.image_ref_name.setToolTip(str(path))

            preview = core.load_preview(str(path), self.image_ref_preview.size())
            if preview:
                self.image_ref_preview.setPixmap(preview)
                self.image_ref_preview.setText("")
            else:
                self.image_ref_preview.setPixmap(QPixmap())
                self.image_ref_preview.setText("Preview unavailable\n(metadata can still be repaired)")

            self.update_reference_metadata("image")
            loaded.append(f"image: {path.name}")

        if video_candidates and not self.reference_for_kind("video"):
            path = video_candidates[0]
            self._video_reference_path = str(path)
            self.video_ref_name.setText(path.name)
            self.video_ref_name.setToolTip(str(path))
            self.video_ref_preview.setPixmap(QPixmap())
            self.video_ref_preview.setText("Video reference selected\npreview unavailable")
            self.update_reference_metadata("video")
            loaded.append(f"video: {path.name}")

        if loaded:
            self.refresh_target_labels()
            self.update_ready_state()
            self.statusBar().showMessage(
                "Auto-loaded trusted reference " + ("media" if len(loaded) > 1 else "file")
                + " from 'good images': " + " • ".join(loaded),
                10000,
            )


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
