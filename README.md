# screenstudio-to-mp4

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/salatech/screenstudio-to-mp4/blob/main/LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/salatech/screenstudio-to-mp4)](https://github.com/salatech/screenstudio-to-mp4/stargazers)
[![GitHub issues](https://img.shields.io/github/issues/salatech/screenstudio-to-mp4)](https://github.com/salatech/screenstudio-to-mp4/issues)

> **Export macOS Screen Studio `.screenstudio` projects to MP4 — completely free, offline, with no app required.**

Reproduces click-following zooms, animated mouse cursors, click ripples, webcam bubbles, rounded corners, drop shadows, custom background frames, and audio cleanup using **Python + ffmpeg**.

---

## Why screenstudio-to-mp4?

[Screen Studio](https://screen.studio) is a fantastic macOS screen recorder. While recording and editing are free, **exporting to MP4 requires a paid subscription** (~$108/year). 

`.screenstudio` files are standard macOS package directories containing unencrypted video, audio, and mouse telemetry. **screenstudio-to-mp4** parses this project data to render finished, high-quality MP4 videos right on your own machine.

> [!NOTE]
> **Disclaimer:** This project is independent and not affiliated with or endorsed by Screen Studio. It reads `.screenstudio` bundle files for interoperability with your own recordings.

---

## Features & Effect Reproduction

| Effect / Feature | Support | How It Works |
|---|:---:|---|
| **Cuts & Trims (`slices`)** | ✅ Exact | Applies exact project cut points to video and audio |
| **Click Zooms (`zoomRanges`)** | ✅ Smooth | Calculates targets from click cluster telemetry with smoothstep easing |
| **Animated Cursor & Ripples** | ✅ Exact | Re-renders mouse pointer & click ripple animation from telemetry |
| **Webcam Bubble** | ✅ Exact | Preserves project webcam position, size, and corner rounding |
| **Rounded Corners & Shadow** | ✅ Exact | Composites customizable corner radius and drop shadow |
| **Background (Gradient/Color)** | ✅ Exact | Extracts original project background gradient or solid color |
| **Custom Frame Image** | ✅ Custom | Pass `--frame wallpaper.jpg` to use your own background |
| **Audio Processing** | ✅ Enhanced | Mic, system, enhanced voice, or a silent track if none exist |
| **Cuts, collapse & speed (`slices`)** | ✅ Exact | Kept ranges only; `timeScale = 1/speed` (e.g. `0.125` = 8× faster) |
| **Motion blur** | ✅ Approx. | Light frame blending from the project's `motionBlurAmount` |
| **Captions** | ✅ If present | Burns in a `.srt` / `.vtt` found in the bundle or passed via `--captions` |
| **System wallpaper** | ✅ If on disk | Uses the macOS / Screen Studio wallpaper when the file exists |

### Current Limitations

- **Captions without a file:** Screen Studio often sets `showTranscript=true` but does not store the words in the bundle. Drop an `.srt`/`.vtt` next to the project or pass `--captions`.
- **System wallpapers:** Extracted from Screen Studio.app when that app is installed. If it is missing, the project's gradient is used (or pass `--frame`).
- **Zoom easing:** Smoothstep approximates Screen Studio's spring (no overshoot).

---

## Prerequisites & Installation

### Requirements

| Tool | Minimum Version | Installation Command |
|---|---|---|
| **macOS** | Any | Required (`.screenstudio` packages are macOS directories) |
| **Python** | 3.8+ | Pre-installed on macOS |
| **ffmpeg** | 8.0+ | `brew install ffmpeg` |
| **Pillow** | Latest | `pip3 install pillow` |

### Installation

Clone the repository to your local environment:

```bash
git clone https://github.com/salatech/screenstudio-to-mp4.git
cd screenstudio-to-mp4
```

> [!TIP]
> No virtual environment, build step, or extra dependencies required!

---

## Quick Start Guide

### Option 0: macOS App (easiest — no coding)

1. Build once (for you / maintainers):
   ```bash
   brew install ffmpeg
   pip3 install pillow pyinstaller
   python3 build_app.py
   ```
2. Open `dist/screenstudio-to-mp4.dmg`
3. Drag **screenstudio-to-mp4** into **Applications**
4. Double-click the app — your browser opens the exporter

> First open on a new Mac: right-click the app → **Open** (macOS Gatekeeper).

---

### Option 1: Easy Web GUI (from source)

```bash
cd screenstudio-to-mp4
python3 web_gui.py
```

This opens a local page in your browser (`http://127.0.0.1:8600`). Pick a recording, click **Export to MP4**, and wait for the progress bar. Nothing is uploaded — everything stays on your Mac.

---

### Option 2: Fast Raw Video Export (Seconds)

If you only need the raw recording with microphone audio (without cursor, zooms, or background frames):

```bash
ffmpeg -i ~/path/to/YourRecording.screenstudio/recording/channel-2-display-0.m3u8 \
       -i ~/path/to/YourRecording.screenstudio/recording/channel-3-microphone-0.m4a \
       -map 0:v:0 -map 1:a:0 -c copy -movflags +faststart ~/Downloads/raw.mp4
```

---

### Option 3: Full Effect Export (from source / CLI)

Copy and run the script block below. Simply update `BUNDLE` and `OUTPUT` to your local file paths:

```bash
# Set your input bundle and output destination
BUNDLE="$HOME/Desktop/MyRecording.screenstudio"
OUTPUT="$HOME/Downloads/MyRecording.mp4"

# 1. Inspect the project bundle for warnings and stream details
python3 scripts/inspect_bundle.py "$BUNDLE"

# 2. Generate render plan, filtergraphs, and composite assets
python3 scripts/prepare_render.py \
  --bundle "$BUNDLE" \
  --work "$HOME/screenstudio-to-mp4/work" \
  --output "$OUTPUT"

# 3. Render transparent animated cursor overlay
python3 scripts/cursor_layer.py \
  --bundle "$BUNDLE" \
  --work "$HOME/screenstudio-to-mp4/work"

# 4. Render video, process audio, and mux into final MP4
zsh work/render_full.sh && zsh work/audio_build.sh && zsh work/mux.sh

echo "🎉 Export complete! Saved to: $OUTPUT"
```

> [!TIP]
> **Want a quick preview first?** Run a 9-second sample before starting the full render:
> ```bash
> zsh work/render_preview.sh && open work/preview.mp4
> ```

---

## Complete Step-by-Step Workflow

### Step 1: Inspect the Project Bundle
```bash
python3 scripts/inspect_bundle.py ~/path/to/Recording.screenstudio
```
Analyzes bundle metadata and prints resolution, frame rate, duration, audio streams, zoom points, and warnings (e.g. VFR video, missing wallpapers, or silent channels).

---

### Step 2: Prepare Assets & Filtergraphs
```bash
python3 scripts/prepare_render.py \
  --bundle ~/path/to/Recording.screenstudio \
  --work ~/screenstudio-to-mp4/work \
  --output ~/Downloads/MyVideo.mp4
```
Generates render configurations inside the `work/` directory:
- `bg.png` — Background canvas (gradient, color, or custom image)
- `screen_mask.png` & `webcam_mask.png` — Geometry masks
- `shadow.png` — Drop shadow overlay
- `render_full.sh`, `render_preview.sh`, `audio_build.sh`, `mux.sh` — Execution scripts
- `plan.json` — Detailed frame geometry and verify markers

> [!IMPORTANT]
> Always pass **absolute paths** for `--bundle`, `--work`, and `--output` to avoid path resolution errors in generated shell scripts.

---

### Step 3: Render Animated Cursor Layer
```bash
python3 scripts/cursor_layer.py \
  --bundle ~/path/to/Recording.screenstudio \
  --work ~/screenstudio-to-mp4/work
```
Renders the mouse telemetry into a transparent overlay video (`cursor.mov`). Takes ~20–30 seconds for a 3-minute recording.

*(To disable cursor rendering, pass `--cursor off` in Step 2)*.

---

### Step 4: Final Render & Muxing
```bash
zsh work/render_full.sh && zsh work/audio_build.sh && zsh work/mux.sh
```
- `render_full.sh`: Renders the composite video stream with zoom/pan animation.
- `audio_build.sh`: Extracts and processes selected audio streams with volume normalization.
- `mux.sh`: Combines video and audio into your output MP4 file.

---

## Configuration & Customization Flags

Options passed to `scripts/prepare_render.py`:

### Background & Framing
| Flag | Default | Description |
|---|---|---|
| `--frame PATH` | *None* | Path to a custom background image (auto-cropped to canvas) |
| `--frame-blur SIGMA` | `2.0` | Background blur intensity (`0` = sharp, `6-8` = heavy blur) |
| `--frame-darken AMOUNT` | `0.03` | Background darkening factor (improves screen contrast) |

### Canvas & Composition
| Flag | Default | Description |
|---|---|---|
| `--out-width PX` | `0` (recording size) | Output video width in pixels; `0` matches the capture. Height follows the recording aspect |
| `--screen-frac RATIO` | project padding | Screen width relative to canvas (`1.0` = borderless). Default uses `backgroundPaddingRatio` |
| `--webcam MODE` | `auto` | Webcam visibility: `auto` (uses project setting), `on`, or `off` |
| `--webcam-margin PX` | `40` | Pixel padding around the webcam bubble |

### Zooms & Cursor
| Flag | Default | Description |
|---|---|---|
| `--zooms MODE` | `on` | Enable (`on`) or disable (`off`) click-following zoom effects |
| `--zoom-ease SECONDS` | `0.6` | Easing transition duration in seconds |
| `--cursor MODE` | `auto` | Animated cursor: `auto` (detects telemetry), `on`, or `off` |

### Quality & Audio
| Flag | Default | Description |
|---|---|---|
| `--crf VALUE` | `18` | x264 quality level (lower = higher quality; `18` is visually lossless) |
| `--preset SPEED` | `slow` | Encoding preset (`ultrafast`, `fast`, `medium`, `slow`, `veryslow`) |
| `--audio MODE` | `auto` | `auto` (enhanced → mic → system → silence), `mic`, `enhanced`, `system`, `mic+system`, `silence` |
| `--audio-cleanup MODE` | `loudnorm` | Audio filter: `none`, `loudnorm` (normalize level), or `voice` (EQ + denoise) |
| `--captions PATH` | *auto* | Burn in an `.srt`/`.vtt` (also auto-detected in the bundle) |
| `--motion-blur MODE` | `auto` | `auto` (from project), `on`, or `off` |

---

## Popular Usage Recipes

### 1. Borderless / Full-Screen Export (No Background Canvas)
```bash
python3 scripts/prepare_render.py \
  --bundle ~/path/to/Recording.screenstudio \
  --work ~/screenstudio-to-mp4/work \
  --output ~/Downloads/full_screen.mp4 \
  --screen-frac 1.0
```

### 2. Custom Wallpaper Background
```bash
python3 scripts/prepare_render.py \
  --bundle ~/path/to/Recording.screenstudio \
  --work ~/screenstudio-to-mp4/work \
  --output ~/Downloads/custom_bg.mp4 \
  --frame ~/Pictures/wallpaper.jpg \
  --frame-blur 6
```

### 3. Maximum Quality Render
```bash
python3 scripts/prepare_render.py \
  --bundle ~/path/to/Recording.screenstudio \
  --work ~/screenstudio-to-mp4/work \
  --output ~/Downloads/high_quality.mp4 \
  --crf 14 --preset veryslow
```

---

## Anatomy of a `.screenstudio` File

A `.screenstudio` file is a standard macOS package directory. Right-click the file in Finder and select **"Show Package Contents"** to inspect:

```
MyProject.screenstudio/
├── meta.json                         # Project metadata & creation date
├── project.json                      # Edit timeline (zooms, slices, style settings)
├── recording-markers.json            # Marker timestamps
└── recording/
    ├── channel-1-system-audio-0.m3u8 # System audio HLS stream
    ├── channel-2-display-0.m3u8      # Main display HLS stream (H.264)
    ├── channel-3-microphone-0.m3u8   # Microphone HLS stream
    ├── channel-4-webcam-0.m3u8       # Webcam video stream (optional)
    ├── enhanced/                     # Noise-reduced voice audio (optional)
    ├── cursors/*.png                 # Cursor sprite images
    ├── cursors.json                  # Hotspot coordinates & sprite metadata
    ├── mousemoves-0.json             # High-frequency mouse position telemetry
    ├── mouseclicks-0.json            # Mouse click event pairs
    └── metadata.json                 # Screen bounds and clock timing anchors
```

*For in-depth specifications, refer to [docs/format.md](docs/format.md).*

---

## Frequently Asked Questions & Troubleshooting

<details>
<summary><b>Error opening input file ./work/bg.png</b></summary>

> Relative paths were passed to `--work`. Always use **absolute paths** (e.g., `~/screenstudio-to-mp4/work` or `/Users/username/screenstudio-to-mp4/work`).
</details>

<details>
<summary><b>No cursor appears in the exported video</b></summary>

> OS cursors are not baked into Screen Studio video files. Ensure you run `python3 scripts/cursor_layer.py` before executing the render script.
</details>

<details>
<summary><b>Missing PIL module (No module named 'PIL')</b></summary>

> Install Pillow via pip: `pip3 install pillow`.
</details>

<details>
<summary><b>Wallpaper falls back to a purple gradient</b></summary>

> Default macOS system wallpapers are referenced by system path and not saved inside project bundles. Supply your own image using the `--frame /path/to/image.png` option.
</details>

<details>
<summary><b>Video and audio become desynchronized</b></summary>

> Screen Studio display recordings use Variable Frame Rates (VFR). `screenstudio-to-mp4` automatically handles stream normalization using `fps=60,setpts=PTS-STARTPTS`.
</details>

---

## Project Structure

```
screenstudio-to-mp4/
├── README.md                  # Project documentation
├── LICENSE                    # MIT License
├── web_gui.py                 # Browser-based exporter GUI
├── exporter.py                # Shared export pipeline
├── build_app.py               # Build macOS .app + .dmg
├── SKILL.md                   # Agent skill instructions
├── llms.txt                   # LLM context reference
├── docs/
│   └── format.md              # Detailed .screenstudio format documentation
└── scripts/
    ├── inspect_bundle.py      # Bundle parser & diagnostic script
    ├── prepare_render.py      # Render pipeline generator
    ├── cursor_layer.py        # Animated cursor renderer
    └── render_lib.py          # Shared path, slice, audio, and speed-ramp helpers
```

---

## Author & Acknowledgements

Created & Maintained by **Abdulrahmon Solahudeen** ([@salatech](https://github.com/salatech)).

Inspired by [screenstudio-export](https://github.com/vignesh-sabhahit/screenstudio-export).

Distributed under the **[MIT License](LICENSE)**. Feel free to use, modify, and distribute.

---

<div align="center">
  <b>If screenstudio-to-mp4 saved you a subscription, give it a ⭐ on <a href="https://github.com/salatech/screenstudio-to-mp4">GitHub</a>!</b>
</div>
