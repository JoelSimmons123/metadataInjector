Metadata Repair Tool aspect-ratio / orientation fix

Replace app_v26.py with the included file.

This fixes the optional AI image cleanup so that a 9:16 input stays 9:16 after SynthID/invisible-watermark removal.

What changed:
- compares displayed dimensions, not raw encoded width/height
- normalises AI-cleaned images to Orientation=1 instead of re-applying the old EXIF rotation tag
- aborts if the AI cleanup actually changes the displayed geometry

If you are using the merged app_unified.py build, replacing app_v26.py is enough because app_unified.py imports app_v26.py.
