# Metadata Repair Tool – Edits-compatible video patch v10

This build keeps the existing Topaz enhancement/interpolation workflow and persistent `vidN` numbering, but changes the final delivery encode for compatibility with iPhone/social editing apps.

Video export changes:

- H.264/AVC instead of HEVC/H.265
- NVENC (`h264_nvenc`) when available; `libx264` fallback
- `avc1` MP4 codec tag
- 8-bit `yuv420p`
- H.264 High profile, Level 4.2 (suitable for 1080p60)
- Exact CFR 60.000 FPS when the 60 FPS preset is selected
- AAC-LC style FFmpeg AAC output, stereo, 48 kHz, 192 kb/s
- MP4 `faststart`
- Encoder text tags cleared
- Removed the custom `videoai=` container metadata tag from the upscale stage

The processing model settings are unchanged: use your selected enhancement model (for example Proteus) and interpolation model (for example Chronos Fast).

Clean filenames and the persistent counter remain unchanged, e.g.:

`vid42_1080_60.mp4` → metadata repair → `vid42_1080_60_cleaned.mp4`
