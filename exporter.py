#!/usr/bin/env python3
"""exporter.py: Unified export engine for screenstudio-to-mp4.

Combines inspection, filtergraph preparation, cursor layer rendering, and ffmpeg
execution into a single Python API with real-time progress reporting.
"""

import os
import sys
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))
try:
    import inspect_bundle
    import prepare_render
    import cursor_layer
except ImportError:
    from scripts import inspect_bundle, prepare_render, cursor_layer


def find_ffmpeg() -> str:
    """Locate ffmpeg binary: bundled PyInstaller asset, system path, or Homebrew."""
    # 1. PyInstaller bundled location
    if hasattr(sys, "_MEIPASS"):
        bundled_ffmpeg = os.path.join(sys._MEIPASS, "ffmpeg")
        if os.path.exists(bundled_ffmpeg) and os.access(bundled_ffmpeg, os.X_OK):
            return bundled_ffmpeg

    # 2. Executable relative to application bundle
    app_res_ffmpeg = os.path.join(os.path.dirname(sys.executable), "..", "Resources", "ffmpeg")
    if os.path.exists(app_res_ffmpeg) and os.access(app_res_ffmpeg, os.X_OK):
        return app_res_ffmpeg

    # 3. System PATH check
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    # 4. Common macOS Homebrew locations
    for loc in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]:
        if os.path.exists(loc) and os.access(loc, os.X_OK):
            return loc

    return "ffmpeg"


class RenderExporter:
    """Unified exporter pipeline managing screenstudio rendering."""

    def __init__(self, bundle_path: str, output_path: str, work_dir: str = None, options: dict = None):
        self.bundle_path = os.path.abspath(bundle_path)
        self.output_path = os.path.abspath(output_path)
        self.work_dir = os.path.abspath(work_dir) if work_dir else tempfile.mkdtemp(prefix="screenstudio_work_")
        self.options = options or {}
        self.ffmpeg_path = find_ffmpeg()

    def run_pipeline(self, progress_callback=None) -> bool:
        """Run the complete export pipeline with progress callbacks.
        
        progress_callback signature: fn(status_text: str, percentage: float)
        """
        def update_progress(msg: str, pct: float):
            if progress_callback:
                progress_callback(msg, pct)

        try:
            os.makedirs(self.work_dir, exist_ok=True)
            output_dir = os.path.dirname(self.output_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)

            # Step 1: Inspect bundle
            update_progress("Inspecting .screenstudio project bundle...", 10.0)
            insp_data = inspect_bundle.inspect_bundle(self.bundle_path)

            # Step 2: Prepare render assets & filtergraphs
            update_progress("Generating render plan & filtergraph assets...", 25.0)
            
            # Prepare CLI-like arguments for prepare_render
            prep_args = [
                "--bundle", self.bundle_path,
                "--work", self.work_dir,
                "--output", self.output_path,
            ]
            
            if "frame" in self.options and self.options["frame"]:
                prep_args.extend(["--frame", self.options["frame"]])
            if "frame_blur" in self.options:
                prep_args.extend(["--frame-blur", str(self.options["frame_blur"])])
            if "screen_frac" in self.options:
                prep_args.extend(["--screen-frac", str(self.options["screen_frac"])])
            if "webcam" in self.options:
                prep_args.extend(["--webcam", str(self.options["webcam"])])
            if "zooms" in self.options:
                prep_args.extend(["--zooms", str(self.options["zooms"])])
            if "cursor" in self.options:
                prep_args.extend(["--cursor", str(self.options["cursor"])])
            if "audio_cleanup" in self.options:
                prep_args.extend(["--audio-cleanup", str(self.options["audio_cleanup"])])
            if "crf" in self.options:
                prep_args.extend(["--crf", str(self.options["crf"])])
            if "preset" in self.options:
                prep_args.extend(["--preset", str(self.options["preset"])])

            parser = prepare_render.build_parser()
            pargs = parser.parse_args(prep_args)
            prepare_render.prepare(pargs)

            # Step 3: Render cursor layer (if enabled)
            cursor_mode = self.options.get("cursor", "auto")
            if cursor_mode != "off":
                update_progress("Rendering animated cursor telemetry layer...", 45.0)
                cursor_parser = cursor_layer.build_parser()
                cargs = cursor_parser.parse_args([
                    "--bundle", self.bundle_path,
                    "--work", self.work_dir
                ])
                cursor_layer.run(cargs)

            # Step 4: Render composite video
            update_progress("Rendering composite video with zooms & effects...", 65.0)
            render_script = os.path.join(self.work_dir, "render_full.sh")
            self._execute_script(render_script)

            # Step 5: Process audio stream
            update_progress("Processing audio & applying normalization...", 85.0)
            audio_script = os.path.join(self.work_dir, "audio_build.sh")
            self._execute_script(audio_script)

            # Step 6: Final Muxing
            update_progress("Muxing video & audio into final MP4...", 95.0)
            mux_script = os.path.join(self.work_dir, "mux.sh")
            self._execute_script(mux_script)

            update_progress("Export completed successfully!", 100.0)
            return True

        except Exception as e:
            update_progress(f"Export failed: {str(e)}", -1.0)
            raise e

    def _execute_script(self, script_path: str):
        """Execute generated shell script replacing ffmpeg invocation if needed."""
        if not os.path.exists(script_path):
            raise FileNotFoundError(f"Script not found: {script_path}")

        # Inject absolute ffmpeg path if different from default system ffmpeg
        content = open(script_path).read()
        if self.ffmpeg_path != "ffmpeg":
            content = content.replace("ffmpeg ", f'"{self.ffmpeg_path}" ')
            open(script_path, "w").write(content)

        res = subprocess.run(["zsh", script_path], cwd=self.work_dir, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Execution error in {os.path.basename(script_path)}:\n{res.stderr}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 exporter.py <bundle_path> <output_mp4>")
        sys.exit(1)

    exp = RenderExporter(sys.argv[1], sys.argv[2])
    exp.run_pipeline(lambda msg, pct: print(f"[{pct:5.1f}%] {msg}"))
