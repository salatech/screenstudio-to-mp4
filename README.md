# 🎬 screenstudio-to-mp4

Export macOS Screen Studio `.screenstudio` projects to MP4 — free, no app, no subscription.

Reproduces zooms, animated cursor, click ripples, webcam bubble, rounded corners, shadow, and backgrounds using Python + ffmpeg.

## Prerequisites

- macOS
- Python 3.8+
- Pillow (`pip3 install pillow`)
- ffmpeg 8+ (`brew install ffmpeg`)

## Quick start

```bash
python3 scripts/inspect_bundle.py ~/path/to/Recording.screenstudio
python3 scripts/prepare_render.py --bundle ~/path/to/Recording.screenstudio --work ~/screenstudio-to-mp4/work --output ~/Downloads/Recording.mp4
python3 scripts/cursor_layer.py --bundle ~/path/to/Recording.screenstudio --work ~/screenstudio-to-mp4/work
zsh work/render_full.sh && zsh work/audio_build.sh && zsh work/mux.sh
```
