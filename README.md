# Metadata Repair Tool v2.2

Native Windows desktop tool for repairing image and video metadata from a trusted reference while keeping destination-specific facts truthful.

## v2.2 video rules

Video timestamps now belong to the **target**, not the reference.

The app copies reference-owned device/location metadata such as Apple make, model, software, GPS/location accuracy and the Apple full-frame-rate playback-intent key. It does **not** copy the reference video's creation, modification, track or media dates.

For each target video, v2.2:

- preserves its existing QuickTime movie `CreateDate` / `ModifyDate`
- preserves its track `TrackCreateDate` / `TrackModifyDate`
- preserves its `MediaCreateDate` / `MediaModifyDate`
- restores target date fields inside Keys/XMP/UserData/ItemList if stripping temporarily removes them
- does not invent reference dates when the target had no embedded date
- preserves filesystem access/modified times and, on Windows, restores the source file's Creation/Access/Write FILETIMEs to the repaired copy
- preserves codec, resolution, frame rate, duration, rotation, HDR/Dolby Vision signalling, audio layout and all encoded media streams
- SHA-256 verifies the QuickTime/MP4 `mdat` media payload before and after repair

### Native Apple playback-intent representation

ExifTool may create `com.apple.quicktime.full-frame-rate-playback-intent` as a UTF-8 value when adding it to an MP4. v2.2 performs an in-place post-write normalisation so its QuickTime `data` atom uses native signed-integer type **21**, matching the iPhone-style representation. Atom sizes are not changed, and the result is verified before success is reported.

## Image rules

Image behaviour from v1.6 remains: reference metadata is cloned while orientation/dimensions stay truthful to the target, stale HEIC-only auxiliary XMP references are removed on cross-format PNG output, and encoded image data is not intentionally recompressed.

## Setup

Place the official Windows ExifTool files beside `app.py` before building:

```text
MetadataRepairTool_v2.2\
  app.py
  exiftool.exe
  exiftool_files\
  build_exe.bat
```

Run `build_exe.bat`, then launch the EXE under `dist\MetadataRepairTool\`.

Keep **Safe copies** enabled while testing.
