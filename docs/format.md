# 📄 The `.screenstudio` Bundle File Format

> **Technical specification and reverse-engineered file format reference for macOS Screen Studio (`.screenstudio`) project bundles.**

---

## 📌 Overview

A `.screenstudio` file is **not a single monolithic file** — it is a standard **macOS package directory**. 

In macOS Finder, right-click the bundle and select **"Show Package Contents"** (or inspect it using `ls` in terminal). All contents are plain, unencrypted video files, audio streams, JSON metadata, and PNG image assets.

---

## 🗂 Directory Structure

Observed layout across Screen Studio 3.x project packages:

```
MyProject.screenstudio/
├── meta.json                    # App version info and creation timestamp
├── project.json                 # Complete edit state & configuration (see below)
├── recording-markers.json       # User-placed markers on the timeline
└── recording/
    ├── channel-1-system-audio-0.m3u8  # System audio HLS playlist + .m4s fragments
    ├── channel-2-display-0.m3u8       # Main display recording (H.264 HLS playlist)
    ├── channel-3-microphone-0.m3u8    # Microphone audio HLS playlist
    ├── channel-4-webcam-0.m3u8        # Webcam video recording (absent if camera disabled)
    ├── enhanced/                      # AI noise-reduced voice track (M4A format, if generated)
    ├── cursors/                       # Directory containing every PNG cursor sprite used
    │   ├── cursor-*.png
    ├── cursors.json                   # Per-sprite hotspots and standard size (in POINT units)
    ├── mousemoves-0.json              # ~10ms telemetry stream (positions & active cursor IDs)
    ├── mouseclicks-0.json             # Array of mouseDown/mouseUp event pairs
    ├── keystrokes-0.json              # Keyboard shortcut telemetry
    ├── metadata.json                  # Recorders, session bounds, and clock timing anchors
    └── polyrecorder.log               # Low-level capture engine log
```

---

## 🎥 Media & Stream Handling

Each stream is stored as **HLS/fMP4** (an initialization segment `channel-*-0000.mp4` plus `.m4s` fragments listed in an `.m3u8` playlist). 

`ffmpeg` reads `.m3u8` playlists directly without unpacking:

```bash
# Lossless raw extraction of display recording
ffmpeg -i channel-2-display-0.m3u8 -c copy output.mp4
```

> [!IMPORTANT]
> ### ⚠️ Variable Frame Rate (VFR) Trap
> Display captures use **Variable Frame Rates**: while `r_frame_rate` reports `60`, `avg_frame_rate` is typically ~`45`.
> 
> If frame-based filters (like ffmpeg's `zoompan`) are applied directly, **video will drift out of sync with audio**. The stream MUST be normalized first:
> ```filtergraph
> fps=60,setpts=PTS-STARTPTS
> ```
> `PTS-STARTPTS` resets the huge wall-clock timestamp to `0.0`.

---

## 📐 Coordinate Systems & Timing Anchors

### 1. Point Units vs. Pixel Coordinates
- Mouse telemetry (`mousemoves-0.json`) and cursor hotspot metadata (`cursors.json`) are recorded in **logical points** (e.g. `1710 × 1107`).
- Video pixels are calculated as `logical_points × display_scale` (typically Retina 2× multiplier → `3420 × 2214`).
- When compositing cursor layers onto full-res video, coordinates must be scaled by `scale_factor = video_pixels / logical_points`.

### 2. Event Timestamp Alignment
Event timestamps in JSON files use absolute process timestamps (`processTimeMs`). The relative video timestamp (`t_video_seconds`) is anchored via `metadata.json`:

$$\text{t\_video\_seconds} = \frac{\text{processTimeMs} - \text{processTimeStartMs}}{1000}$$

Where `processTimeStartMs` is the start timestamp of the display recorder session.

---

## ⚙️ Edit State (`project.json`)

All editing settings are stored under the root `.json` key in `project.json`:

### 1. Timeline & Cuts (`scenes[0].slices`)
Contains an array of kept video spans:
```json
[
  { "sourceStartMs": 0, "sourceEndMs": 4200, "timeScale": 1.0 },
  { "sourceStartMs": 5800, "sourceEndMs": 12500, "timeScale": 1.0 }
]
```

### 2. Click Zooms (`scenes[0].zoomRanges`)
Defines zoom animation targets across time:
- `startTime` / `endTime`: Source timestamps (in ms).
- `zoom`: Magnification factor (e.g. `1.5`, `2.0`).
- `type`: `follow-click-groups` (centers on mouse click centroid) or `manual` (uses `manualTargetPoint`).

### 3. Styling & Config (`config`)
Contains project styling properties (~70 key-value pairs):
- **Background:** `backgroundType`, `backgroundColor`, `backgroundGradient`, `backgroundSystemName`
- **Window Geometry:** `backgroundPaddingRatio`, `windowBorderRadius`, `shadowOpacity`, `shadowRadius`
- **Webcam Bubble:** `cameraPosition`, `cameraSize`, `cameraCornerRadius`, `hideCamera`
- **Cursor Settings:** `cursorSize`, `hideCursor`, `clickEffect`

> [!NOTE]
> `backgroundSystemName` (e.g., `macOS/tahoe-light.jpg`) refers to local macOS wallpapers built into the Screen Studio app. They are **not stored** inside the bundle directory.

---

## 🛠️ Interoperability & Script Pipeline

Because `.screenstudio` bundles contain raw HLS media streams, full vector telemetry, and JSON project configurations, full video playback and effect reproduction can be executed headlessly:

1. **Normalize VFR streams** via `fps=60,setpts=PTS-STARTPTS`.
2. **Overlay cursor telemetry** rendered by [scripts/cursor_layer.py](../scripts/cursor_layer.py).
3. **Execute zoom/pan animation** driven by `zoomRanges` and click centroids via [scripts/prepare_render.py](../scripts/prepare_render.py).
4. **Composite background, shadow, and webcam mask**.
5. **Apply cut slices** and mux processed audio streams.
