# Metadata Repair Tool v2.8.4

## v2.8.4 — preserve video orientation and shape during Topaz upscale

The upscale stage reads an actual decoded source frame before choosing its canvas. The 1080p/720p preset sets the shorter side and keeps the input aspect ratio, including square and 4:3 video. For portrait 9:16, 1080p still means exactly 1080×1920. After Topaz finishes, the app decodes its output and rejects/deletes it if the output dimensions differ from those requested. This prevents the observed 720×1280 input becoming a tiny portrait frame in a 1920×1080 landscape canvas. Previously damaged files need to be upscaled again from the original source.

## v2.8.3 — clear AAC encoder identification

When FFmpeg's version identifier occurs in the known AAC fill-element layout, the video path replaces only those identifier bytes. Packet lengths and timestamps stay the same. Verification compares every video packet, compares audio packets after this exact normalisation, and confirms decoded audio is byte-for-byte identical. Unknown `Lavc` locations fail the job instead of being changed blindly. This removes a visible encoder string; it does not prove camera origin or the absence of invisible watermarking.

## v2.8.2 — upscale preset default

The Topaz output preset now starts at **1080p60**. Other presets remain available.

Native Windows desktop tool for repairing image and video metadata from trusted references while keeping destination-specific media facts intact.

## v2.8.1 — clear the FFmpeg video vendor field

The MOV remux clears the four-byte `FFMP` video sample-entry vendor field to an unspecified value, like the provided `v1.mov` reference. This changes only that field in the MOV header. The app checks the completed file for surviving FFmpeg vendor/encoder metadata. The absence of that field does not establish that an iPhone recorded the video; the codec bitstream and other container details can still reveal processing.

## v2.8.0 — one-click QuickTime video output

Video targets produce `.mov` files with a QuickTime `qt` container and Core Media track handler labels. FFmpeg copies encoded streams without re-encoding, while ExifTool transfers the selected Make/Model/Software keys from the video reference. The video path never copies GPS, location accuracy, the reference capture time or playback intent. A post-write audit rejects location and obvious AI provenance tags; packet verification checks that media changes are limited to the known AAC identification bytes.

The output does not prove physical iPhone capture. Its actual codec, resolution, frame rate and rotation come from the target. Unrecognised streams or codecs that QuickTime cannot hold cause a clear error rather than a lossy conversion.

Install FFmpeg with `ffmpeg.exe` and `ffprobe.exe` on PATH, or put both beside `app.py` and `build_exe.bat`. The build script includes both executables in the app folder when they are beside the source. No video options need selecting; the normal Process button runs the new pipeline. When Replace originals is selected, the original source is retained in a `.metadatarepair_backup` file.

## New in v2.6.0 — optional AI image pixel cleanup

The image workflow now has an **AI image cleanup** panel. When enabled, image targets are first passed through the separately installed [`remove-ai-watermarks`](https://github.com/wiltodelta/remove-ai-watermarks) tool, then the existing metadata repair engine runs on the cleaned pixels.

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

- **Off by default.** Normal metadata-only repair behaves exactly as before.
- **Images only.** Video repair is unchanged.
- **Safe copies only in v2.6.** AI cleanup is disabled when **Replace originals** is enabled.
- **SDXL + Z-Image is the recommended default.** Qwen + Z-Image remains available as an experimental choice.
- **One batch / one model load.** Multiple image targets are sent through the scrubber's batch mode so the diffusion stack is reused instead of reloaded for every file.
- The scrubber runs from a temporary staging directory and the temporary cleaned files are deleted after metadata repair.
- The original image dimensions are checked after pixel cleanup. A dimension change stops the repair instead of silently continuing.
- Original filesystem timestamps and non-default orientation are carried into the temporary cleaned file so the existing repair engine sees the target's original layout/timing context.
- Verification details show whether AI pixel cleanup ran and which pipeline was used.

Pixel cleanup is a lossy image-generation operation and **does alter pixels**. Leave it disabled when you only want metadata repair.

### Installing the optional AI scrubber

The Metadata Repair Tool intentionally does **not** bundle PyTorch, CUDA or the diffusion models into its EXE. Install the scrubber separately with `uv`:

```powershell
uv tool install --force "remove-ai-watermarks[qwen-zimage]"
```

On a standard Windows `uv` install the app automatically checks:

```text
%USERPROFILE%\.local\bin\remove-ai-watermarks.exe
```

You can also click **Locate scrubber** in the app and select the executable manually.

The AI subprocess uses the user's home directory as its working directory, so DiffSynth model files are reused from the same `models` cache used by normal command-line runs rather than being downloaded beside the Metadata Repair Tool EXE.

### AI cleanup troubleshooting surfaced by the GUI

v2.6 recognises several common failures and reports a more useful message:

- Windows native GPU crash `0xC0000005` / `-1073741819`
- corrupted/incomplete Z-Image `.safetensors` files
- Windows paging-file error 1455
- NVIDIA GPU detected but PyTorch has no working CUDA backend

For a corrupted Z-Image cache, delete:

```text
%USERPROFILE%\models\Tongyi-MAI\Z-Image-Turbo
```

and retry so it downloads again.

## v2.5.1 — dual references and AI Fingerprint Audit

Choose one **image reference** and one **video reference**. Mixed batches are routed automatically:

- image targets → image reference
- video targets → video reference

Each queued file shows its routing directly in the list, for example:

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

## Default output folder

Safe-copy mode defaults to a `Repaired` folder beside the running app. For example:

```text
C:\Tools\MetadataRepairTool\MetadataRepairTool.exe
C:\Tools\MetadataRepairTool\Repaired\
```

The folder is created automatically when a repair is run. You can still choose a different output folder with **Browse**.

## Video behaviour

Video outputs are QuickTime MOV safe copies by default. The remux strips user metadata, timestamps and location tags, then transfers only selected device make/model/software fields from the video reference. The app checks the resulting codec, dimensions, frame rate, rotation and encoded packets. It rejects location and explicit AI provenance metadata if any survive.

## Image behaviour

With AI cleanup **disabled**, image metadata is cloned from the image reference while target-specific layout stays truthful:

- target orientation is preserved
- target dimensions are preserved
- stale HEIC-only auxiliary XMP is removed from cross-format PNG output
- image data is not intentionally recompressed

With AI cleanup **enabled**, the selected diffusion pipeline intentionally regenerates image pixels first; the same metadata/layout repair and verification rules then run on that cleaned image.

## Setup

Place the official Windows ExifTool files beside the source before building:

```text
metadataInjector\
  app.py
  app_unified.py
  exiftool.exe
  exiftool_files\
  ffmpeg.exe
  ffprobe.exe
  build_exe.bat
```

Run:

```text
build_exe.bat
```

The unified build entry point is `app_unified.py`; it imports the `app.py` metadata engine. The EXE is produced under:

```text
dist\MetadataRepairTool\MetadataRepairTool.exe
```

For source-mode testing, run `run.bat`.

Keep **Safe copies** enabled while testing, especially when AI pixel cleanup is enabled.
