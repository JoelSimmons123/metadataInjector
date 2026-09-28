from __future__ import annotations

from pathlib import Path

NEW_VERSION = "2.9.0"

readme = Path("README.md")
version_file = Path("VERSION.txt")
unified = Path("app_unified.py")

for path in (readme, version_file, unified):
    if not path.is_file():
        raise SystemExit(f"ERROR: {path} was not found. Run this script from the metadataInjector repo folder.")

text = readme.read_text(encoding="utf-8")

old_title = "# Metadata Repair Tool v2.8.4"
if old_title in text:
    text = text.replace(old_title, f"# Metadata Repair Tool v{NEW_VERSION}", 1)
elif f"# Metadata Repair Tool v{NEW_VERSION}" not in text:
    raise SystemExit("ERROR: Could not find the README version title.")

entry = """## v2.9.0 — resilient batch processing and end-to-end video workflow

This release focuses on reliability and quality-of-life improvements for large Topaz + metadata-repair batches.

### Video batch workflow

- Added **automatic metadata repair after successful Topaz upscales**.
- Successful Topaz intermediates are now **deleted only after the repaired output has been created successfully**, leaving the original source video and the final repaired/upscaled video.
- If an upscale fails, the batch **continues with the remaining videos** instead of stopping.
- Failed upscale originals are copied into:
  `Topaz\\failed upscale`
- If metadata repair fails after a successful upscale, the Topaz intermediate is preserved in:
  `Topaz\\failed metadata repair`
- Added **Retry failed upscales**.
- Added **Open failed upscale folder**.
- Added **Stop after current video** so a long batch can be ended cleanly without killing the active encode.
- Added **skip already-completed originals** using a persistent Topaz manifest.
- Added a **disk-space warning** before large batches when available free space may be too low.
- Added cleanup/recovery handling for abandoned or zero-byte Topaz partial files.
- Added clearer batch progress showing processed, succeeded, failed and skipped counts.
- Original filenames are tracked alongside generated `vidN_*` names so each final output can be traced back to its source.
- Added final batch accounting so every queued original is classified as repaired, upscale-failed, metadata-repair-failed or skipped.
- Added persistent batch reports:
  - `Topaz\\last_topaz_batch_report.txt`
  - `Topaz\\last_topaz_batch_report.json`
  - `Topaz\\topaz_manifest.json`

### Safety behaviour

The cleanup logic only removes app-generated Topaz intermediate files from the expected `Topaz` output folder. Original source videos are never deleted by the successful-repair cleanup step. If the repaired output is missing or metadata repair fails, the intermediate is retained instead of being removed.

"""

marker = "## v2.8.4 — preserve video orientation and shape during Topaz upscale"
if entry.strip() not in text:
    if marker not in text:
        raise SystemExit("ERROR: Could not find the v2.8.4 README changelog marker.")
    text = text.replace(marker, entry + marker, 1)

readme.write_text(text, encoding="utf-8", newline="\n")
version_file.write_text(NEW_VERSION + "\n", encoding="utf-8", newline="\n")

u = unified.read_text(encoding="utf-8")
u = u.replace('"""Metadata Repair Tool v2.7.1 unified entry point.', f'"""Metadata Repair Tool v{NEW_VERSION} unified entry point.', 1)
u = u.replace('APP_VERSION = "2.8.4"', f'APP_VERSION = "{NEW_VERSION}"', 1)

if f'APP_VERSION = "{NEW_VERSION}"' not in u:
    raise SystemExit("ERROR: Could not update APP_VERSION in app_unified.py.")

unified.write_text(u, encoding="utf-8", newline="\n")

print("Updated release/version information:")
print(f"  README.md -> v{NEW_VERSION} + changelog entry")
print(f"  VERSION.txt -> {NEW_VERSION}")
print(f"  app_unified.py -> window/app version {NEW_VERSION}")
print()
print("Review with:")
print("  git diff -- README.md VERSION.txt app_unified.py")
print()
print("Then commit with:")
print('  git add README.md VERSION.txt app_unified.py')
print('  git commit -m "Release v2.9.0 batch workflow improvements"')
print("  git push")
