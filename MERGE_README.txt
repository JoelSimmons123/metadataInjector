Metadata Repair Tool v2.7.1 full merged drag-and-drop patch

Drop these files into the root of your existing metadataInjector folder.

Replaces:
- app_v26.py
- run.bat
- build_exe.bat
- VERSION.txt

Adds:
- app_unified.py

Keep these existing files:
- app.py
- app_plus.py
- requirements.txt
- ExifTool files

Included in this merged version:
- normal metadata repair
- optional image SynthID / invisible-watermark cleanup
- Topaz video enhancement
- default trusted references auto-loaded from a folder named "good images"

Notes:
- The AI image cleanup checkbox remains OFF by default.
- HEIC / HEIF are supported for the image reference.
- MOV is supported for the video reference.
- The app prefers .heic for image references and .mov for video references.
- run.bat now launches the GUI without leaving a console window sitting open.

Example layout:
metadataInjector\
  app.py
  app_plus.py
  app_v26.py
  app_unified.py
  run.bat
  build_exe.bat
  good images\
    reference.HEIC
    reference.MOV
