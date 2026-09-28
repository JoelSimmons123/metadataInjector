v2.11.0 — Simple video pipeline UI
=================================

The old video controls exposed implementation details ("Topaz captions",
"captions before normal Process", separate Topaz/Process buttons).

v2.11 replaces that with one obvious video pipeline:

    [x] 1. Upscale  ->  [x] 2. Caption  ->  [x] 3. Metadata repair
                                      [ Run selected pipeline ]

Examples
--------

Fresh video needing everything:
    Upscale: ON
    Caption: ON
    Metadata repair: ON

Already upscaled video needing captions + metadata:
    Upscale: OFF
    Caption: ON
    Metadata repair: ON

Already captioned/upscaled video needing metadata only:
    Upscale: OFF
    Caption: OFF
    Metadata repair: ON

Upscale only:
    Upscale: ON
    Caption: OFF
    Metadata repair: OFF

Caption only:
    Upscale: OFF
    Caption: ON
    Metadata repair: OFF

The underlying Topaz, Whisper captioning, metadata repair, cleanup, retry and
verification implementations are unchanged. The new UI simply chooses which
existing stages run.

Advanced Topaz and caption style settings remain visible below the simple
pipeline card. The old route-selection checkboxes and old action buttons are
hidden so there is one obvious video action.
