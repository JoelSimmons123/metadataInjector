Metadata Repair Tool v2.7.0 merge patch

Drop these files into the root of your existing metadataInjector folder.

REPLACES:
- run.bat
- build_exe.bat
- VERSION.txt

ADDS:
- app_unified.py

KEEP these existing files:
- app.py
- app_v26.py
- app_plus.py
- requirements.txt
- ExifTool files

Features now live together in one app:
- normal metadata repair
- optional image SynthID/invisible-watermark cleanup
- Topaz video enhancement
- automatic trusted references from "good images"

AI image cleanup remains OFF by default.
Tick "Remove invisible AI watermark before metadata repair" only when you want it.

Default trusted references:
Create this folder beside the source files / built EXE:

good images\

Put at least:
- one image reference (HEIC is supported and preferred)
- one video reference (MOV is supported and preferred)

If several files exist, the app prefers:
Image: .heic, .heif, .jpg, .jpeg, .png, ...
Video: .mov, .mp4, .m4v

The normal Choose image/video reference buttons still work and can override the auto-loaded references for the current session.
