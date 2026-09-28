Metadata Repair Tool v2.11.1 — UI readability cleanup
=======================================================

This update changes layout only. Media-processing behavior is unchanged.

Main changes
------------
- Video pipeline remains visible as the main workflow.
- "Video enhancement" is collapsed by default.
- "Automatic video captions" is collapsed by default.
- New buttons in the pipeline card:
    Topaz settings
    Caption settings
- Only one advanced section can be expanded at a time.
- Base UI font size increased.
- Small labels increased in size.
- Buttons slightly enlarged.
- Outer margins/spacing reduced.
- File queue minimum height reduced slightly so the entire workflow fits better
  on shorter-height displays.

The intended normal workflow is now visually simple:

    [x] Upscale  ->  [x] Caption  ->  [x] Metadata repair
                       Run selected pipeline

Open the advanced settings only when you actually need to change a model,
resolution, FPS option, caption font, word count, etc.
