---
name: screenstudio-to-mp4
description: Export a .screenstudio project bundle to MP4 using Python and ffmpeg without Screen Studio.
---

# Screen Studio → MP4 exporter

A `.screenstudio` bundle is a macOS package directory containing video, audio, and telemetry.

## Usage

1. Inspect bundle: `python3 scripts/inspect_bundle.py <bundle>`
2. Prepare render: `python3 scripts/prepare_render.py --bundle <bundle> --work <work> --output <output>`
3. Build cursor layer: `python3 scripts/cursor_layer.py --bundle <bundle> --work <work>`
4. Render: `zsh work/render_full.sh && zsh work/audio_build.sh && zsh work/mux.sh`
