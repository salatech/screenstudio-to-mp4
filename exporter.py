#!/usr/bin/env python3
"""Unified export engine for screenstudio-to-mp4.

Runs prepare → cursor → render → audio → mux with progress callbacks.
Works both from source and from a frozen macOS .app (PyInstaller).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, Optional

ProgressCb = Callable[[str, float], None]


def app_root() -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


ROOT = app_root()
SCRIPTS = os.path.join(ROOT, "scripts")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


def _bundled(name: str) -> Optional[str]:
    cand = os.path.join(ROOT, name)
    if os.path.isfile(cand) and os.access(cand, os.X_OK):
        return cand
    # PyInstaller sometimes puts binaries in a nested folder
    for sub in ("", "bin"):
        cand = os.path.join(ROOT, sub, name) if sub else os.path.join(ROOT, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def find_ffmpeg() -> str:
    bundled = _bundled("ffmpeg")
    if bundled:
        return bundled
    found = shutil.which("ffmpeg")
    if found:
        return found
    for loc in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if os.path.isfile(loc) and os.access(loc, os.X_OK):
            return loc
    return "ffmpeg"


def find_ffprobe() -> str:
    bundled = _bundled("ffprobe")
    if bundled:
        return bundled
    found = shutil.which("ffprobe")
    if found:
        return found
    for loc in ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe"):
        if os.path.isfile(loc) and os.access(loc, os.X_OK):
            return loc
    return "ffprobe"


def check_dependencies() -> list[str]:
    """Return missing dependency descriptions (empty if OK)."""
    missing = []
    if find_ffmpeg() == "ffmpeg" and not shutil.which("ffmpeg"):
        missing.append("ffmpeg (install with: brew install ffmpeg)")
    if find_ffprobe() == "ffprobe" and not shutil.which("ffprobe"):
        missing.append("ffprobe (comes with ffmpeg)")
    try:
        import PIL  # noqa: F401
    except ImportError:
        missing.append("Pillow (install with: pip3 install pillow)")
    return missing


def _call_with_argv(main_fn, argv: list[str]) -> None:
    old = sys.argv[:]
    try:
        sys.argv = argv
        main_fn()
    finally:
        sys.argv = old


class RenderExporter:
    """Drive the full Screen Studio → MP4 pipeline."""

    def __init__(
        self,
        bundle_path: str,
        output_path: str,
        work_dir: Optional[str] = None,
        options: Optional[dict] = None,
    ):
        self.bundle_path = os.path.abspath(os.path.expanduser(bundle_path.rstrip("/")))
        self.output_path = os.path.abspath(os.path.expanduser(output_path))
        self.work_dir = (
            os.path.abspath(os.path.expanduser(work_dir))
            if work_dir
            else tempfile.mkdtemp(prefix="screenstudio_work_")
        )
        self.options = options or {}
        self.ffmpeg_path = find_ffmpeg()
        self.ffprobe_path = find_ffprobe()

    def run_pipeline(self, progress_callback: Optional[ProgressCb] = None) -> bool:
        def update(msg: str, pct: float) -> None:
            if progress_callback:
                progress_callback(msg, pct)

        missing = check_dependencies()
        if missing:
            raise RuntimeError("Missing dependencies:\n- " + "\n- ".join(missing))

        if not os.path.isdir(self.bundle_path):
            raise FileNotFoundError(
                f"Bundle not found or not a folder:\n{self.bundle_path}\n\n"
                "Tip: .screenstudio files are packages — use the full path, "
                "or pick one from the detected list."
            )
        if not os.path.isfile(os.path.join(self.bundle_path, "project.json")):
            raise FileNotFoundError(
                f"Not a valid Screen Studio project:\n{self.bundle_path}\n"
                "(missing project.json)"
            )

        os.makedirs(self.work_dir, exist_ok=True)
        out_dir = os.path.dirname(self.output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        env = os.environ.copy()
        bin_dirs = []
        for p in (self.ffmpeg_path, self.ffprobe_path):
            d = os.path.dirname(p)
            if d and d not in bin_dirs:
                bin_dirs.append(d)
        if bin_dirs:
            env["PATH"] = os.pathsep.join(bin_dirs) + os.pathsep + env.get("PATH", "")

        try:
            import inspect_bundle
            import prepare_render
            import cursor_layer

            update("Reading your Screen Studio project…", 8.0)
            try:
                inspect_bundle.main(self.bundle_path)
            except SystemExit:
                pass
            except Exception:
                pass  # inspection is best-effort diagnostics

            update("Preparing video layout, zooms, and effects…", 20.0)
            prep = [
                "prepare_render.py",
                "--bundle",
                self.bundle_path,
                "--work",
                self.work_dir,
                "--output",
                self.output_path,
            ]
            opts = self.options
            if opts.get("frame"):
                prep.extend(["--frame", os.path.abspath(os.path.expanduser(opts["frame"]))])
            if "frame_blur" in opts:
                prep.extend(["--frame-blur", str(opts["frame_blur"])])
            if "screen_frac" in opts:
                prep.extend(["--screen-frac", str(opts["screen_frac"])])
            if opts.get("webcam"):
                prep.extend(["--webcam", str(opts["webcam"])])
            if opts.get("zooms"):
                prep.extend(["--zooms", str(opts["zooms"])])
            if opts.get("cursor"):
                prep.extend(["--cursor", str(opts["cursor"])])
            if opts.get("audio"):
                prep.extend(["--audio", str(opts["audio"])])
            if opts.get("audio_cleanup"):
                prep.extend(["--audio-cleanup", str(opts["audio_cleanup"])])
            if "crf" in opts:
                prep.extend(["--crf", str(opts["crf"])])
            if opts.get("preset"):
                prep.extend(["--preset", str(opts["preset"])])
            _call_with_argv(prepare_render.main, prep)

            cursor_mode = opts.get("cursor", "auto")
            if cursor_mode != "off":
                update("Drawing the mouse cursor and click ripples…", 40.0)
                _call_with_argv(
                    cursor_layer.main,
                    [
                        "cursor_layer.py",
                        "--bundle",
                        self.bundle_path,
                        "--work",
                        self.work_dir,
                    ],
                )

            update("Rendering video (this is the longest step)…", 55.0)
            self._run_script(os.path.join(self.work_dir, "render_full.sh"), env=env)

            update("Cleaning up and normalizing audio…", 85.0)
            self._run_script(os.path.join(self.work_dir, "audio_build.sh"), env=env)

            update("Combining video and audio into your MP4…", 95.0)
            self._run_script(os.path.join(self.work_dir, "mux.sh"), env=env)

            if not os.path.isfile(self.output_path):
                raise RuntimeError(f"Export finished but file not found:\n{self.output_path}")

            update("Done! Your MP4 is ready.", 100.0)
            return True
        except Exception as exc:
            update(f"Export failed: {exc}", -1.0)
            raise

    def _run_script(self, script_path: str, env: dict) -> None:
        if not os.path.isfile(script_path):
            raise FileNotFoundError(f"Missing generated script:\n{script_path}")

        content = open(script_path, encoding="utf-8").read()
        patched = content
        if self.ffmpeg_path != "ffmpeg":
            patched = patched.replace("ffmpeg ", f'"{self.ffmpeg_path}" ')
        if self.ffprobe_path != "ffprobe":
            patched = patched.replace("ffprobe ", f'"{self.ffprobe_path}" ')
        if patched != content:
            open(script_path, "w", encoding="utf-8").write(patched)

        res = subprocess.run(
            ["zsh", script_path],
            capture_output=True,
            text=True,
            env=env,
            cwd=self.work_dir,
        )
        if res.returncode != 0:
            err = (res.stderr or res.stdout or "").strip()
            raise RuntimeError(f"{os.path.basename(script_path)} failed:\n{err}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 exporter.py <bundle.screenstudio> <output.mp4>")
        sys.exit(1)

    exporter = RenderExporter(sys.argv[1], sys.argv[2])
    exporter.run_pipeline(lambda msg, pct: print(f"[{pct:5.1f}%] {msg}"))
