Metadata Repair Tool v2.10.1 caption integration patch
========================================================

What changed
------------
- Adds GPU Whisper automatic captions as TWO independent workflows.
- Existing / already-ready videos can now use: captions -> metadata repair, with NO Topaz/upscale pass.
- Future raw videos can use: Topaz -> captions -> metadata repair.
- Metadata repair/injection always runs AFTER caption rendering.
- If the Topaz FFmpeg has the ASS/libass filter, captions are burned in the SAME
  Topaz encode, avoiding an extra video generation entirely.
- If Topaz lacks libass, the app uses normal FFmpeg for a CQ10 high-quality NVENC
  fallback caption pass and stream-copies audio bit-for-bit.
- Captions-only mode also uses CQ10 NVENC (libx264 CRF10 fallback) and stream-copies audio.
- Topaz video quality increased from NVENC CQ18/P6 to CQ14/P7.
- libx264 Topaz fallback increased from CRF18/medium to CRF14/slow.
- Existing AAC audio is copied rather than re-encoded during Topaz processing.
- Caption defaults match the approved BatchCaptioner look: 4 words, white bold text,
  yellow current-word highlight, black outline, lower-center placement.
- GPU transcription uses distil-large-v3 / CUDA float16 by default with CPU fallback.

Install / apply
---------------
1. Close the Metadata Repair Tool.
2. Copy every file from this patch folder into the root of your metadataInjector
   repo/folder, replacing app_unified.py, requirements.txt, run.bat and VERSION.txt.
   captioning.py and caption_integration.py are new files.
3. Run run.bat.
4. The FIRST run installs faster-whisper plus NVIDIA cuBLAS/cuDNN/CUDA runtime into
   the tool's existing .venv. This is roughly a 1.3 GB one-time dependency download.
5. The GUI will have a new "Automatic video captions" card.

Already-ready videos (NO UPSCALE)
----------------------------------
1. Leave the Topaz workflow alone. Do NOT click "Upscale queued videos".
2. Use Safe copies mode (Replace originals must be OFF).
3. Add the already-ready videos to the normal queue.
4. Tick: "Caption videos before normal Process / metadata repair (NO UPSCALE)".
5. Click the normal Process / metadata-repair button.

Pipeline:
  ready video
   -> CQ10 caption burn (NO Topaz, same resolution/FPS)
   -> audio stream-copy
   -> normal metadata repair/injection
   -> final cleaned MOV

Captioned intermediates are written under <output>\Captioned. After a successful
metadata repair they are deleted automatically. If metadata repair fails, the
captioned intermediate is preserved under Captioned\failed metadata repair.

Future videos using Topaz
-------------------------
Pipeline:
  original
   -> Topaz upscale / optional 60 FPS
   -> captions (same encode when possible; otherwise CQ10 fallback)
   -> metadata repair/injection
   -> final cleaned MOV

Notes
-----
- The Whisper model itself downloads on first captioned video and is cached.
- Metadata is injected LAST, so captioning cannot wipe the final Apple/QuickTime
  metadata you deliberately repair afterward.
- Captions-only mode never calls Topaz. It keeps the input resolution and FPS.
- If a video has no detectable speech, its media is copied unchanged into the
  intermediate and the metadata-repair stage still runs.
- Safe copies are required for captions-only mode so already-finished originals
  are never overwritten.
