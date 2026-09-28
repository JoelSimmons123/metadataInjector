from pathlib import Path

vp = Path("video_pipeline.py")
app = Path("app.py")
unified = Path("app_unified.py")

for p in (vp, app, unified):
    if not p.is_file():
        raise SystemExit(f"ERROR: {p} not found. Run this from the metadataInjector repo root.")

vp_text = vp.read_text(encoding="utf-8")
app_text = app.read_text(encoding="utf-8")
unified_text = unified.read_text(encoding="utf-8")

# ---- 1) Upgrade the pipeline card with collapsible advanced settings ----

old_import = """from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
"""
new_import = """from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
"""
# Import block itself is unchanged, kept as a structural sanity check.
if old_import not in vp_text:
    raise SystemExit("ERROR: Unexpected video_pipeline.py import block. No changes made.")

old_tail = """        # Clarify that the old cards are now settings only.
        if hasattr(self, "topaz_status"):
            self.topaz_status.setText("Settings ready")
"""

new_tail = """        # Clarify that the old cards are now settings only.
        if hasattr(self, "topaz_status"):
            self.topaz_status.setText("Settings ready")

        # Find the two implementation/detail cards and collapse them by default.
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

        # Compact advanced-settings row inside the simple pipeline card.
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
        layout.insertLayout(layout.count() - 1, advanced_row)
"""

if old_tail not in vp_text:
    raise SystemExit("ERROR: Could not locate v2.11 pipeline setup block. No changes made.")
vp_text = vp_text.replace(old_tail, new_tail, 1)

# Insert toggle helper before _set_pipeline_busy.
anchor = """    def _set_pipeline_busy(self, busy: bool):
"""
helper = """    def _toggle_pipeline_advanced(self, which: str, visible: bool):
        frame = getattr(self, "_advanced_video_cards", {}).get(which)
        if frame is None:
            return

        frame.setVisible(bool(visible))

        # Keep only one advanced panel open at a time to avoid vertical clutter.
        other = "caption" if which == "topaz" else "topaz"
        other_frame = getattr(self, "_advanced_video_cards", {}).get(other)
        if visible and other_frame is not None:
            other_frame.hide()
            other_btn = (
                getattr(self, "pipeline_caption_settings_btn", None)
                if other == "caption"
                else getattr(self, "pipeline_topaz_settings_btn", None)
            )
            if other_btn is not None:
                other_btn.blockSignals(True)
                other_btn.setChecked(False)
                other_btn.blockSignals(False)

        # Make the button label clearly indicate open/closed state.
        btn = (
            getattr(self, "pipeline_topaz_settings_btn", None)
            if which == "topaz"
            else getattr(self, "pipeline_caption_settings_btn", None)
        )
        if btn is not None:
            base = "Topaz settings" if which == "topaz" else "Caption settings"
            btn.setText(("Hide " if visible else "") + base)

        self.adjustSize() if False else None

"""
if anchor not in vp_text:
    raise SystemExit("ERROR: Could not locate _set_pipeline_busy. No changes made.")
vp_text = vp_text.replace(anchor, helper + anchor, 1)

# ---- 2) Improve global readability without blowing up the layout ----

replacements = {
    'font-size: 10.5pt;': 'font-size: 11.5pt;',
    'font-size: 25pt;': 'font-size: 27pt;',
    'font-size: 12pt;': 'font-size: 13pt;',
    'font-size: 9pt;': 'font-size: 10pt;',
    'padding: 8px 13px;': 'padding: 9px 14px;',
    'padding: 11px 18px;': 'padding: 12px 20px;',
}

for old, new in replacements.items():
    if old in app_text:
        app_text = app_text.replace(old, new)

# The file queue should remain useful even on a shorter-height display.
app_text = app_text.replace(
    "self.setMinimumHeight(270)",
    "self.setMinimumHeight(220)"
)

# Slightly reduce outer whitespace to give content more breathing room vertically.
app_text = app_text.replace(
    "main.setContentsMargins(26, 22, 26, 18)",
    "main.setContentsMargins(20, 16, 20, 14)"
)
app_text = app_text.replace(
    "main.setSpacing(16)",
    "main.setSpacing(12)"
)

# ---- 3) Version bump ----
unified_text = unified_text.replace(
    'APP_VERSION = "2.11.0"',
    'APP_VERSION = "2.11.1"'
)
unified_text = unified_text.replace(
    '"""Metadata Repair Tool v2.10.1 unified entry point.',
    '"""Metadata Repair Tool v2.11.1 unified entry point.'
)

# Compile all prospective Python before writing anything.
compile(vp_text, "video_pipeline.py", "exec")
compile(app_text, "app.py", "exec")
compile(unified_text, "app_unified.py", "exec")

# Backups
for path, original in (
    (vp, vp.read_text(encoding="utf-8")),
    (app, app.read_text(encoding="utf-8")),
    (unified, unified.read_text(encoding="utf-8")),
):
    backup = path.with_suffix(path.suffix + ".before_v2.11.1_ui")
    if not backup.exists():
        backup.write_text(original, encoding="utf-8", newline="\n")

vp.write_text(vp_text, encoding="utf-8", newline="\n")
app.write_text(app_text, encoding="utf-8", newline="\n")
unified.write_text(unified_text, encoding="utf-8", newline="\n")

version = Path("VERSION.txt")
if version.exists():
    version.write_text("2.11.1\n", encoding="utf-8")

print("SUCCESS: v2.11.1 readability/layout patch applied.")
print()
print("Changes:")
print("  - Topaz settings collapsed by default")
print("  - Caption settings collapsed by default")
print("  - Only one advanced settings panel opens at once")
print("  - Larger fonts and buttons")
print("  - Less wasted outer spacing")
print("  - More compact file queue minimum height")
print("  - Processing logic unchanged")
