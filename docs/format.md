# The .screenstudio file format

A `.screenstudio` file is a macOS package directory containing plain, unencrypted video, audio, and JSON metadata.

```
MyProject.screenstudio/
├── meta.json                    # app version, createdAt
├── project.json                 # ALL edit state
├── recording-markers.json
└── recording/
    ├── channel-1-system-audio-0.m3u8
    ├── channel-2-display-0.m3u8      # main screen capture, h264
    ├── channel-3-microphone-0.m3u8
    ├── channel-4-webcam-0.m3u8       # webcam, h264
    ├── enhanced/                     # noise-reduced voice
    ├── cursors/*.png                 # cursor sprites
    ├── cursors.json                  # hotspots and sizes
    ├── mousemoves-0.json             # position telemetry
    ├── mouseclicks-0.json            # click pairs
    └── metadata.json                 # timing anchors
```
