# Metadata Repair Tool v2.11.1

## v2.11.1 — simplified and readable video pipeline

The normal video workflow is now presented as three stages:

```text
Upscale → Caption → Metadata repair
```

Choose the stages you want and click **Run selected pipeline**.

### Common workflows

**Fresh/raw video**

```text
Upscale          ON
Caption          ON
Metadata repair  ON
```

Result:

```text
source
  → Topaz upscale / optional 60 FPS
  → automatic captions
  → metadata repair
  → final MOV
```

**Already upscaled video that only needs captions + metadata**

```text
Upscale          OFF
Caption          ON
Metadata repair  ON
```

**Already upscaled and captioned video**

```text
Upscale          OFF
Caption          OFF
Metadata repair  ON
```

**Caption only**

```text
Upscale          OFF
Caption          ON
Metadata repair  OFF
```

**Upscale only**

```text
Upscale          ON
Caption          OFF
Metadata repair  OFF
```

### Cleaner UI

The old route-specific caption/Topaz controls still exist internally but are no longer exposed as the primary workflow.

- **Video enhancement** is collapsed by default.
- **Automatic video captions** is collapsed by default.
- Use **Topaz settings** when you need to change upscale/FPS/model settings.
- Use **Caption settings** when you need to change Whisper model, font, words-per-caption, highlighting, etc.
- Only one advanced panel opens at a time.
- Larger text/buttons and tighter spacing make the app readable on high-resolution monitors.

The underlying Topaz, captioning, metadata-repair, retry and verification code is unchanged by the UI layer.

---

## Automatic video captions

Captioning uses local `faster-whisper` word-level timestamps.

Default caption styling:

- four-word groups
- bold white text
- current spoken word highlighted yellow
- black outline
- lower-centre placement

### GPU transcription

Default behavior:

```text
CUDA float16
  → CPU int8 fallback if CUDA cannot be used
```

A working GPU run should show:

```text
Loading distil-large-v3 on CUDA (float16)...
Transcription device active: CUDA
```

Current caption dependencies include:

- `faster-whisper`
- NVIDIA CUDA 12 runtime
- cuBLAS
- cuDNN 9

If the virtual environment existed before caption support was added, update it once with:

```bat
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Video/audio quality

Burning captions necessarily re-encodes video because the text becomes part of the frames.

The caption workflow therefore uses deliberately high-quality encoding:

- NVIDIA NVENC preferred
- high-quality constant-quality settings
- very-high-quality fallback caption pass
- resolution/FPS retained when Upscale is disabled
- audio stream-copy whenever possible

AAC audio is copied unchanged where supported instead of unnecessarily doing:

```text
AAC → decode → AAC encode
```

### Temporary caption files

When **Caption + Metadata repair** are both enabled:

```text
captioned intermediate
  → metadata repair
  → final MOV
```

After successful metadata repair, the captioned intermediate is deleted.

If metadata repair fails, the captioned intermediate is preserved so it can be retried with only **Metadata repair** selected.

---

## v2.10.3 — Lavc false-positive fix

Earlier builds contained fallback checks that searched the entire MOV/MP4 byte stream for the ASCII sequence:

```text
Lavc
```

Valid compressed H.264/AAC payload can coincidentally contain those bytes inside `mdat`.

The unsafe whole-file checks were removed. Targeted verification remains, including:

- known AAC `Lavc` encoder identifier cleanup
- FFmpeg `FFMP` video sample-entry vendor cleanup
- metadata/vendor inspection
- encoded packet verification
- decoded-audio verification

This prevents valid videos from being rejected merely because compressed media bytes happen to spell `Lavc`.

---

## Topaz batch workflow

The Topaz workflow supports:

- local Topaz Video enhancement
- 1080p60 default preset
- aspect-ratio/orientation preservation
- automatic continuation through large batches
- retrying failed upscales
- stop-after-current behavior
- skip-already-completed tracking
- disk-space warnings
- failure folders
- automatic metadata repair when selected
- cleanup of successful intermediates
- persistent batch accounting/reports

Topaz outputs are only deleted after the downstream stage has succeeded.

---

## Metadata repair

Video repair produces QuickTime MOV safe copies by default.

The repair stage:

- removes unwanted metadata
- transfers selected trusted device fields from the video reference
- preserves the target video's real structural/media properties
- verifies encoded media after writing
- rejects unwanted location/AI-provenance metadata

The target's real resolution, duration, frame rate, codec, rotation, HDR signaling, audio layout and media streams are not cloned from the reference.

---

## Image workflow

Images support normal metadata repair and optional AI image pixel cleanup.

When AI cleanup is enabled:

```text
image
  → optional invisible-watermark pixel cleanup
  → metadata repair
  → final image
```

AI image cleanup is intentionally separate from the video pipeline.

### Optional AI scrubber

Install with `uv`:

```powershell
uv tool install --force "remove-ai-watermarks[qwen-zimage]"
```

---

## AI Fingerprint Audit

The audit can inspect:

- direct AI-related metadata strings
- possible C2PA / Content Credentials markers
- EXIF/XMP/ICC/MakerNotes presence
- PNG metadata chunks
- device/software/container metadata
- sparse or stripped metadata footprints

The audit is heuristic. A low score does not prove a file is non-AI.

---

## Setup

Core project files:

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
  requirements.txt
  run.bat
  build_exe.bat
  verify_file.bat
```

Local runtime assets may also include:

```text
exiftool.exe
exiftool_files\
ffmpeg.exe
ffprobe.exe
good images\
```

Run from source with:

```bat
run.bat
```

Build the desktop application with:

```bat
build_exe.bat
```

The unified entry point is `app_unified.py`.

Keep **Safe copies** enabled while testing new processing workflows.
