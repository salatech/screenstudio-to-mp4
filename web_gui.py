#!/usr/bin/env python3
"""Local web GUI for screenstudio-to-mp4 — simple export for non-technical users.

  python3 web_gui.py

Opens http://127.0.0.1:8600 in your browser. Everything runs offline on your Mac.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from exporter import RenderExporter, check_dependencies, app_root

HOME = os.path.expanduser("~")
DEFAULT_PORT = 8600


def default_work_dir() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.join(HOME, "Library", "Application Support", "screenstudio-to-mp4")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "work")
    return os.path.join(app_root(), "work")


WORK_DIR = default_work_dir()


class ProgressTracker:
    def __init__(self):
        self.message = "Ready when you are."
        self.percentage = 0.0
        self.status = "idle"  # idle | rendering | success | error
        self.output = ""
        self.lock = threading.Lock()

    def reset(self):
        with self.lock:
            self.message = "Starting…"
            self.percentage = 0.0
            self.status = "rendering"
            self.output = ""

    def update(self, msg: str, pct: float):
        with self.lock:
            self.message = msg
            if pct >= 0:
                self.percentage = pct
            if pct >= 100.0:
                self.status = "success"
            elif pct < 0:
                self.status = "error"

    def set_output(self, path: str):
        with self.lock:
            self.output = path

    def get_state(self):
        with self.lock:
            return {
                "message": self.message,
                "percentage": self.percentage,
                "status": self.status,
                "output": self.output,
            }


tracker = ProgressTracker()


def scan_system_bundles() -> list[str]:
    """Find .screenstudio packages in common macOS folders."""
    search_dirs = [
        os.path.join(HOME, "Desktop"),
        os.path.join(HOME, "Downloads"),
        os.path.join(HOME, "Movies"),
        os.path.join(HOME, "Documents"),
        os.path.join(HOME, "Movies", "Screen Studio"),
        HOME,
    ]
    found: list[str] = []
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        try:
            for name in os.listdir(d):
                if not name.endswith(".screenstudio"):
                    continue
                full = os.path.join(d, name)
                if os.path.isdir(full) and os.path.isfile(os.path.join(full, "project.json")):
                    found.append(full)
        except OSError:
            continue
    return sorted(set(found), key=lambda p: os.path.getmtime(p), reverse=True)


def resolve_bundle_path(input_path: str) -> str:
    """Map a filename or partial path to an absolute .screenstudio bundle."""
    if not input_path:
        return ""
    raw = input_path.strip().strip('"').strip("'")
    expanded = os.path.abspath(os.path.expanduser(raw))
    if os.path.isdir(expanded) and expanded.endswith(".screenstudio"):
        return expanded
    # If user picked a file inside the package, walk up
    cur = expanded
    for _ in range(4):
        if cur.endswith(".screenstudio") and os.path.isdir(cur):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    filename = os.path.basename(raw.rstrip("/"))
    if not filename.endswith(".screenstudio"):
        # Maybe they dropped an inner file — try parent names from scan
        filename = filename  # keep for basename match below

    for bundle in scan_system_bundles():
        if os.path.basename(bundle) == filename or bundle.endswith(raw):
            return bundle
        if filename and filename in bundle:
            return bundle

    for parent in (
        HOME,
        os.path.join(HOME, "Desktop"),
        os.path.join(HOME, "Downloads"),
        os.path.join(HOME, "Movies"),
        os.path.join(HOME, "Documents"),
    ):
        cand = os.path.join(parent, filename)
        if os.path.isdir(cand):
            return cand
    return expanded


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>screenstudio-to-mp4</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #eef2f6;
    --panel: rgba(255, 255, 255, 0.86);
    --ink: #1a2332;
    --muted: #5b677a;
    --soft: #8490a1;
    --line: rgba(26, 35, 50, 0.1);
    --line-2: rgba(26, 35, 50, 0.16);
    --fill: rgba(26, 35, 50, 0.035);
    --accent: #0f7a6c;
    --accent-fg: #ffffff;
    --accent-soft: #d8f0eb;
    --focus: #0f7a6c;
    --ok: #0f7a6c;
    --bad: #c23b3b;
    --warn-bg: #fff6e8;
    --warn-line: #efc98a;
    --warn: #8a5a12;
    --star: #3d4a5c;
  }
  * { box-sizing: border-box; }
  html, body {
    height: 100%;
    margin: 0;
    overflow: hidden;
  }
  body {
    position: relative;
    color: var(--ink);
    background: var(--bg);
    font-family: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 15px;
    line-height: 1.45;
    -webkit-font-smoothing: antialiased;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .lens {
    position: fixed;
    inset: 0;
    pointer-events: none;
    z-index: 0;
    overflow: hidden;
  }
  .lens .stars {
    position: absolute;
    inset: 0;
  }
  .lens .dot {
    position: absolute;
    left: 0;
    top: 0;
    border-radius: 50%;
    background: var(--star);
    will-change: transform, opacity;
    mix-blend-mode: multiply;
    box-shadow: 0 0 7px 1px rgba(61, 74, 92, 0.35);
  }
  .app {
    position: relative;
    z-index: 1;
    width: min(1100px, calc(100vw - 32px));
    max-height: calc(100vh - 32px);
    overflow: auto;
    padding: 18px;
    display: grid;
    grid-template-rows: auto auto auto auto;
    gap: 12px;
    align-content: start;
    background: var(--panel);
    border: 1px solid var(--line);
    backdrop-filter: blur(18px);
    -webkit-backdrop-filter: blur(18px);
    box-shadow: 0 18px 50px rgba(26, 35, 50, 0.08);
  }
  .top {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 16px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--line);
  }
  .brand-name {
    font-weight: 700;
    font-size: 16px;
    letter-spacing: -0.02em;
  }
  .brand-sub {
    color: var(--muted);
    margin-top: 2px;
    font-size: 13px;
  }
  .top-note {
    color: var(--soft);
    font-size: 13px;
    white-space: nowrap;
  }
  .deps {
    display: none;
    background: var(--warn-bg);
    border: 1px solid var(--warn-line);
    color: var(--warn);
    padding: 8px 10px;
    font-size: 13px;
  }
  .deps.show { display: block; }
  .main {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    align-items: start;
  }
  .panel {
    background: var(--panel);
    border: 1px solid var(--line);
    padding: 14px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .panel-head {
    display: flex;
    align-items: center;
    gap: 10px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--line);
    flex-shrink: 0;
  }
  .step {
    color: var(--accent);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.04em;
    background: var(--accent-soft);
    padding: 3px 7px;
  }
  .panel-head h2 {
    margin: 0;
    font-size: 15px;
    font-weight: 700;
    letter-spacing: -0.01em;
  }
  label.lbl {
    display: block;
    font-size: 12px;
    font-weight: 600;
    color: var(--muted);
    margin-bottom: 4px;
    letter-spacing: 0.02em;
    text-transform: uppercase;
  }
  .field { margin: 0; }
  input[type="text"] {
    width: 100%;
    border: 1px solid var(--line-2);
    background: var(--fill);
    color: var(--ink);
    border-radius: 0;
    padding: 9px 11px;
    font: inherit;
    font-size: 14px;
  }
  input[type="text"]:focus {
    outline: none;
    background: #fff;
    border-color: var(--focus);
    box-shadow: 0 0 0 1px var(--focus);
  }
  .cselect {
    position: relative;
    width: 100%;
  }
  .cselect-btn {
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    text-align: left;
    border: 1px solid var(--line-2);
    background: var(--fill);
    color: var(--ink);
    border-radius: 0;
    padding: 9px 11px;
    font: inherit;
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
  }
  .cselect-btn:hover { background: rgba(0,0,0,0.04); }
  .cselect.open .cselect-btn {
    border-color: var(--focus);
    box-shadow: 0 0 0 1px var(--focus);
    background: #fff;
  }
  .cselect-btn .val {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .cselect-btn .chev {
    color: var(--soft);
    font-size: 11px;
    flex-shrink: 0;
    transition: transform 0.15s ease;
  }
  .cselect.open .cselect-btn .chev { transform: rotate(180deg); }
  .cselect-menu {
    display: none;
    position: absolute;
    left: 0; right: 0;
    top: calc(100% + 4px);
    z-index: 20;
    max-height: 200px;
    overflow: auto;
    border: 1px solid var(--line-2);
    background: #fff;
    box-shadow: 0 12px 28px rgba(0,0,0,0.12);
  }
  .cselect.open .cselect-menu { display: block; }
  .cselect-option {
    display: block;
    width: 100%;
    text-align: left;
    border: 0;
    border-radius: 0;
    background: transparent;
    color: var(--ink);
    padding: 9px 11px;
    font: inherit;
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
  }
  .cselect-option:hover { background: rgba(0,0,0,0.04); }
  .cselect-option[aria-selected="true"] {
    background: rgba(0,0,0,0.08);
    color: #000;
  }
  .row { display: flex; gap: 6px; }
  .row input { flex: 1; min-width: 0; }
  button, .btn {
    font: inherit;
    font-size: 13px;
    font-weight: 600;
    border: 1px solid var(--line-2);
    background: #fff;
    color: var(--ink);
    border-radius: 0;
    padding: 9px 11px;
    cursor: pointer;
    white-space: nowrap;
  }
  button:hover { background: rgba(0,0,0,0.04); }
  button:disabled { opacity: 0.4; cursor: not-allowed; }
  .drop {
    border: 1px dashed var(--line-2);
    padding: 18px 12px;
    text-align: center;
    color: var(--muted);
    font-size: 14px;
    line-height: 1.4;
    cursor: pointer;
    background: var(--fill);
    flex-shrink: 0;
  }
  .drop strong { color: var(--ink); font-weight: 600; }
  .drop small { display: block; margin-top: 4px; font-size: 12px; color: var(--soft); }
  .drop:hover, .drop.over {
    border-color: var(--ink);
    border-style: solid;
    color: var(--ink);
  }
  .hint { font-size: 12px; color: var(--soft); line-height: 1.4; margin: 0; }
  .effects {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
  }
  .effect {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 9px 10px;
    border: 1px solid var(--line);
    background: var(--fill);
    cursor: pointer;
    user-select: none;
  }
  .effect:has(input:checked) {
    background: #fff;
    border-color: rgba(15, 122, 108, 0.45);
    box-shadow: 0 0 0 1px rgba(15, 122, 108, 0.18);
  }
  .effect input {
    appearance: none;
    width: 14px; height: 14px;
    margin-top: 1px;
    border: 1px solid var(--line-2);
    border-radius: 0;
    background: #fff;
    flex-shrink: 0;
    display: grid; place-items: center;
  }
  .effect input:checked {
    background: var(--accent);
    border-color: var(--accent);
  }
  .effect input:checked::after {
    content: "";
    width: 3px; height: 6px;
    border: solid #fff;
    border-width: 0 1.5px 1.5px 0;
    transform: rotate(45deg) translateY(-1px);
  }
  .effect-title {
    font-size: 14px;
    font-weight: 600;
    display: block;
  }
  .effect-desc {
    font-size: 12px;
    color: var(--muted);
    line-height: 1.3;
    margin-top: 2px;
    display: block;
  }
  #bgOptions {
    border: 1px solid var(--line);
    background: var(--fill);
    padding: 10px;
    display: grid;
    gap: 8px;
  }
  .bg-row {
    display: grid;
    grid-template-columns: 1.4fr 0.9fr;
    gap: 8px;
    align-items: end;
  }
  details.advanced {
    border-top: 1px solid var(--line);
    padding-top: 8px;
  }
  details.advanced summary {
    cursor: pointer;
    font-size: 13px;
    font-weight: 600;
    color: var(--muted);
    list-style: none;
  }
  details.advanced summary::-webkit-details-marker { display: none; }
  details.advanced summary::before { content: "+ "; color: var(--soft); }
  details.advanced[open] summary { margin-bottom: 8px; color: var(--ink); }
  details.advanced[open] summary::before { content: "- "; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  input[type="range"] { width: 100%; accent-color: var(--accent); }
  .bottom {
    display: grid;
    grid-template-columns: 180px 1fr;
    gap: 12px;
    align-items: center;
    padding-top: 12px;
    border-top: 1px solid var(--line);
    min-height: 0;
  }
  .btn-primary {
    border: 1px solid var(--accent);
    background: var(--accent);
    color: var(--accent-fg);
    padding: 10px 14px;
    font-size: 14px;
    font-weight: 600;
    height: 44px;
    max-height: 44px;
    width: 100%;
  }
  .btn-primary:hover {
    background: #0c655a;
    border-color: #0c655a;
  }
  .progress-wrap {
    padding: 8px 12px;
    border: 1px solid var(--line);
    background: var(--panel);
    min-width: 0;
  }
  .bar-bg {
    height: 4px;
    background: rgba(26, 35, 50, 0.1);
    overflow: hidden;
  }
  .bar-fill {
    height: 100%;
    width: 0%;
    background: var(--accent);
    transition: width 0.3s ease;
  }
  .status-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 6px;
    min-height: 18px;
  }
  .status {
    font-size: 13px;
    color: var(--muted);
    flex: 1;
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .status.ok { color: var(--ok); font-weight: 600; }
  .status.bad { color: var(--bad); font-weight: 600; white-space: normal; }
  .success-actions { display: none; gap: 6px; flex-shrink: 0; }
  .success-actions.show { display: flex; }
  @media (max-width: 820px) {
    html, body { overflow: auto; height: auto; }
    body { display: block; padding: 16px; }
    .app { width: 100%; max-height: none; }
    .main { grid-template-columns: 1fr; }
    .bottom { grid-template-columns: 1fr; }
    .top-note { display: none; }
    .bg-row { grid-template-columns: 1fr; }
  }
  @media (max-height: 700px) {
    .drop { padding: 12px; }
    .effect-desc { display: none; }
    .brand-sub { display: none; }
  }
  @media (prefers-reduced-motion: reduce) {
    .lens .dot { transition: none !important; }
  }
</style>
</head>
<body>
  <div class="lens" aria-hidden="true">
    <div class="stars" id="stars"></div>
  </div>
  <input type="file" id="bundlePicker" hidden>
  <input type="file" id="framePicker" hidden accept="image/*">

  <div class="app">
    <div class="top">
      <div>
        <div class="brand-name">screenstudio-to-mp4</div>
        <div class="brand-sub">export · local · offline</div>
      </div>
      <div class="top-note">127.0.0.1 · no upload</div>
    </div>

    <div id="deps" class="deps"></div>

    <div class="main">
      <section class="panel">
        <div class="panel-head">
          <span class="step">01</span>
          <h2>Recording</h2>
        </div>

        <div class="field" id="scanSection" hidden>
          <label class="lbl" for="scannedBtn">Found on this Mac</label>
          <div class="cselect" id="scanned" data-value="">
            <button type="button" class="cselect-btn" id="scannedBtn" aria-haspopup="listbox" aria-expanded="false">
              <span class="val">Select a .screenstudio recording…</span>
              <span class="chev">▾</span>
            </button>
            <div class="cselect-menu" role="listbox" id="scannedMenu"></div>
          </div>
        </div>

        <div class="drop" id="drop" tabindex="0" role="button" aria-label="Choose Screen Studio recording">
          Drop a <strong>.screenstudio</strong> file here
          <small>or click to browse</small>
        </div>

        <div class="field">
          <label class="lbl" for="bundlePath">Recording path</label>
          <div class="row">
            <input id="bundlePath" type="text" placeholder="/Users/you/Desktop/MyRecording.screenstudio" autocomplete="off">
            <button type="button" id="browseBundle">Browse…</button>
          </div>
        </div>

        <div class="field">
          <label class="lbl" for="outputPath">Save MP4 as</label>
          <input id="outputPath" type="text" placeholder="__DOWNLOADS__/MyRecording.mp4" autocomplete="off">
        </div>
        <p class="hint">Tip: if Browse only fills the name, paste the full path from Finder (Option → Copy path).</p>
      </section>

      <section class="panel">
        <div class="panel-head">
          <span class="step">02</span>
          <h2>Effects</h2>
        </div>

        <div class="effects">
          <label class="effect">
            <input type="checkbox" id="fxBackground" checked>
            <span>
              <span class="effect-title">Background frame</span>
              <span class="effect-desc">Canvas around the screen</span>
            </span>
          </label>
          <label class="effect">
            <input type="checkbox" id="fxZooms" checked>
            <span>
              <span class="effect-title">Click zooms</span>
              <span class="effect-desc">Zoom on clicks</span>
            </span>
          </label>
          <label class="effect">
            <input type="checkbox" id="fxCursor" checked>
            <span>
              <span class="effect-title">Animated cursor</span>
              <span class="effect-desc">Pointer + ripples</span>
            </span>
          </label>
          <label class="effect">
            <input type="checkbox" id="fxWebcam" checked>
            <span>
              <span class="effect-title">Webcam bubble</span>
              <span class="effect-desc">If project has one</span>
            </span>
          </label>
        </div>

        <div id="bgOptions">
          <div class="bg-row">
            <div class="field">
              <label class="lbl" for="framePath">Custom background</label>
              <div class="row">
                <input id="framePath" type="text" placeholder="Optional image path">
                <button type="button" id="browseFrame">Image…</button>
              </div>
            </div>
            <div class="field">
              <label class="lbl">Screen size: <span id="scaleVal">78%</span></label>
              <input type="range" id="screenFrac" min="50" max="98" step="2" value="78">
            </div>
          </div>
        </div>

        <details class="advanced">
          <summary>More options</summary>
          <div class="grid2">
            <div class="field">
              <label class="lbl" for="audioCleanupBtn">Audio cleanup</label>
              <div class="cselect" id="audioCleanup" data-value="loudnorm">
                <button type="button" class="cselect-btn" id="audioCleanupBtn" aria-haspopup="listbox" aria-expanded="false">
                  <span class="val">Normalize volume</span>
                  <span class="chev">▾</span>
                </button>
                <div class="cselect-menu" role="listbox">
                  <button type="button" class="cselect-option" role="option" data-value="loudnorm" aria-selected="true">Normalize volume</button>
                  <button type="button" class="cselect-option" role="option" data-value="none" aria-selected="false">Keep original</button>
                  <button type="button" class="cselect-option" role="option" data-value="voice" aria-selected="false">Voice EQ + denoise</button>
                </div>
              </div>
            </div>
            <div class="field">
              <label class="lbl" for="presetBtn">Encode speed</label>
              <div class="cselect" id="preset" data-value="slow">
                <button type="button" class="cselect-btn" id="presetBtn" aria-haspopup="listbox" aria-expanded="false">
                  <span class="val">Higher quality</span>
                  <span class="chev">▾</span>
                </button>
                <div class="cselect-menu" role="listbox">
                  <button type="button" class="cselect-option" role="option" data-value="fast" aria-selected="false">Faster</button>
                  <button type="button" class="cselect-option" role="option" data-value="medium" aria-selected="false">Balanced</button>
                  <button type="button" class="cselect-option" role="option" data-value="slow" aria-selected="true">Higher quality</button>
                </div>
              </div>
            </div>
          </div>
        </details>
      </section>
    </div>

    <div class="bottom">
      <button class="btn-primary" id="exportBtn" type="button">Export to MP4</button>
      <div class="progress-wrap">
        <div class="bar-bg"><div class="bar-fill" id="progressFill"></div></div>
        <div class="status-row">
          <div class="status" id="statusMsg">Ready when you are.</div>
          <div class="success-actions" id="successActions">
            <button type="button" id="openFile">Open</button>
            <button type="button" id="openFolder">Finder</button>
          </div>
        </div>
      </div>
    </div>
  </div>

<script>
  const HOME = "__HOME__";
  const DOWNLOADS = "__DOWNLOADS__";

  function getCSelectValue(id) {
    const el = document.getElementById(id);
    return el ? (el.dataset.value || "") : "";
  }

  function setCSelectValue(root, value, label) {
    root.dataset.value = value;
    const valEl = root.querySelector(".cselect-btn .val");
    if (valEl) valEl.textContent = label;
    root.querySelectorAll(".cselect-option").forEach(opt => {
      opt.setAttribute("aria-selected", opt.dataset.value === value ? "true" : "false");
    });
    root.dispatchEvent(new CustomEvent("cchange", { detail: { value, label } }));
  }

  function closeAllCSelects(except) {
    document.querySelectorAll(".cselect.open").forEach(el => {
      if (el === except) return;
      el.classList.remove("open");
      const btn = el.querySelector(".cselect-btn");
      if (btn) btn.setAttribute("aria-expanded", "false");
    });
  }

  function wireCSelect(root) {
    const btn = root.querySelector(".cselect-btn");
    const menu = root.querySelector(".cselect-menu");
    if (!btn || !menu) return;

    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const willOpen = !root.classList.contains("open");
      closeAllCSelects(root);
      root.classList.toggle("open", willOpen);
      btn.setAttribute("aria-expanded", willOpen ? "true" : "false");
    });

    menu.addEventListener("click", (e) => {
      const opt = e.target.closest(".cselect-option");
      if (!opt) return;
      e.stopPropagation();
      setCSelectValue(root, opt.dataset.value, opt.textContent.trim());
      root.classList.remove("open");
      btn.setAttribute("aria-expanded", "false");
    });
  }

  document.querySelectorAll(".cselect").forEach(wireCSelect);
  document.addEventListener("click", () => closeAllCSelects());

  async function init() {
    const meta = await (await fetch("/api/meta")).json();
    if (meta.missing && meta.missing.length) {
      const el = document.getElementById("deps");
      el.classList.add("show");
      el.innerHTML = "<strong>Almost ready</strong> — install: " +
        meta.missing.map(m => m.split(" (")[0]).join(", ");
    }

    const scan = await (await fetch("/api/scan-bundles")).json();
    if (scan.bundles && scan.bundles.length) {
      const root = document.getElementById("scanned");
      const menu = document.getElementById("scannedMenu");
      menu.innerHTML = "";
      const placeholder = document.createElement("button");
      placeholder.type = "button";
      placeholder.className = "cselect-option";
      placeholder.setAttribute("role", "option");
      placeholder.dataset.value = "";
      placeholder.setAttribute("aria-selected", "true");
      placeholder.textContent = "Select a .screenstudio recording…";
      menu.appendChild(placeholder);
      scan.bundles.forEach(b => {
        const opt = document.createElement("button");
        opt.type = "button";
        opt.className = "cselect-option";
        opt.setAttribute("role", "option");
        opt.dataset.value = b;
        opt.setAttribute("aria-selected", "false");
        opt.textContent = b.split("/").pop();
        menu.appendChild(opt);
      });
      document.getElementById("scanSection").hidden = false;
    }
  }

  (function setupStars() {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const layer = document.getElementById("stars");
    if (!layer) return;

    const COUNT = 90;
    const stars = [];
    const w = () => window.innerWidth;
    const h = () => window.innerHeight;

    for (let i = 0; i < COUNT; i++) {
      const el = document.createElement("span");
      el.className = "dot";
      const size = 1 + Math.random() * 2.4;
      el.style.width = size + "px";
      el.style.height = size + "px";
      layer.appendChild(el);
      stars.push({
        el,
        x: Math.random() * w(),
        y: Math.random() * h(),
        vx: reduce ? 0 : (Math.random() - 0.5) * 0.35,
        vy: reduce ? 0 : (Math.random() - 0.5) * 0.35,
        twinkle: Math.random() * Math.PI * 2,
        speed: 0.8 + Math.random() * 1.6,
        depth: 0.35 + Math.random() * 0.65
      });
    }

    let t0 = performance.now();
    function frame(now) {
      const elapsed = (now - t0) / 1000;
      const ww = w(), hh = h();
      for (const s of stars) {
        s.x += s.vx * s.depth;
        s.y += s.vy * s.depth;
        if (s.x < -20) s.x = ww + 20;
        if (s.x > ww + 20) s.x = -20;
        if (s.y < -20) s.y = hh + 20;
        if (s.y > hh + 20) s.y = -20;
        const tw = 0.35 + 0.65 * (0.5 + 0.5 * Math.sin(elapsed * s.speed + s.twinkle));
        s.el.style.opacity = String(tw);
        s.el.style.transform = `translate(${s.x}px, ${s.y}px) translate(-50%, -50%)`;
        s.el.style.boxShadow = "0 0 7px 1px rgba(61, 74, 92, 0.3)";
      }
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  })();

  document.getElementById("scanned").addEventListener("cchange", e => {
    if (e.detail.value) setBundle(e.detail.value);
  });

  document.getElementById("screenFrac").addEventListener("input", e => {
    document.getElementById("scaleVal").textContent = e.target.value + "%";
  });

  function syncBgOptions() {
    const on = document.getElementById("fxBackground").checked;
    document.getElementById("bgOptions").style.display = on ? "" : "none";
  }
  document.getElementById("fxBackground").addEventListener("change", syncBgOptions);
  syncBgOptions();

  const drop = document.getElementById("drop");
  drop.addEventListener("click", () => document.getElementById("bundlePicker").click());
  drop.addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") document.getElementById("bundlePicker").click();
  });
  drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", e => {
    e.preventDefault();
    drop.classList.remove("over");
    const f = e.dataTransfer.files[0];
    if (f) resolvePath(f.path || f.name);
  });

  document.getElementById("browseBundle").addEventListener("click", () =>
    document.getElementById("bundlePicker").click());
  document.getElementById("browseFrame").addEventListener("click", () =>
    document.getElementById("framePicker").click());

  document.getElementById("bundlePicker").addEventListener("change", e => {
    const f = e.target.files[0];
    if (f) resolvePath(f.path || f.name);
  });
  document.getElementById("framePicker").addEventListener("change", e => {
    const f = e.target.files[0];
    if (f) document.getElementById("framePath").value = f.path || f.name;
  });

  document.getElementById("bundlePath").addEventListener("change", e => resolvePath(e.target.value));

  async function resolvePath(raw) {
    if (!raw) return;
    const res = await fetch("/api/resolve-path", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: raw })
    });
    const data = await res.json();
    setBundle(data.resolved || raw);
  }

  function setBundle(path) {
    document.getElementById("bundlePath").value = path;
    const base = path.split("/").pop().replace(/\.screenstudio$/i, "") || "export";
    const out = document.getElementById("outputPath");
    if (!out.value || out.dataset.auto !== "0") {
      out.value = DOWNLOADS + "/" + base + ".mp4";
      out.dataset.auto = "1";
    }
    document.getElementById("statusMsg").textContent = "Selected: " + path.split("/").pop();
    document.getElementById("statusMsg").className = "status";
    document.getElementById("successActions").classList.remove("show");
  }

  document.getElementById("outputPath").addEventListener("input", e => {
    e.target.dataset.auto = "0";
  });

  document.getElementById("exportBtn").addEventListener("click", startExport);
  document.getElementById("openFile").addEventListener("click", () =>
    fetch("/api/reveal", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: document.getElementById("outputPath").value, mode: "open" }) }));
  document.getElementById("openFolder").addEventListener("click", () =>
    fetch("/api/reveal", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: document.getElementById("outputPath").value, mode: "folder" }) }));

  async function startExport() {
    const bundle = document.getElementById("bundlePath").value.trim();
    const output = document.getElementById("outputPath").value.trim();
    if (!bundle || !output) {
      alert("Please choose a Screen Studio recording and an output file path.");
      return;
    }

    const btn = document.getElementById("exportBtn");
    btn.disabled = true;
    document.getElementById("successActions").classList.remove("show");
    document.getElementById("statusMsg").className = "status";
    document.getElementById("progressFill").style.width = "2%";

    const useBg = document.getElementById("fxBackground").checked;
    await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bundle,
        output,
        frame: useBg ? document.getElementById("framePath").value.trim() : "",
        screen_frac: useBg
          ? parseInt(document.getElementById("screenFrac").value, 10) / 100
          : 1.0,
        audio_cleanup: getCSelectValue("audioCleanup"),
        zooms: document.getElementById("fxZooms").checked ? "on" : "off",
        cursor: document.getElementById("fxCursor").checked ? "on" : "off",
        webcam: document.getElementById("fxWebcam").checked ? "on" : "off",
        preset: getCSelectValue("preset")
      })
    });

    pollProgress();
  }

  function pollProgress() {
    const timer = setInterval(async () => {
      const data = await (await fetch("/api/status")).json();
      const msg = document.getElementById("statusMsg");
      msg.textContent = data.message;
      if (data.percentage >= 0) {
        document.getElementById("progressFill").style.width = data.percentage + "%";
      }
      if (data.status === "success") {
        clearInterval(timer);
        document.getElementById("exportBtn").disabled = false;
        msg.className = "status ok";
        document.getElementById("successActions").classList.add("show");
      } else if (data.status === "error") {
        clearInterval(timer);
        document.getElementById("exportBtn").disabled = false;
        msg.className = "status bad";
      }
    }, 600);
  }

  init();
</script>
</body>
</html>
"""

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            html = (
                HTML.replace("__HOME__", HOME)
                .replace("__DOWNLOADS__", os.path.join(HOME, "Downloads"))
            )
            self._bytes(200, "text/html; charset=utf-8", html.encode("utf-8"))
        elif path == "/api/meta":
            self._json({"home": HOME, "downloads": os.path.join(HOME, "Downloads"),
                        "missing": check_dependencies()})
        elif path == "/api/scan-bundles":
            self._json({"bundles": scan_system_bundles()})
        elif path == "/api/status":
            self._json(tracker.get_state())
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._json({"error": "invalid json"}, 400)
            return

        path = urlparse(self.path).path

        if path == "/api/resolve-path":
            self._json({"resolved": resolve_bundle_path(data.get("path", ""))})
            return

        if path == "/api/reveal":
            target = os.path.abspath(os.path.expanduser(data.get("path", "")))
            mode = data.get("mode", "folder")
            if mode == "open" and os.path.isfile(target):
                os.system(f'open "{target}"')
            elif os.path.exists(target):
                os.system(f'open -R "{target}"')
            self._json({"ok": True})
            return

        if path == "/api/export":
            if tracker.get_state()["status"] == "rendering":
                self._json({"status": "busy", "error": "An export is already running."}, 409)
                return

            bundle = resolve_bundle_path(data.get("bundle", ""))
            output = os.path.abspath(os.path.expanduser(data.get("output", "")))
            options = {
                "frame": data.get("frame") or None,
                "screen_frac": float(data.get("screen_frac", 0.78)),
                "audio_cleanup": data.get("audio_cleanup", "loudnorm"),
                "zooms": data.get("zooms", "on"),
                "cursor": data.get("cursor", "auto"),
                "webcam": data.get("webcam", "auto"),
                "preset": data.get("preset", "slow"),
            }

            tracker.reset()
            tracker.set_output(output)

            def run():
                try:
                    exp = RenderExporter(
                        bundle,
                        output,
                        work_dir=WORK_DIR,
                        options=options,
                    )
                    exp.run_pipeline(progress_callback=tracker.update)
                except Exception as exc:
                    tracker.update(str(exc), -1.0)

            threading.Thread(target=run, daemon=True).start()
            self._json({"status": "started"})
            return

        self.send_error(404)

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self._bytes(code, "application/json", body)

    def _bytes(self, code, ctype, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main(port: int = DEFAULT_PORT):
    missing = check_dependencies()
    server = HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    def say(msg: str) -> None:
        print(msg, flush=True)

    say("Screen Studio → MP4 GUI")
    say(f"Open: {url}")
    if missing:
        say("Warning — missing dependencies:")
        for m in missing:
            say(f"  - {m}")
    else:
        say("Dependencies OK (ffmpeg + Pillow).")
    say("Press Ctrl+C to stop.\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    import sys

    port = DEFAULT_PORT
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    main(port)
