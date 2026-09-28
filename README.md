# Metadata Repair Tool v2.10.3

## v2.10.3 — automatic video captions + Lavc false-positive fix

This release adds local GPU-accelerated short-form video captioning to the existing Topaz + metadata-repair workflow, while also fixing a rare false-positive video verification failure.

### Automatic video captions

The app now supports **two independent captioning workflows**:

#### 1. Topaz → captions → metadata repair

Use this for new/raw videos that still need enhancement or 60 FPS conversion.

```text
source video
  -> Topaz upscale / optional 60 FPS conversion
  -> automatic captions
  -> metadata repair / injection
  -> final MOV
```

Enable:

- **Burn captions on Topaz video outputs**

The app transcribes the processed video with local Whisper and burns the captions before the final metadata-repair stage.

When the Topaz FFmpeg build supports the required subtitle filter, captions can be rendered in the same processing chain instead of requiring an unnecessary additional encode.

#### 2. Captions only → metadata repair

Use this for videos that are already fully upscaled/finished and only need captions added before metadata repair.

```text
finished video
  -> automatic captions
  -> metadata repair / injection
  -> final MOV
```

Enable:

- **Caption videos before normal Process / metadata repair (NO UPSCALE)**

Do **not** run Topaz for this workflow.

This is the intended mode for existing ready-to-upload 1080p/60 FPS videos that should not be upscaled again.

Captions-only mode uses **Safe copies** so the original finished files remain untouched.

### Caption style

The default caption style is designed for short-form vertical content:

- local word-level Whisper timestamps
- four-word caption groups
- bold white text
- current spoken word highlighted yellow
- black outline
- lower-centre placement
- original resolution and frame rate retained in captions-only mode

### GPU transcription

Caption transcription uses `faster-whisper`.

The default behaviour is:

```text
CUDA float16
  -> automatic CPU int8 fallback if CUDA cannot be loaded
```

On a correctly configured NVIDIA system the log should show:

```text
Loading distil-large-v3 on CUDA (float16)...
Transcription device active: CUDA
```

The project installs the required CUDA runtime libraries into its own Python virtual environment rather than requiring a full system-wide CUDA Toolkit installation.

Current caption dependencies include:

- `faster-whisper`
- NVIDIA CUDA 12 runtime
- cuBLAS
- cuDNN 9

The first run may also download the Whisper model. Subsequent runs reuse the local cache.

### Existing virtual environments

If `.venv` already existed before captioning support was added, install the new dependencies once with:

```bat
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Then launch normally:

```bat
run.bat
```

### Caption encoding quality

Captioning necessarily changes video pixels because the text must be burned into the frames.

The caption path therefore uses a deliberately high-quality encode to minimise generational loss:

- NVIDIA NVENC preferred
- high-quality preset
- constant-quality encoding
- captions-only fallback uses very high quality settings
- original dimensions and frame rate are retained
- audio is stream-copied whenever possible rather than being unnecessarily re-encoded

For Topaz processing, the encode quality was also increased from the older lower-quality settings.

### Audio preservation

When the source audio can be copied safely, the caption pipeline uses stream copy rather than:

```text
AAC -> decode -> AAC re-encode
```

This avoids unnecessary audio-generation loss.

The metadata-repair stage retains its existing packet and decoded-audio verification.

### Caption intermediate cleanup

Captioned intermediate files are temporary.

If metadata repair succeeds:

- the intermediate captioned video is removed
- the final metadata-repaired MOV is retained
- the original source remains untouched in Safe-copy mode

If metadata repair fails:

- the captioned intermediate is preserved under the failed metadata-repair folder
- the successful caption work is not discarded
- the file can be retried later with captioning and Topaz both disabled

For an already-captioned retry:

```text
captioned MP4
  -> metadata repair only
  -> final MOV
```

### v2.10.3 Lavc false-positive fix

Previous versions contained two fallback checks that searched the **entire MOV/MP4 file** for the literal ASCII bytes:

```text
Lavc
```

That was unsafe because compressed H.264/AAC media payload is arbitrary binary data. A valid video can coincidentally contain those four bytes inside its `mdat` media payload.

This caused a small number of otherwise valid captioned videos to fail with:

```text
An unrecognised Lavc identifier remains in the output.
```

v2.10.3 removes those unsafe whole-file scans.

The existing targeted verification remains in place, including:

- known AAC `Lavc` encoder-identifier cleanup
- FFmpeg `FFMP` video sample-entry vendor cleanup
- metadata/vendor inspection
- encoded packet verification
- decoded-audio verification

Random `Lavc` bytes inside compressed video/audio data are therefore no longer mistaken for container metadata.

---

## v2.9.0 — resilient batch processing and end-to-end video workflow

This release focuses on reliability and quality-of-life improvements for large Topaz + metadata-repair batches.

### Video batch workflow

- Added **automatic metadata repair after successful Topaz upscales**.
- Successful Topaz intermediates are now **deleted only after the repaired output has been created successfully**, leaving the original source video and the final repaired/upscaled video.
- If an upscale fails, the batch **continues with the remaining videos** instead of stopping.
- Failed upscale originals are copied into:
  `Topaz\failed upscale`
- If metadata repair fails after a successful upscale, the Topaz intermediate is preserved in:
  `Topaz\failed metadata repair`
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
  - `Topaz\last_topaz_batch_report.txt`
  - `Topaz\last_topaz_batch_report.json`
  - `Topaz\topaz_manifest.json`

### Safety behaviour

The cleanup logic only removes app-generated Topaz intermediate files from the expected `Topaz` output folder. Original source videos are never deleted by the successful-repair cleanup step. If the repaired output is missing or metadata repair fails, the intermediate is retained instead of being removed.

## v2.8.4 — preserve video orientation and shape during Topaz upscale

The upscale stage reads an actual decoded source frame before choosing its canvas. The 1080p/720p preset sets the shorter side and keeps the input aspect ratio, including square and 4:3 video. For portrait 9:16, 1080p still means exactly 1080×1920. After Topaz finishes, the app decodes its output and rejects/deletes it if the output dimensions differ from those requested.

## v2.8.3 — clear AAC encoder identification

When FFmpeg's version identifier occurs in the known AAC fill-element layout, the video path replaces only those identifier bytes. Packet lengths and timestamps stay the same. Verification compares every video packet, compares audio packets after this exact normalisation, and confirms decoded audio is byte-for-byte identical.

## v2.8.2 — upscale preset default

The Topaz output preset starts at **1080p60**. Other presets remain available.

## v2.8.1 — clear the FFmpeg video vendor field

The MOV remux clears the four-byte `FFMP` video sample-entry vendor field to an unspecified value. The app checks the completed file for surviving FFmpeg vendor/encoder metadata.

## v2.8.0 — one-click QuickTime video output

Video targets produce `.mov` files with a QuickTime `qt` container and Core Media track handler labels. FFmpeg copies encoded streams without re-encoding, while ExifTool transfers selected device make/model/software keys from the video reference.

## v2.6.0 — optional AI image pixel cleanup

The image workflow has an **AI image cleanup** panel. When enabled, image targets are first passed through the separately installed `remove-ai-watermarks` tool, then the existing metadata repair engine runs on the cleaned pixels.

The order is:

```text
image target
  -> optional invisible-watermark pixel cleanup
  -> strip old metadata
  -> clone trusted image-reference metadata
  -> restore target dimensions/orientation
  -> verify repaired metadata
```

Important behaviour:

- **Off by default.**
- **Images only.**
- **Safe copies only.**
- **SDXL + Z-Image is the recommended default.**
- **One batch / one model load.**
- Temporary cleaned files are deleted after metadata repair.
- Original image dimensions are checked after pixel cleanup.

### Installing the optional AI scrubber

Install separately with `uv`:

```powershell
uv tool install --force "remove-ai-watermarks[qwen-zimage]"
```

## v2.5.1 — dual references and AI Fingerprint Audit

Choose one **image reference** and one **video reference**. Mixed batches are routed automatically.

### AI Fingerprint Audit

The audit reports:

- direct AI-related metadata strings
- possible C2PA / Content Credentials-style metadata markers
- EXIF, XMP, ICC and MakerNotes presence
- PNG metadata chunks
- basic device/software/container information
- sparse/stripped metadata footprints

The score measures observable metadata/provenance markers only. It is not a probability that media is AI-generated.

## Default output folder

Safe-copy mode defaults to a `Repaired` folder beside the running app:

```text
C:\Tools\MetadataRepairTool\MetadataRepairTool.exe
C:\Tools\MetadataRepairTool\Repaired\
```

## Video behaviour

Video outputs are QuickTime MOV safe copies by default.

The repair workflow:

- removes unwanted metadata
- transfers selected trusted device fields from the video reference
- preserves the target video's actual structural/media properties
- verifies encoded streams after writing
- rejects unwanted location/AI-provenance metadata

## Image behaviour

With AI cleanup **disabled**, image metadata is cloned from the image reference while target-specific layout stays truthful.

With AI cleanup **enabled**, the selected diffusion pipeline intentionally regenerates image pixels first; the same metadata/layout repair and verification rules then run on that cleaned image.

## Setup

Place the required local tools beside the source before building:

```text
metadataInjector\
  app.py
  app_plus.py
  app_unified.py
  app_v26.py
  captioning.py
  caption_integration.py
  video_quicktime.py
  exiftool.exe
  exiftool_files\
  ffmpeg.exe
  ffprobe.exe
  requirements.txt
  run.bat
  build_exe.bat
```

For source-mode use:

```bat
run.bat
```

For a distributable build:

```bat
build_exe.bat
```

The unified entry point is `app_unified.py`.

Keep **Safe copies** enabled while testing new video/image processing paths.
