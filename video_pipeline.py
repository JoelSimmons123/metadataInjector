from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

import app as core


EXTRA_UI_STYLE = r"""
QWidget {
    font-size: 11.5pt;
}
QLabel#Title {
    font-size: 27pt;
}
QLabel#Subtitle {
    font-size: 11.5pt;
}
QLabel#SectionTitle {
    font-size: 13pt;
}
QLabel#Small,
QLabel#StatLabel {
    font-size: 10pt;
}
QPushButton {
    padding: 9px 14px;
}
QPushButton#Primary {
    padding: 12px 20px;
}
"""


class VideoPipelineMixin:
    """Simple stage-based front end for the existing video workers.

    This class changes orchestration/UI only. The proven Topaz, Whisper caption,
    metadata repair, retry, verification and cleanup implementations remain in
    their existing modules.
    """

    def __init__(self):
        super().__init__()
        self._pipeline_skip_metadata_after_direct_caption = False
        self._install_simple_video_pipeline()
        self._apply_readability_tweaks()

    def _apply_readability_tweaks(self):
        app = QApplication.instance()
        if app is not None and EXTRA_UI_STYLE not in app.styleSheet():
            app.setStyleSheet(app.styleSheet() + "\n" + EXTRA_UI_STYLE)

        # Recover useful vertical space without changing the underlying base UI.
        if hasattr(self, "drop_list"):
            self.drop_list.setMinimumHeight(220)

        main = self.centralWidget().layout() if self.centralWidget() else None
        if main is not None:
            main.setContentsMargins(20, 16, 20, 14)
            main.setSpacing(12)

    def _install_simple_video_pipeline(self):
        main = self.centralWidget().layout()

        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(17, 14, 17, 14)
        layout.setSpacing(10)

        title = QLabel("Video pipeline")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "Choose the stages you want. Queued videos run left-to-right: "
            "Upscale → Caption → Metadata repair."
        )
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        stages = QHBoxLayout()
        stages.setSpacing(18)

        self.pipeline_upscale_box = QCheckBox("1. Upscale")
        self.pipeline_upscale_box.setChecked(True)
        self.pipeline_upscale_box.setToolTip(
            "Runs the existing Topaz enhancement / optional 60 FPS stage."
        )
        stages.addWidget(self.pipeline_upscale_box)

        arrow1 = QLabel("→")
        arrow1.setObjectName("Muted")
        stages.addWidget(arrow1)

        self.pipeline_caption_box = QCheckBox("2. Caption")
        self.pipeline_caption_box.setChecked(True)
        self.pipeline_caption_box.setToolTip(
            "Runs local GPU Whisper transcription and burns the short-form captions."
        )
        stages.addWidget(self.pipeline_caption_box)

        arrow2 = QLabel("→")
        arrow2.setObjectName("Muted")
        stages.addWidget(arrow2)

        self.pipeline_metadata_box = QCheckBox("3. Metadata repair")
        self.pipeline_metadata_box.setChecked(True)
        self.pipeline_metadata_box.setToolTip(
            "Runs the existing final QuickTime/iPhone metadata repair stage."
        )
        stages.addWidget(self.pipeline_metadata_box)

        stages.addStretch()
        layout.addLayout(stages)

        examples = QLabel(
            "Fresh/raw: all 3 on   •   Already upscaled: Caption + Metadata   •   "
            "Already captioned: Metadata only"
        )
        examples.setObjectName("Small")
        examples.setWordWrap(True)
        layout.addWidget(examples)

        advanced_row = QHBoxLayout()
        advanced_label = QLabel("Advanced settings")
        advanced_label.setObjectName("Small")
        advanced_row.addWidget(advanced_label)

        self.pipeline_topaz_settings_btn = QPushButton("Topaz settings")
        self.pipeline_topaz_settings_btn.setCheckable(True)
        self.pipeline_topaz_settings_btn.clicked.connect(
            lambda checked: self._toggle_pipeline_advanced("topaz", checked)
        )
        advanced_row.addWidget(self.pipeline_topaz_settings_btn)

        self.pipeline_caption_settings_btn = QPushButton("Caption settings")
        self.pipeline_caption_settings_btn.setCheckable(True)
        self.pipeline_caption_settings_btn.clicked.connect(
            lambda checked: self._toggle_pipeline_advanced("caption", checked)
        )
        advanced_row.addWidget(self.pipeline_caption_settings_btn)

        advanced_row.addStretch()
        layout.addLayout(advanced_row)

        run_row = QHBoxLayout()
        run_row.addStretch()
        self.pipeline_run_btn = QPushButton("Run selected pipeline")
        self.pipeline_run_btn.setObjectName("Primary")
        self.pipeline_run_btn.clicked.connect(self.run_selected_video_pipeline)
        run_row.addWidget(self.pipeline_run_btn)
        layout.addLayout(run_row)

        # Put the simple workflow before the detailed video cards.
        main.insertWidget(2, card)

        # Hide route-selection controls that are now driven by the three stage
        # checkboxes above.
        if hasattr(self, "caption_enabled_box"):
            self.caption_enabled_box.hide()
        if hasattr(self, "caption_direct_box"):
            self.caption_direct_box.hide()
        if hasattr(self, "topaz_auto_repair_box"):
            self.topaz_auto_repair_box.hide()

        # There should be one obvious action for video processing.
        if hasattr(self, "topaz_run_btn"):
            self.topaz_run_btn.hide()
        if hasattr(self, "repair_btn"):
            self.repair_btn.hide()

        if hasattr(self, "topaz_status"):
            self.topaz_status.setText("Settings ready")

        # Find the existing advanced cards by their heading and collapse them.
        self._advanced_video_cards = {}
        for frame in self.findChildren(QFrame):
            labels = frame.findChildren(QLabel)
            titles = {label.text().strip() for label in labels}
            if "Video enhancement" in titles:
                self._advanced_video_cards["topaz"] = frame
            elif "Automatic video captions" in titles:
                self._advanced_video_cards["caption"] = frame

        for frame in self._advanced_video_cards.values():
            frame.hide()

    def _toggle_pipeline_advanced(self, which: str, visible: bool):
        frame = self._advanced_video_cards.get(which)
        if frame is None:
            return

        frame.setVisible(bool(visible))

        # Keep only one advanced panel open at once.
        other = "caption" if which == "topaz" else "topaz"
        other_frame = self._advanced_video_cards.get(other)
        if visible and other_frame is not None:
            other_frame.hide()
            other_btn = (
                self.pipeline_caption_settings_btn
                if other == "caption"
                else self.pipeline_topaz_settings_btn
            )
            other_btn.blockSignals(True)
            other_btn.setChecked(False)
            other_btn.blockSignals(False)
            other_btn.setText(
                "Caption settings" if other == "caption" else "Topaz settings"
            )

        btn = (
            self.pipeline_topaz_settings_btn
            if which == "topaz"
            else self.pipeline_caption_settings_btn
        )
        base = "Topaz settings" if which == "topaz" else "Caption settings"
        btn.setText(("Hide " if visible else "") + base)

    def _set_pipeline_busy(self, busy: bool):
        if hasattr(self, "pipeline_run_btn"):
            self.pipeline_run_btn.setEnabled(not busy)

    def run_selected_video_pipeline(self):
        upscale = self.pipeline_upscale_box.isChecked()
        caption = self.pipeline_caption_box.isChecked()
        metadata = self.pipeline_metadata_box.isChecked()

        if not (upscale or caption or metadata):
            QMessageBox.information(
                self,
                "No stages selected",
                "Select at least one stage: Upscale, Caption, or Metadata repair.",
            )
            return

        if not self.targets:
            QMessageBox.information(
                self, "No files queued", "Add at least one file first."
            )
            return

        videos = [p for p in self.targets if core.is_video_path(p)]
        images = [p for p in self.targets if core.is_image_path(p)]

        if (upscale or caption) and not videos:
            QMessageBox.information(
                self,
                "No videos queued",
                "Upscale and Caption are video stages. Add at least one video.",
            )
            return

        if images and (upscale or caption):
            QMessageBox.warning(
                self,
                "Mixed queue",
                "Upscale/Caption apply to videos only. Process image targets separately, "
                "or disable Upscale and Caption to run metadata repair on the mixed queue.",
            )
            return

        self._pipeline_skip_metadata_after_direct_caption = False

        # Drive the existing hidden controls rather than duplicating processing.
        if hasattr(self, "caption_enabled_box"):
            self.caption_enabled_box.setChecked(bool(caption))
        if hasattr(self, "caption_direct_box"):
            self.caption_direct_box.setChecked(False)
        if hasattr(self, "topaz_auto_repair_box"):
            self.topaz_auto_repair_box.setChecked(bool(metadata))

        if upscale:
            # Existing flow handles:
            # Topaz -> optional captions -> optional automatic metadata repair.
            self._set_pipeline_busy(True)
            try:
                return super().start_video_upscale()
            finally:
                worker = getattr(self, "upscale_worker", None)
                if not worker or not worker.isRunning():
                    self._set_pipeline_busy(False)

        if caption:
            # Existing captions-only worker. By default it chains to metadata;
            # the override below stops after captions when metadata is unchecked.
            if hasattr(self, "caption_direct_box"):
                self.caption_direct_box.setChecked(True)
            self._pipeline_skip_metadata_after_direct_caption = not metadata
            self._set_pipeline_busy(True)
            try:
                return super().start_repair()
            finally:
                worker = getattr(self, "caption_only_worker", None)
                if not worker or not worker.isRunning():
                    self._set_pipeline_busy(False)

        # Metadata-only.
        self._set_pipeline_busy(True)
        try:
            return super().start_repair()
        finally:
            worker = getattr(self, "worker", None)
            if not worker or not worker.isRunning():
                self._set_pipeline_busy(False)

    def _on_caption_direct_finished(self, summary):
        """Allow Caption-only mode to stop without forcing metadata repair."""
        if not self._pipeline_skip_metadata_after_direct_caption:
            return super()._on_caption_direct_finished(summary)

        successes = list(summary.get("successes", []))
        failures = list(summary.get("failures", []))

        if failures:
            self._restore_direct_queue()
            self._clear_direct_state()
            self._pipeline_skip_metadata_after_direct_caption = False
            self._set_pipeline_busy(False)
            detail = "\n\n".join(
                f"{Path(item.get('source', '')).name}: {item.get('error', '')}"
                for item in failures[:8]
            )
            QMessageBox.critical(
                self,
                "Captioning failed",
                f"Captioning failed for {len(failures)} video(s).\n\n{detail}",
            )
            return

        outputs = [item["output"] for item in successes]
        original_targets = list(
            self._caption_direct_original_targets or self.targets
        )
        mapping = {
            item["source"]: item["output"]
            for item in successes
        }
        updated = [mapping.get(path, path) for path in original_targets]

        self.targets = []
        self.drop_list.clear()
        self.add_targets(updated)
        self.refresh_target_labels()

        self.log_box.appendPlainText("")
        self.log_box.appendPlainText(
            f"Caption-only pipeline complete: {len(outputs)} captioned video(s) retained. "
            "Metadata repair was not selected."
        )
        self.run_status.setText("Captioning complete")
        self.result_summary.setText(f"{len(outputs)} video(s) captioned")

        self._caption_direct_original_targets = None
        self._caption_direct_map = {}
        self._caption_direct_output_dir = None
        self.caption_only_worker = None
        self._pipeline_skip_metadata_after_direct_caption = False
        self._set_pipeline_busy(False)

    def on_upscale_finished(self, summary):
        super().on_upscale_finished(summary)
        worker = getattr(self, "worker", None)
        if worker and worker.isRunning():
            return
        self._set_pipeline_busy(False)

    def on_upscale_failed(self, message):
        try:
            return super().on_upscale_failed(message)
        finally:
            self._set_pipeline_busy(False)

    def _on_caption_direct_failed(self, message):
        try:
            return super()._on_caption_direct_failed(message)
        finally:
            self._pipeline_skip_metadata_after_direct_caption = False
            self._set_pipeline_busy(False)

    def on_finished(self, results):
        try:
            return super().on_finished(results)
        finally:
            self._pipeline_skip_metadata_after_direct_caption = False
            self._set_pipeline_busy(False)
