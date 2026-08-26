#!/usr/bin/env python3
"""Shared helpers for the Screen Studio → MP4 pipeline."""

from __future__ import annotations

import json
import os
import re
import struct
from dataclasses import dataclass
from typing import Iterable, Optional


def clean_path(path: str) -> str:
    """Turn pasted/escaped paths into a real filesystem path.

    Browsers and shells sometimes send ``h30\\.screenstudio`` or
    ``/Users/me/h30\\.screenstudio``. Those backslashes are not part of the
    real macOS path.
    """
    if not path:
        return ""
    raw = path.strip().strip('"').strip("'")
    raw = raw.replace("\\", "")
    return os.path.abspath(os.path.expanduser(raw.rstrip("/")))


def shell_quote(path: str) -> str:
    """Quote a path for a zsh script. Never backslash-escape dots inside quotes."""
    return '"' + clean_path(path).replace('"', '\\"') + '"'


def first_existing(*paths: Optional[str]) -> Optional[str]:
    for p in paths:
        if p and os.path.isfile(p):
            return p
    return None


def find_channel_media(rec: str, channel_id: Optional[str],
                       exts: tuple[str, ...] = (".m4a", ".m3u8", ".mp4", ".wav")) -> Optional[str]:
    if not channel_id:
        return None
    return first_existing(*(os.path.join(rec, f"{channel_id}-0{ext}") for ext in exts))


def find_captions(bundle: str) -> Optional[str]:
    names = (
        "captions.srt", "captions.vtt", "transcript.srt", "transcript.vtt",
        "subtitles.srt", "subtitles.vtt",
    )
    roots = (bundle, os.path.join(bundle, "recording"))
    for root in roots:
        for name in names:
            cand = os.path.join(root, name)
            if os.path.isfile(cand):
                return cand
        if os.path.isdir(root):
            try:
                for fn in sorted(os.listdir(root)):
                    if fn.lower().endswith((".srt", ".vtt")):
                        return os.path.join(root, fn)
            except OSError:
                pass
    return None


def _wallpaper_cache_dir() -> str:
    cache = os.path.join(
        os.path.expanduser("~/Library/Caches/screenstudio-to-mp4/wallpapers")
    )
    os.makedirs(cache, exist_ok=True)
    return cache


def _screen_studio_asars() -> list[str]:
    homes = (
        "/Applications/Screen Studio.app/Contents/Resources/app.asar",
        os.path.expanduser("~/Applications/Screen Studio.app/Contents/Resources/app.asar"),
    )
    return [p for p in homes if os.path.isfile(p)]


def _asar_extract_file(asar_path: str, inner_path: str) -> Optional[bytes]:
    """Read one file from an Electron asar archive (no extra dependency)."""
    inner_path = inner_path.replace("\\", "/").lstrip("/")
    try:
        with open(asar_path, "rb") as f:
            f.read(4)  # pickle header size
            header_pickle_size = struct.unpack("<I", f.read(4))[0]
            f.read(4)  # json pickle size
            json_len = struct.unpack("<I", f.read(4))[0]
            header = json.loads(f.read(json_len))
            data_offset = 8 + header_pickle_size
            node = header
            for part in inner_path.split("/"):
                if not part:
                    continue
                node = (node.get("files") or {}).get(part)
                if node is None:
                    return None
            if "offset" not in node or "size" not in node:
                return None
            f.seek(data_offset + int(node["offset"]))
            data = f.read(int(node["size"]))
            return data if len(data) == int(node["size"]) else None
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def extract_screen_studio_wallpaper(name: str) -> Optional[str]:
    """Copy a wallpaper out of Screen Studio.app and cache it locally."""
    name = name.lstrip("/")
    base = os.path.basename(name)
    cache = os.path.join(_wallpaper_cache_dir(), name.replace("/", os.sep))
    if os.path.isfile(cache) and os.path.getsize(cache) > 1024:
        return cache
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    inner_candidates = (
        f"assets/backgrounds/{name}",
        f"assets/backgrounds/{base}",
        name,
        f"assets/{name}",
    )
    for asar in _screen_studio_asars():
        for inner in inner_candidates:
            data = _asar_extract_file(asar, inner)
            if not data or len(data) < 1024:
                continue
            with open(cache, "wb") as out:
                out.write(data)
            return cache
    return None


def find_system_wallpaper(name: Optional[str]) -> Optional[str]:
    """Locate a Screen Studio wallpaper: disk, then the app's asar archive."""
    if not name:
        return None
    name = name.lstrip("/")
    base = os.path.basename(name)
    unpacked = [
        os.path.join(os.path.dirname(asar), "app.asar.unpacked")
        for asar in _screen_studio_asars()
    ]
    roots = [
        "/Applications/Screen Studio.app/Contents/Resources",
        os.path.expanduser("~/Applications/Screen Studio.app/Contents/Resources"),
        os.path.expanduser("~/Library/Application Support/Screen Studio"),
        "/System/Library/Desktop Pictures",
        "/Library/Desktop Pictures",
        "/System/Library/Desktop Pictures/Solid Colors",
        *unpacked,
    ]
    rels = [
        name, base,
        os.path.join("assets", "backgrounds", name),
        os.path.join("assets", "backgrounds", base),
        os.path.join("wallpapers", name),
        os.path.join("wallpapers", base),
    ]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for rel in rels:
            cand = os.path.join(root, rel)
            if os.path.isfile(cand) and os.path.getsize(cand) > 4096:
                return cand
        # Skip Screen Studio thumbnail caches (128px previews).
        if "Thumbnails" in root or "Caches" in root:
            continue
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in ("Thumbnails", "Cache", "Code Cache")]
                if base in filenames:
                    cand = os.path.join(dirpath, base)
                    if os.path.getsize(cand) > 4096:
                        return cand
                if dirpath.count(os.sep) - root.count(os.sep) > 4:
                    dirnames[:] = []
        except OSError:
            continue
    return extract_screen_studio_wallpaper(name)


@dataclass
class Slice:
    start: float
    end: float
    time_scale: float
    volume: float = 1.0
    hide_cursor: bool = False

    @property
    def src_dur(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def playback_speed(self) -> float:
        """Screen Studio stores timeScale = 1/speed (0.125 = 8× faster)."""
        ts = self.time_scale if self.time_scale > 1e-6 else 1.0
        return 1.0 / ts

    @property
    def out_dur(self) -> float:
        return self.src_dur * (self.time_scale if self.time_scale > 1e-6 else 1.0)


def parse_slices(raw: Iterable[dict], fallback_dur: float) -> list[Slice]:
    out: list[Slice] = []
    for s in raw or []:
        ts = float(s.get("timeScale", 1) or 1)
        if ts <= 0:
            ts = 1.0
        out.append(Slice(
            start=float(s["sourceStartMs"]) / 1000.0,
            end=float(s["sourceEndMs"]) / 1000.0,
            time_scale=ts,
            volume=float(s.get("volume", 1) or 1),
            hide_cursor=bool(s.get("hideCursor")),
        ))
    if not out:
        out = [Slice(0.0, float(fallback_dur), 1.0)]
    return out


def output_duration(slices: list[Slice]) -> float:
    return sum(s.out_dur for s in slices)


def source_to_output_time(t: float, slices: list[Slice]) -> Optional[float]:
    acc = 0.0
    for s in slices:
        if s.start <= t < s.end or (t == s.end and s is slices[-1]):
            return acc + (t - s.start) * (s.time_scale if s.time_scale > 1e-6 else 1.0)
        if t >= s.end:
            acc += s.out_dur
            continue
        if t < s.start:
            return None
    return None


def slices_are_identity(slices: list[Slice], src_dur: float, tol: float = 1e-3) -> bool:
    """True when slices are a single 1.0x pass over the whole recording."""
    if not slices:
        return True
    if any(abs(s.time_scale - 1.0) > tol for s in slices):
        return False
    t = 0.0
    for s in slices:
        if abs(s.start - t) > tol:
            return False
        t = s.end
    return abs(t - src_dur) <= max(tol, 0.05) or t >= src_dur - 0.05


def slices_have_gaps(slices: list[Slice], tol: float = 1e-3) -> bool:
    t = slices[0].start if slices else 0.0
    for s in slices:
        if s.start - t > tol:
            return True
        t = s.end
    return False


def screen_frac_from_padding(padding_ratio) -> float:
    """Map Screen Studio ``backgroundPaddingRatio`` to screen/canvas width.

    The app stores a percent (10 = 10% inset on each side → screen is 80%).
    Values in ``(0, 1]`` are treated as already-normalized fractions.
    """
    if padding_ratio is None:
        return 0.80
    try:
        raw = float(padding_ratio)
    except (TypeError, ValueError):
        return 0.80
    pad = raw / 100.0 if raw > 1.0 else raw
    pad = min(0.25, max(0.0, pad))
    return max(0.50, min(0.98, 1.0 - 2.0 * pad))


def fit_zoom(project_zoom: float, bbox_w: float, bbox_h: float,
             frame_w: float, frame_h: float, snap: float = 0.25) -> float:
    """Never zoom tighter than the click group (plus edge snap) can fit."""
    z = max(1.0, float(project_zoom or 1.0))
    snap = min(0.45, max(0.0, float(snap or 0.0)))
    inner = max(0.15, 1.0 - 2.0 * snap)
    if bbox_w > 1 and frame_w > 0:
        z = min(z, frame_w / max(bbox_w / inner, 1.0))
    if bbox_h > 1 and frame_h > 0:
        z = min(z, frame_h / max(bbox_h / inner, 1.0))
    return max(1.0, z)


def atempo_chain(scale: float) -> str:
    """ffmpeg atempo filters that multiply to ``scale`` (each in [0.5, 100])."""
    if abs(scale - 1.0) < 1e-6:
        return ""
    if scale <= 0:
        return ""
    filters: list[str] = []
    remaining = float(scale)
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    while remaining > 100.0:
        filters.append("atempo=100")
        remaining /= 100.0
    if abs(remaining - 1.0) > 1e-6:
        filters.append(f"atempo={remaining:.8g}")
    return ",".join(filters)


def src_gate(start: float, end: float, is_last: bool, clock: str = "T") -> str:
    if is_last:
        return f"between({clock},{start:.6f},{end:.6f})"
    return f"gte({clock},{start:.6f})*lt({clock},{end:.6f})"


def video_timeline_filter(slices: list[Slice], fps: int, src_dur: float,
                          label_in: str, label_out: str) -> str:
    """Map a source-time video pad onto the edited output timeline.

    Avoids ``split=N`` (which copies the whole stream N times in memory).
    Speed ramps use ``setpts``; missing cut regions are dropped with ``select``.
    """
    if slices_are_identity(slices, src_dur):
        return f"[{label_in}]null[{label_out}]"

    parts: list[str] = [f"[{label_in}]"]
    filters: list[str] = []

    if slices_have_gaps(slices) or (slices and slices[0].start > 1e-3):
        sel = "+".join(
            src_gate(s.start, s.end, i == len(slices) - 1, clock="t")
            for i, s in enumerate(slices)
        )
        filters.append(f"select='{sel}'")

    if any(abs(s.time_scale - 1.0) > 1e-6 for s in slices):
        terms: list[str] = []
        acc = 0.0
        for i, s in enumerate(slices):
            gate = src_gate(s.start, s.end, i == len(slices) - 1, clock="T")
            terms.append(
                f"({gate}*((T-{s.start:.6f})*{s.time_scale:.8g}+{acc:.6f}))"
            )
            acc += s.out_dur
        filters.append(f"setpts='({'+'.join(terms)})/TB'")
        filters.append(f"fps={fps}")
    else:
        # cuts only — drop gaps and restamp
        filters.append("setpts=N/FRAME_RATE/TB")

    parts.append(",".join(filters))
    parts.append(f"[{label_out}]")
    return "".join(parts)


def audio_slice_filters(slices: list[Slice], cleanup: str,
                        mixed: bool = False) -> str:
    """Build the audio filter_complex for sliced + speed-ramped voice."""
    n = len(slices)
    src = "[mx]" if mixed else "[0:a]"
    head = f"[0:a][1:a]amix=inputs=2:duration=first[mx];" if mixed else ""
    chain = [f"{src}asplit={n}" + "".join(f"[x{i}]" for i in range(n))]
    for i, s in enumerate(slices):
        bits = [f"atrim={s.start:.6f}:{s.end:.6f}", "asetpts=PTS-STARTPTS"]
        tempo = atempo_chain(s.playback_speed)
        if tempo:
            bits.append(tempo)
        if abs(s.volume - 1.0) > 1e-3:
            bits.append(f"volume={s.volume:.4g}")
        chain.append(f"[x{i}]{','.join(bits)}[a{i}]")
    chain.append("".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[cut]")
    if cleanup:
        chain.append(f"[cut]{cleanup}[out]")
    else:
        chain.append("[cut]anull[out]")
    return head + ";".join(chain)


def hide_cursor_enable(slices: list[Slice]) -> Optional[str]:
    hidden = [s for s in slices if s.hide_cursor]
    if not hidden:
        return None
    expr = "+".join(f"between(t,{s.start:.6f},{s.end:.6f})" for s in hidden)
    return f"not({expr})"


def parse_ffmpeg_progress(line: str) -> Optional[float]:
    """Return output time in seconds from an ffmpeg -progress / stats line."""
    line = line.strip()
    m = re.search(r"out_time_ms=(\d+)", line)
    if m:
        return int(m.group(1)) / 1_000_000.0
    m = re.search(r"out_time=(\d+):(\d+):(\d+(?:\.\d+)?)", line)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", line)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return None


def resolve_audio_sources(
    rec: str,
    chans: dict,
    mode: str,
    *,
    mute_mic: bool = False,
    mute_system: bool = False,
    enhanced: Optional[str] = None,
    system_silent: Optional[bool] = None,
) -> tuple[str, Optional[str], Optional[str], list[str]]:
    """Return (mode, voice_src, system_src, warnings)."""
    warnings: list[str] = []
    mic_id = next((i for i, r in chans.items() if r.get("type") == "microphone"), None)
    sys_id = next((i for i, r in chans.items() if r.get("type") == "systemAudio"), None)
    mic = None if mute_mic else find_channel_media(rec, mic_id)
    sysa = None if mute_system else find_channel_media(rec, sys_id)

    if mode == "auto":
        if enhanced:
            mode = "enhanced"
        elif mic:
            mode = "mic"
        elif sysa and not system_silent:
            mode = "system"
        elif sysa:
            warnings.append("no microphone; system audio is silent — using a silent track")
            mode = "silence"
        else:
            warnings.append("no audio channels found — using a silent track")
            mode = "silence"
    elif mode == "enhanced" and not enhanced:
        warnings.append("no enhanced track; falling back to microphone")
        mode = "mic" if mic else ("system" if sysa else "silence")
    elif mode == "mic" and not mic:
        warnings.append("no microphone track; falling back to system audio" if sysa else
                        "no microphone track — using a silent track")
        mode = "system" if sysa else "silence"
    elif mode == "mic+system":
        if mute_mic:
            warnings.append("microphone muted in project; mixing system audio only")
            mode = "system" if sysa else "silence"
        elif not mic and sysa:
            warnings.append("no microphone; using system audio only")
            mode = "system"
        elif not mic and not sysa:
            warnings.append("no mic or system audio — using a silent track")
            mode = "silence"
    elif mode == "system" and not sysa:
        warnings.append("no system audio — using a silent track")
        mode = "silence"

    voice = {
        "mic": mic,
        "enhanced": enhanced,
        "mic+system": mic,
        "system": sysa,
        "silence": None,
    }.get(mode)
    sys_src = sysa if mode == "mic+system" else None
    return mode, voice, sys_src, warnings
