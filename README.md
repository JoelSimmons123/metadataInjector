# Metadata Repair Tool v2.5

Native Windows desktop tool for repairing image and video metadata from trusted references while keeping destination-specific facts truthful.

## New in v2.5

### Dual references stay loaded at the same time
Choose one **image reference** and one **video reference**. Mixed batches are routed automatically:

- image targets → image reference
- video targets → video reference

Each queued file now shows its routing directly in the list, for example:

```text
photo.png
IMAGE REF → IMG_1234.HEIC

clip.mp4
VIDEO REF → IMG_5678.MOV
```

If a required reference has not been selected yet, the queue says `not selected`.

### AI Fingerprint Audit
Two audit controls are available:

- **Audit selected** — audit the currently selected queued file
- **Audit any file** — choose any supported media file without adding it to the repair queue

The audit reports:

- direct AI-related metadata strings
- possible C2PA / Content Credentials-style metadata markers
- EXIF, XMP, ICC and MakerNotes presence
- PNG metadata chunks
- basic device/software/container information
- sparse/stripped metadata footprints

It also shows an **AI marker signal** from `0–100` with labels such as `NONE FOUND`, `LOW`, `MEDIUM`, `HIGH`, or `INCONCLUSIVE`.

Important: the score measures the strength of **observable metadata/provenance markers only**. It is not a probability that the media is AI-generated, and a score of zero does not prove that a file is non-AI.


### Default output folder
Safe-copy mode now defaults to a `Repaired` folder beside the running app. For example:

```text
C:\Tools\MetadataRepairTool\MetadataRepairTool.exe
C:\Tools\MetadataRepairTool\Repaired\
```

The folder is created automatically when a repair is run. You can still choose a different output folder with **Browse**.

## Video behaviour

Target video timestamps belong to the **target**, not the reference. The app preserves the target's existing creation/modify/track/media timestamps and never invents reference timestamps when they were absent.

It also preserves the target's codec, resolution, frame rate, duration, rotation, HDR/Dolby Vision signalling, audio layout and encoded streams. The MP4/MOV `mdat` payload is SHA-256 checked before and after repair.

The video reference supplies only the intended reference-owned metadata such as Apple make/model/software, GPS/location accuracy and supported Apple QuickTime keys.

## Image behaviour

Image metadata is cloned from the image reference while target-specific layout stays truthful:

- target orientation is preserved
- target dimensions are preserved
- stale HEIC-only auxiliary XMP is removed from cross-format PNG output
- image data is not intentionally recompressed

## Setup

Place the official Windows ExifTool files beside `app.py` before building:

```text
MetadataRepairTool_v2.5\
  app.py
  exiftool.exe
  exiftool_files\
  build_exe.bat
```

Run `build_exe.bat`, then launch the EXE under:

```text
dist\MetadataRepairTool\MetadataRepairTool.exe
```

Keep **Safe copies** enabled while testing.
