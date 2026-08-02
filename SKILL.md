---
name: screenstudio-to-mp4
description: >
  Export a .screenstudio (Screen Studio) project bundle to MP4 with ffmpeg only — no
  Screen Studio app, no paid export — reproducing the project's effects: cuts/trims,
  click-following zooms, animated cursor + click ripples, webcam bubble, rounded
  corners + shadow, background (user-supplied "frame" image, or the project's
  gradient/color). Use when the user wants to convert, export, render, or "get an mp4
  out of" a .screenstudio bundle/package/project, or mentions rendering a Screen
  Studio recording without the app.
---

# 🎬 Screen Studio → MP4 Exporter (AI Agent Skill)

> **Instructions for AI agents (Claude Code, Antigravity, CLI assistants) to convert macOS `.screenstudio` project bundles into rendered MP4 videos using Python and ffmpeg.**

---

## 📌 Bundle Layout Quick Reference

A `.screenstudio` bundle is a macOS package directory. Inspect contents using `ls` (never `cat` binary streams):

```
X.screenstudio/
├── meta.json / project.json / recording-markers.json
└── recording/
    ├── channel-1-system-audio-0.{m3u8,m4a}   (HLS init segment + fragments)
    ├── channel-2-display-0.m3u8              (Main screen video)
    ├── channel-3-microphone-0.{m3u8,m4a}     (Mic recording)
    ├── channel-4-webcam-0.m3u8               (Webcam video, if enabled)
    ├── enhanced/…-enhanced.m4a                (AI noise-reduced voice, if generated)
    ├── cursors/*.png + cursors.json           (Sprites + hotspot metadata, POINT units)
    ├── mousemoves-0.json / mouseclicks-0.json (Telemetry streams)
    └── metadata.json                          (Session bounds & time anchors)
```

- **Media & Playlists:** `ffmpeg` reads `.m3u8` playlists directly.
- **Project State:** `project.json` stores styles under `.json.config` and edits under `.json.scenes[0]` (`slices` for cuts, `zoomRanges` for zooms).

---

## 🎛️ User CLI Arguments & Options

All flags are passed to `scripts/prepare_render.py`:

| Variable | Flag | Default | Description & Notes |
|---|---|---|---|
| **Bundle Path** | `--bundle` | *Required* | Absolute path to the `.screenstudio` package |
| **Output File** | `--output` | `~/Downloads/<name>.mp4` | Absolute destination MP4 path |
| **Frame Image** | `--frame IMG` | *None* | Custom background image (cover-cropped to canvas) |
| **Frame Blur** | `--frame-blur` | `2.0` | Background blur ($\sigma$); `0` = sharp, `6–8` = heavy blur |
| `--frame-darken` | `--frame-darken` | `0.03` | Darkening factor for screen contrast |
| **Output Width** | `--out-width` | `1920` | Output width in pixels (height follows aspect ratio) |
| **Screen Size** | `--screen-frac` | `0.78` | Screen width relative to canvas (`1.0` = edge-to-edge / no border) |
| **Webcam** | `--webcam` | `auto` | Visibility: `auto` (reads project `hideCamera`), `on`, or `off` |
| **Zooms** | `--zooms` | `on` | Enable/disable click-following zoom effects |
| **Cursor** | `--cursor` | `auto` | Animated pointer overlay (`auto`, `on`, or `off`) |
| **Quality** | `--crf` / `--preset` | `18` / `slow` | x264 quality level and encoding preset |
| **Audio Track** | `--audio` | `auto` | `auto` (uses enhanced voice if present, else mic), `mic`, `mic+system` |
| **Audio Cleanup** | `--audio-cleanup` | `loudnorm` | `none`, `loudnorm` (normalize level), or `voice` (EQ + denoise) |

---

## 🚀 Execution Workflow for AI Agents

Prerequisites: `ffmpeg` (v8+), `python3`, `Pillow` (`pip install pillow`).

### 1. Inspect the Bundle
```bash
python3 scripts/inspect_bundle.py <bundle_path>
```
Read any **WARNINGS** aloud to the user (VFR capture, silent system audio, missing wallpapers, speed ramps). Confirm user preferences (e.g. custom frame image).

### 2. Stream-Copy Raw Backup (Instant Win)
```bash
ffmpeg -i <bundle>/recording/channel-2-display-0.m3u8 \
       -i <bundle>/recording/channel-3-microphone-0.m4a \
       -map 0:v:0 -map 1:a:0 -c copy -movflags +faststart raw.mp4
```

### 3. Prepare Render Script & Assets
```bash
python3 scripts/prepare_render.py --bundle <bundle_path> --work <work_dir> --output <output_path> [flags]
```
Outputs asset masks, filtergraphs, and scripts (`render_full.sh`, `render_preview.sh`, `audio_build.sh`, `mux.sh`) into `<work_dir>`.

> [!IMPORTANT]
> Always use **absolute paths** for `--bundle`, `--work`, and `--output`.

### 4. Build Cursor Layer
```bash
python3 scripts/cursor_layer.py --bundle <bundle_path> --work <work_dir>
```
Renders mouse telemetry into `cursor.mov`.

### 5. Preview Before Full Render
```bash
zsh work/render_preview.sh
```
Renders a 9-second clip around the first zoom range using `ultrafast` preset. Verify framing, background, cursor alignment, and webcam bubble before proceeding.

### 6. Full Render & Assembly
```bash
zsh work/render_full.sh && zsh work/audio_build.sh && zsh work/mux.sh
```

### 7. Verification Procedure (Mandatory)
- Use `plan.json.verify_points` to extract frame pairs from `raw.mp4` and `output.mp4`.
- Confirm visual sync between display content and audio track.
- Verify final output duration matches expected cut timeline.

---

## 🛑 Hard-Won Technical Gotchas

> [!CAUTION]
> **Violating any of these rules will result in broken renders or audio drift:**

1. **VFR Sync Trap:** Display recordings use Variable Frame Rate (`avg_frame_rate` ~45 vs `r_frame_rate` 60). Any filter graph MUST start with `fps=60,setpts=PTS-STARTPTS` prior to `zoompan`.
2. **`crop` Cannot Animate Zooms:** `crop` parameters evaluate only once at init. Use `zoompan` for per-frame dynamic zooming.
3. **Cursor Overlay Order:** Composite the cursor onto the display stream *after* frame-rate normalization but *before* `zoompan` so it zooms naturally with the screen content.
4. **Point vs Pixel Scaling:** Mouse coordinates are recorded in logical display **points**. Scale cursor overlays by $\text{display\_px} / \text{bounds\_pt}$ (typically $2.0$).
5. **Missing Wallpapers:** Project settings with `backgroundType: "system"` refer to macOS wallpapers not stored in the bundle directory. Fall back to project gradient or pass `--frame`.
6. **Even Dimension Constraints:** x264 requires even dimensions for all geometry streams (`yuv420p` format).

---

## 📢 Known Limitations to Disclose to Users

- **Zoom Easing:** Uses smoothstep interpolation (approximates Screen Studio's damped spring without overshoot).
- **Speed Ramps (`timeScale` ≠ 1):** Currently skipped (warning displayed during inspection).
- **Captions:** Subtitles are rendered at export time by the app and are not present in `.screenstudio` bundles.
