# Metadata Repair Tool v2.11.1

## v2.11.1 — cleaner, readable video workflow UI

v2.11.1 keeps the v2.11 processing pipeline but cleans up the interface so the normal workflow is readable without exposing every advanced option at once.

### Main video workflow

The primary video workflow is now simply:

```text
Upscale → Caption → Metadata repair
```

The app exposes these as three stage checkboxes:

- **1. Upscale**
- **2. Caption**
- **3. Metadata repair**

and one action button:

- **Run selected pipeline**

Common setups:

```text
Fresh/raw video:
Upscale ON
Caption ON
Metadata repair ON

Already upscaled video:
Upscale OFF
Caption ON
Metadata repair ON

Already captioned/upscaled video:
Upscale OFF
Caption OFF
Metadata repair ON

Upscale only:
Upscale ON
Caption OFF
Metadata repair OFF

Caption only:
Upscale OFF
Caption ON
Metadata repair OFF
```

The underlying Topaz, Whisper captioning, metadata repair, verification, cleanup and retry logic is unchanged. The simple UI only controls which existing stages run.

### Readability improvements

The old implementation-specific controls are no longer shown as the primary workflow.

- **Video enhancement** settings are collapsed by default.
- **Automatic video captions** settings are collapsed by default.
- The pipeline card provides **Topaz settings** and **Caption settings** buttons.
- Only one advanced panel is shown at a time.
- Global font size was increased.
- Small labels were enlarged.
- Buttons were slightly enlarged.
- Outer margins and spacing were reduced.
- The file queue uses less vertical space so the complete workflow fits more comfortably.

Normal use should therefore require only the three pipeline checkboxes and the single **Run selected pipeline** button.

---

## v2.11.0 — simplified stage-based video pipeline

v2.11 replaced the older route-specific controls with a stage-based pipeline.

Before v2.11, users had to understand separate options such as:

- Burn captions on Topaz video outputs
- Caption videos before normal Process / metadata repair
- separate Topaz and metadata Process buttons

Those controls exposed internal implementation details and made it unclear which path to use.

v2.11 maps the existing processing logic to three simple stages instead:

```text
Upscale → Caption → Metadata repair
```

When **Upscale** is enabled, the existing Topaz workflow processes queued videos first.

When **Caption** is enabled, local Whisper transcription generates word-level subtitles and burns them into the video.

When **Metadata repair** is enabled, the existing final QuickTime/iPhone metadata repair runs after all selected media-processing stages.

---

## v2.10.3 — automatic video captions + Lavc false-positive fix

This release adds local GPU-accelerated short-form video captioning to the existing Topaz + metadata-repair workflow, while also fixing a rare false-positive video verification failure.

### Automatic video captions

The caption engine supports both:

```text
source video
  → Topaz upscale / optional 60 FPS conversion
  → automatic captions
  → metadata repair
  → final MOV
```

and:

```text
already-finished video
  → automatic captions
  → metadata repair
  → final MOV
```

The v2.11+ stage-based interface selects these paths automatically based on the **Upscale**, **Caption** and **Metadata repair** checkboxes.

### Caption style

The default caption style is designed for short-form vertical content:

- local word-level Whisper timestamps
- four-word caption groups
- bold white text
- current spoken word highlighted yellow
- black outline
- lower-centre placement
- original resolution and frame rate retained when no upscale stage is selected

### GPU transcription

Caption transcription uses `faster-whisper`.

The default behaviour is:

```text
CUDA float16
  → automatic CPU int8 fallback if CUDA cannot be loaded
```

On a correctly configured NVIDIA system the log should show:

```text
Loading distil-large-v3 on CUDA (float16)...
Transcription device active: CUDA
```

The project installs the required NVIDIA CUDA runtime libraries into its own Python virtual environment.

Current caption dependencies include:

- `faster-whisper`
- NVIDIA CUDA 12 runtime
- cuBLAS
- cuDNN 9

The first run may download the Whisper model. Subsequent runs reuse the local cache.

### Existing virtual environments

If `.venv` existed before captioning support was added, install the current dependencies with:

```bat
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Then launch normally:

```bat
run.bat
```

### Caption encoding quality

Burned-in captions necessarily alter video pixels because the text becomes part of the encoded frames.

The caption pipeline therefore uses deliberately high-quality encoding:

- NVIDIA NVENC preferred
- high-quality preset
- constant-quality encoding
- very-high-quality caption-only fallback
- source dimensions and FPS retained when Upscale is disabled
- audio stream-copy whenever possible

The Topaz output encode quality was also increased compared with the older settings.

### Audio preservation

When the input audio can be copied safely, the caption workflow uses stream copy rather than unnecessary AAC recompression.

```text
AAC → copied unchanged
```

instead of:

```text
AAC → decode → AAC encode
```

The metadata-repair stage retains packet and decoded-audio verification.

### Caption intermediate cleanup

Captioned intermediates are temporary when Metadata repair is also selected.

If metadata repair succeeds:

- the temporary captioned video is removed
- the final repaired MOV is retained
- the original source remains untouched in Safe-copy mode

If metadata repair fails:

- the captioned intermediate is preserved in the failed metadata-repair folder
- caption work is not discarded
- it can be retried later with only **Metadata repair** selected

### Lavc false-positive fix

Older builds performed fallback checks that searched the entire MOV/MP4 byte stream for the literal ASCII bytes:

```text
Lavc
```

Compressed H.264/AAC payload is arbitrary binary data, so valid media can coincidentally contain those four bytes inside `mdat`.

This caused a small number of valid captioned videos to be incorrectly rejected.

v2.10.3 removed those unsafe whole-file scans while retaining the targeted checks for:

- known AAC `Lavc` encoder identifiers
- FFmpeg `FFMP` video sample-entry vendor fields
- metadata/vendor inspection
- encoded packet verification
- decoded-audio verification

---

## v2.9.0 — resilient batch processing and end-to-end video workflow

This release focuses on reliability and quality-of-life improvements for large Topaz + metadata-repair batches.

### Video batch workflow

- Added **automatic metadata repair after successful Topaz upscales**.
- Successful Topaz intermediates are deleted only after the repaired output has been created successfully.
- If an upscale fails, the batch continues with the remaining videos.
- Failed upscale originals are copied into:
  `Topaz\failed upscale`
- If metadata repair fails after a successful upscale, the Topaz intermediate is preserved in:
  `Topaz\failed metadata repair`
- Added **Retry failed upscales**.
- Added **Open failed upscale folder**.
- Added **Stop after current video**.
- Added skip-already-completed handling using a persistent Topaz manifest.
- Added a disk-space warning before large batches.
- Added cleanup/recovery for abandoned or zero-byte Topaz partial files.
- Added clearer batch progress.
- Original filenames are tracked alongside generated `vidN_*` names.
- Added final batch accounting.
- Added persistent batch reports:
  - `Topaz\last_topaz_batch_report.txt`
  - `Topaz\last_topaz_batch_report.json`
  - `Topaz\topaz_manifest.json`

### Safety behaviour

Cleanup only removes app-generated Topaz intermediate files from the expected Topaz output folder. Original source videos are not deleted by successful-repair cleanup.

---

## v2.8.4 — preserve video orientation and shape during Topaz upscale

The upscale stage reads an actual decoded source frame before choosing its canvas.

The 1080p/720p preset sets the shorter side while retaining the input aspect ratio, including square and 4:3 video.

For portrait 9:16, 1080p remains 1080×1920.

After Topaz finishes, the output dimensions are verified and incorrect outputs are rejected.

## v2.8.3 — clear AAC encoder identification

When FFmpeg's version identifier occurs in the known AAC fill-element layout, the video path replaces only those identifier bytes.

Packet sizes and timestamps remain unchanged, and decoded audio is verified.

## v2.8.2 — upscale preset default

The Topaz output preset starts at **1080p60**.

## v2.8.1 — clear FFmpeg video vendor field

The MOV remux clears the four-byte `FFMP` video sample-entry vendor field when present.

## v2.8.0 — one-click QuickTime video output

Video targets produce `.mov` files using a QuickTime container with Core Media track handler labels.

FFmpeg stream-copies encoded media during the metadata-remux stage and ExifTool transfers selected trusted device fields from the video reference.

---

## v2.6.0 — optional AI image pixel cleanup

The image workflow includes an optional AI image cleanup stage.

When enabled:

```text
image target
  → optional invisible-watermark pixel cleanup
  → strip old metadata
  → clone trusted image-reference metadata
  → restore target dimensions/orientation
  → verify repaired metadata
```

Important behaviour:

- Off by default
- Images only
- Safe copies only
- SDXL + Z-Image recommended
- one model load per batch
- temporary cleaned images removed after repair
- original target geometry verified

### Installing the optional AI scrubber

Install separately with `uv`:

```powershell
uv tool install --force "remove-ai-watermarks[qwen-zimage]"
```

---

## v2.5.1 — dual references and AI Fingerprint Audit

Choose one image reference and one video reference. Mixed batches are routed automatically.

### AI Fingerprint Audit

The audit can report:

- direct AI-related metadata strings
- possible C2PA / Content Credentials markers
- EXIF, XMP, ICC and MakerNotes presence
- PNG metadata chunks
- device/software/container information
- sparse/stripped metadata footprints

The score reflects observable metadata/provenance signals only. It is not a probability that media is AI-generated.

---

## Default output folder

Safe-copy mode defaults to a `Repaired` folder beside the running app:

```text
C:\Tools\MetadataRepairTool\MetadataRepairTool.exe
C:\Tools\MetadataRepairTool\Repaired\
```

---

## Video behaviour

Video outputs are QuickTime MOV safe copies by default.

The repair workflow:

- removes unwanted metadata
- transfers selected trusted device fields from the video reference
- preserves the target video's real structural/media properties
- verifies encoded streams after writing
- rejects unwanted location or AI-provenance metadata

---

## Image behaviour

With AI cleanup disabled, image metadata is cloned from the trusted image reference while target-specific layout remains truthful.

With AI cleanup enabled, the selected diffusion pipeline regenerates image pixels first, then the same metadata/layout repair and verification runs.

---

## Setup

Expected project files include:

```text
metadataInjector\
  app.py
  app_plus.py
  app_unified.py
  app_v26.py
  captioning.py
  caption_integration.py
  video_pipeline.py
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

Keep **Safe copies** enabled while testing new processing paths.
