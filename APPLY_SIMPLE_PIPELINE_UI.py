from pathlib import Path

path = Path("app_unified.py")
if not path.is_file():
    raise SystemExit("ERROR: app_unified.py not found. Run from the repo root.")

text = path.read_text(encoding="utf-8")

replacements = [
    (
        'import caption_integration as captions\n',
        'import caption_integration as captions\nimport video_pipeline as pipeline\n',
    ),
    (
        'APP_VERSION = "2.10.1"',
        'APP_VERSION = "2.11.0"',
    ),
    (
        'class MainWindow(captions.CaptionMixin, ai.MainWindow, topaz.MainWindow):',
        'class MainWindow(pipeline.VideoPipelineMixin, captions.CaptionMixin, ai.MainWindow, topaz.MainWindow):',
    ),
    (
        '"""One window containing metadata repair, AI cleanup, Topaz and caption tools."""',
        '"""One window with a simple Upscale → Caption → Metadata video pipeline."""',
    ),
]

for old, new in replacements:
    if old not in text:
        raise SystemExit(f"ERROR: expected text not found in app_unified.py: {old!r}")
    text = text.replace(old, new, 1)

compile(text, "app_unified.py", "exec")

backup = Path("app_unified.py.before_v2.11_pipeline")
if not backup.exists():
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")

path.write_text(text, encoding="utf-8", newline="\n")

version = Path("VERSION.txt")
if version.exists():
    version.write_text("2.11.0\n", encoding="utf-8")

print("SUCCESS: v2.11.0 simple video pipeline UI enabled.")
print("Stages: Upscale -> Caption -> Metadata repair")
