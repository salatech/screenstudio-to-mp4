#!/usr/bin/env python3
"""web_gui.py: Web-based Desktop Interface for screenstudio-to-mp4.

Features automatic system bundle scanning, smart path resolution, native HTML5
file pickers, drag-and-drop, and real-time export progress tracking.
"""

import os
import sys
import json
import time
import shutil
import urllib.parse
import threading
import subprocess
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler

# Ensure exporter script is accessible
sys.path.insert(0, os.path.dirname(__file__))
from exporter import RenderExporter, find_ffmpeg


class ProgressTracker:
    def __init__(self):
        self.message = "Ready for export."
        self.percentage = 0.0
        self.status = "idle"  # idle, rendering, success, error
        self.lock = threading.Lock()

    def update(self, msg: str, pct: float):
        with self.lock:
            self.message = msg
            if pct >= 0:
                self.percentage = pct
            if pct >= 100.0:
                self.status = "success"
            elif pct < 0:
                self.status = "error"

    def get_state(self):
        with self.lock:
            return {
                "message": self.message,
                "percentage": self.percentage,
                "status": self.status
            }


global_tracker = ProgressTracker()


def scan_system_bundles() -> list:
    """Scan common macOS directories for .screenstudio project packages."""
    home = os.path.expanduser("~")
    search_dirs = [
        os.path.join(home, "Desktop"),
        os.path.join(home, "Downloads"),
        os.path.join(home, "Movies"),
        os.path.join(home, "Documents"),
        os.path.join(home, "Movies", "Screen Studio"),
        home
    ]
    found = []
    for d in search_dirs:
        if not os.path.exists(d):
            continue
        try:
            for item in os.listdir(d):
                if item.endswith(".screenstudio"):
                    full_path = os.path.join(d, item)
                    if os.path.isdir(full_path):
                        found.append(full_path)
        except Exception:
            pass
    return sorted(list(set(found)))


def resolve_bundle_path(input_path: str) -> str:
    """Resolve relative file/folder names or webkit paths to absolute POSIX paths."""
    if not input_path:
        return ""

    input_path = input_path.strip().strip('"').strip("'")
    if os.path.isabs(input_path) and os.path.exists(input_path):
        return input_path

    filename = os.path.basename(input_path)
    # Check scanned bundles for exact match
    for bundle in scan_system_bundles():
        if os.path.basename(bundle) == filename or bundle.endswith(input_path):
            return bundle

    # Search in common user folders
    home = os.path.expanduser("~")
    for parent in [home, os.path.join(home, "Desktop"), os.path.join(home, "Downloads"), os.path.join(home, "Movies"), os.path.join(home, "Documents")]:
        cand = os.path.join(parent, filename)
        if os.path.exists(cand):
            return cand

    return input_path


HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>screenstudio-to-mp4 Exporter</title>
  <style>
    :root {
      --bg: #1e1e2e;
      --card: #2a2a3c;
      --accent: #89b4fa;
      --text: #cdd6f4;
      --subtext: #a6adc8;
      --input: #11111b;
      --success: #a6e3a1;
      --error: #f38ba8;
    }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      margin: 0;
      padding: 30px 20px;
      display: flex;
      justify-content: center;
    }
    .container {
      max-width: 680px;
      width: 100%;
    }
    h1 {
      color: var(--accent);
      margin: 0 0 4px 0;
      font-size: 24px;
    }
    p.subtitle {
      color: var(--subtext);
      margin: 0 0 20px 0;
      font-size: 13px;
    }
    .card {
      background: var(--card);
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 16px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.25);
    }
    .drop-zone {
      border: 2px dashed #45475a;
      border-radius: 8px;
      padding: 24px;
      text-align: center;
      color: var(--subtext);
      margin-bottom: 16px;
      cursor: pointer;
      transition: all 0.2s ease;
      font-weight: 500;
    }
    .drop-zone:hover, .drop-zone.dragover {
      border-color: var(--accent);
      background: rgba(137, 180, 250, 0.08);
      color: var(--text);
    }
    .field {
      margin-bottom: 14px;
    }
    .field:last-child {
      margin-bottom: 0;
    }
    label {
      display: block;
      font-size: 12px;
      font-weight: 600;
      margin-bottom: 6px;
      color: var(--text);
    }
    .input-row {
      display: flex;
      gap: 8px;
    }
    input[type="text"] {
      flex: 1;
      background: var(--input);
      border: 1px solid #313244;
      color: var(--text);
      padding: 10px 12px;
      border-radius: 8px;
      font-size: 13px;
    }
    button, .btn-browse {
      background: #313244;
      color: var(--text);
      border: none;
      padding: 10px 16px;
      border-radius: 8px;
      font-weight: 600;
      font-size: 12px;
      cursor: pointer;
      transition: background 0.2s;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      text-decoration: none;
    }
    button:hover, .btn-browse:hover {
      background: var(--accent);
      color: #11111b;
    }
    button.btn-primary {
      background: var(--accent);
      color: #11111b;
      width: 100%;
      padding: 14px;
      font-size: 15px;
      margin-top: 10px;
    }
    button.btn-primary:hover {
      background: #b4befe;
    }
    button:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
    .grid-2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }
    select, input[type="range"] {
      width: 100%;
      background: var(--input);
      color: var(--text);
      border: 1px solid #313244;
      padding: 8px;
      border-radius: 8px;
      font-size: 12px;
    }
    .progress-bar-bg {
      background: var(--input);
      height: 12px;
      border-radius: 6px;
      overflow: hidden;
      margin-top: 12px;
    }
    .progress-bar-fill {
      background: var(--accent);
      height: 100%;
      width: 0%;
      transition: width 0.3s ease;
    }
    .status-msg {
      font-size: 13px;
      color: var(--subtext);
      margin-top: 8px;
    }
  </style>
</head>
<body onload="initApp()">
  <!-- Hidden Native File Pickers -->
  <input type="file" id="nativeFilePicker" style="display:none" onchange="onNativeFileSelected(event)">
  <input type="file" id="nativeFramePicker" style="display:none" accept="image/*" onchange="onNativeFrameSelected(event)">

  <div class="container">
    <h1>🎬 screenstudio-to-mp4</h1>
    <p class="subtitle">Export macOS Screen Studio recordings to MP4 — free, offline, no subscription.</p>

    <div class="card">
      <!-- Quick Scan Selector -->
      <div class="field" id="scanSection" style="display:none;">
        <label>✨ Found Recordings on your Mac:</label>
        <select id="scannedSelect" onchange="onScannedSelected(this.value)">
          <option value="">-- Choose a detected .screenstudio recording --</option>
        </select>
      </div>

      <div class="drop-zone" id="dropZone" 
           onclick="document.getElementById('nativeFilePicker').click()"
           ondragover="event.preventDefault(); this.classList.add('dragover');" 
           ondragleave="this.classList.remove('dragover');" 
           ondrop="handleDrop(event)">
        📥 Drag & Drop your .screenstudio file here, or click to choose
      </div>

      <div class="field">
        <label>📁 .screenstudio Recording Package Path:</label>
        <div class="input-row">
          <input type="text" id="bundlePath" placeholder="/Users/.../Recording.screenstudio" onchange="resolvePath(this.value)">
          <button class="btn-browse" onclick="document.getElementById('nativeFilePicker').click()">Browse...</button>
        </div>
      </div>

      <div class="field">
        <label>💾 Output MP4 Destination Path:</label>
        <div class="input-row">
          <input type="text" id="outputPath" placeholder="/Users/.../Downloads/Output.mp4">
        </div>
      </div>
    </div>

    <div class="card">
      <label style="font-size: 14px; margin-bottom: 12px;">⚙️ Customization Options</label>
      
      <div class="field">
        <label>Custom Background Frame Image (Optional):</label>
        <div class="input-row">
          <input type="text" id="framePath" placeholder="Path to wallpaper PNG/JPG">
          <button class="btn-browse" onclick="document.getElementById('nativeFramePicker').click()">Select Image...</button>
        </div>
      </div>

      <div class="grid-2">
        <div class="field">
          <label>Screen Scale Ratio: <span id="scaleVal">0.78</span></label>
          <input type="range" id="screenFrac" min="0.5" max="1.0" step="0.02" value="0.78" oninput="document.getElementById('scaleVal').innerText=this.value">
        </div>

        <div class="field">
          <label>Audio Cleanup Mode:</label>
          <select id="audioCleanup">
            <option value="loudnorm" selected>loudnorm (Volume Normalized)</option>
            <option value="none">none (Faithful Original)</option>
            <option value="voice">voice (EQ + Denoise + Loudnorm)</option>
          </select>
        </div>
      </div>
    </div>

    <button id="exportBtn" class="btn-primary" onclick="startExport()">🚀 Export to MP4</button>

    <div class="progress-bar-bg">
      <div id="progressFill" class="progress-bar-fill"></div>
    </div>
    <div id="statusMsg" class="status-msg">Ready for export.</div>
  </div>

  <script>
    async function initApp() {
      // Scan for recordings on page load
      const res = await fetch('/api/scan-bundles');
      const data = await res.json();
      if (data.bundles && data.bundles.length > 0) {
        const select = document.getElementById('scannedSelect');
        data.bundles.forEach(b => {
          const opt = document.createElement('option');
          opt.value = b;
          opt.innerText = b.split('/').pop() + ` (${b})`;
          select.appendChild(opt);
        });
        document.getElementById('scanSection').style.display = 'block';
      }
    }

    function onScannedSelected(path) {
      if (path) setBundlePath(path);
    }

    function handleDrop(e) {
      e.preventDefault();
      document.getElementById('dropZone').classList.remove('dragover');
      if (e.dataTransfer.files.length > 0) {
        const file = e.dataTransfer.files[0];
        const raw = file.path || file.name;
        resolvePath(raw);
      }
    }

    function onNativeFileSelected(e) {
      if (e.target.files.length > 0) {
        const file = e.target.files[0];
        const raw = file.path || file.name;
        resolvePath(raw);
      }
    }

    function onNativeFrameSelected(e) {
      if (e.target.files.length > 0) {
        const file = e.target.files[0];
        document.getElementById('framePath').value = file.path || file.name;
      }
    }

    async function resolvePath(raw) {
      if (!raw) return;
      const res = await fetch('/api/resolve-path', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: raw })
      });
      const data = await res.json();
      if (data.resolved) {
        setBundlePath(data.resolved);
      } else {
        setBundlePath(raw);
      }
    }

    function setBundlePath(path) {
      document.getElementById('bundlePath').value = path;
      if (!document.getElementById('outputPath').value) {
        const base = path.split('/').pop().replace('.screenstudio', '');
        document.getElementById('outputPath').value = `/Users/solahudeen/Downloads/${base}.mp4`;
      }
      document.getElementById('statusMsg').innerText = "Selected: " + path;
    }

    async function startExport() {
      const bundle = document.getElementById('bundlePath').value.trim();
      const output = document.getElementById('outputPath').value.trim();

      if (!bundle || !output) {
        alert('Please specify both the .screenstudio bundle path and output MP4 destination.');
        return;
      }

      document.getElementById('exportBtn').disabled = true;
      
      await fetch('/api/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          bundle: bundle,
          output: output,
          frame: document.getElementById('framePath').value.trim(),
          screen_frac: parseFloat(document.getElementById('screenFrac').value),
          audio_cleanup: document.getElementById('audioCleanup').value
        })
      });

      pollProgress();
    }

    function pollProgress() {
      const interval = setInterval(async () => {
        const res = await fetch('/api/status');
        const data = await res.json();
        
        document.getElementById('statusMsg').innerText = data.message;
        if (data.percentage >= 0) {
          document.getElementById('progressFill').style.width = `${data.percentage}%`;
        }

        if (data.status === 'success') {
          clearInterval(interval);
          document.getElementById('exportBtn').disabled = false;
          alert('🎉 Export Completed Successfully!\nSaved to: ' + document.getElementById('outputPath').value);
        } else if (data.status === 'error') {
          clearInterval(interval);
          document.getElementById('exportBtn').disabled = false;
          alert('❌ Export Error: ' + data.message);
        }
      }, 500);
    }
  </script>
</body>
</html>
"""


class WebGUIRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress HTTP logging in stdout

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_CONTENT.encode("utf-8"))
        elif self.path == "/api/scan-bundles":
            bundles = scan_system_bundles()
            self._send_json({"bundles": bundles})
        elif self.path == "/api/status":
            self._send_json(global_tracker.get_state())
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body_bytes = self.rfile.read(length) if length > 0 else b""

        if self.path == "/api/resolve-path":
            data = json.loads(body_bytes.decode("utf-8"))
            raw = data.get("path", "")
            resolved = resolve_bundle_path(raw)
            self._send_json({"resolved": resolved})

        elif self.path == "/api/export":
            data = json.loads(body_bytes.decode("utf-8"))
            bundle = resolve_bundle_path(data.get("bundle"))
            output = data.get("output")
            options = {
                "frame": data.get("frame") or None,
                "screen_frac": data.get("screen_frac", 0.78),
                "audio_cleanup": data.get("audio_cleanup", "loudnorm")
            }

            global_tracker.update("Starting exporter engine...", 5.0)
            global_tracker.status = "rendering"

            def run_thread():
                try:
                    exp = RenderExporter(bundle, output, options=options)
                    exp.run_pipeline(progress_callback=lambda msg, pct: global_tracker.update(msg, pct))
                except Exception as e:
                    global_tracker.update(str(e), -1.0)

            threading.Thread(target=run_thread, daemon=True).start()
            self._send_json({"status": "started"})

    def _send_json(self, data: dict):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))


def launch_web_gui(port=8600):
    server = HTTPServer(("127.0.0.1", port), WebGUIRequestHandler)
    url = f"http://127.0.0.1:{port}"
    print(f"🌐 Launching screenstudio-to-mp4 Web Desktop Interface at: {url}")

    # Auto-open browser
    webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping GUI server...")


if __name__ == "__main__":
    launch_web_gui()
