#!/usr/bin/env python3
"""Build a double-clickable macOS .app and .dmg for non-technical users.

Usage:
  python3 build_app.py

Requires: pip install pyinstaller pillow
          brew install ffmpeg   (copied into the app bundle)

Output:
  dist/screenstudio-to-mp4.app
  dist/screenstudio-to-mp4.dmg
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ASSETS = ROOT / "build_assets"
APP_NAME = "screenstudio-to-mp4"


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("Installing PyInstaller…")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyinstaller"],
            check=True,
        )


def stage_binaries() -> tuple[Path, Path]:
    ASSETS.mkdir(exist_ok=True)
    ffmpeg_dst = ASSETS / "ffmpeg"
    ffprobe_dst = ASSETS / "ffprobe"

    def resolve(name: str, dst: Path) -> Path:
        if dst.exists() and os.access(dst, os.X_OK):
            print(f"Using cached {dst}")
            return dst
        src = shutil.which(name)
        candidates = [
            src,
            f"/opt/homebrew/bin/{name}",
            f"/usr/local/bin/{name}",
        ]
        for c in candidates:
            if c and Path(c).is_file() and os.access(c, os.X_OK):
                print(f"Copying {c} → {dst}")
                shutil.copy2(c, dst)
                dst.chmod(0o755)
                return dst
        raise RuntimeError(
            f"Could not find {name}. Install with: brew install ffmpeg"
        )

    return resolve("ffmpeg", ffmpeg_dst), resolve("ffprobe", ffprobe_dst)


def build_app(ffmpeg: Path, ffprobe: Path) -> Path:
    ensure_pyinstaller()
    DIST.mkdir(exist_ok=True)

    # Clean previous app
    app_path = DIST / f"{APP_NAME}.app"
    if app_path.exists():
        shutil.rmtree(app_path)

    sep = os.pathsep  # : on macOS for --add-data
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        APP_NAME,
        "--osx-bundle-identifier",
        "com.salatech.screenstudio-to-mp4",
        f"--add-data={ROOT / 'scripts'}{sep}scripts",
        f"--add-binary={ffmpeg}{sep}.",
        f"--add-binary={ffprobe}{sep}.",
        f"--paths={ROOT}",
        f"--paths={ROOT / 'scripts'}",
        "--hidden-import=exporter",
        "--hidden-import=PIL",
        "--hidden-import=PIL.Image",
        "--hidden-import=PIL.ImageDraw",
        "--hidden-import=PIL.ImageFilter",
        "--hidden-import=inspect_bundle",
        "--hidden-import=prepare_render",
        "--hidden-import=cursor_layer",
        f"--workpath={BUILD}",
        f"--distpath={DIST}",
        f"--specpath={BUILD}",
        str(ROOT / "web_gui.py"),
    ]
    print("Running PyInstaller…")
    subprocess.run(cmd, check=True, cwd=ROOT)

    if not app_path.is_dir():
        raise RuntimeError(f"App bundle not created: {app_path}")
    print(f"App ready: {app_path}")
    return app_path


def build_dmg(app_path: Path) -> Path:
    dmg_path = DIST / f"{APP_NAME}.dmg"
    stage = DIST / "dmg_stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    staged_app = stage / app_path.name
    shutil.copytree(app_path, staged_app)
    os.symlink("/Applications", stage / "Applications")

    if dmg_path.exists():
        dmg_path.unlink()

    print("Creating DMG…")
    subprocess.run(
        [
            "hdiutil",
            "create",
            "-volname",
            "Screen Studio to MP4",
            "-srcfolder",
            str(stage),
            "-ov",
            "-format",
            "UDZO",
            str(dmg_path),
        ],
        check=True,
    )
    shutil.rmtree(stage)
    print(f"DMG ready: {dmg_path}")
    return dmg_path


def main() -> None:
    print("Building screenstudio-to-mp4 macOS app + DMG")
    ffmpeg, ffprobe = stage_binaries()
    app = build_app(ffmpeg, ffprobe)
    dmg = build_dmg(app)
    size_mb = dmg.stat().st_size / (1024 * 1024)
    print()
    print("=" * 56)
    print("BUILD COMPLETE")
    print(f"  App:  {app}")
    print(f"  DMG:  {dmg}  ({size_mb:.1f} MB)")
    print()
    print("For users: open the DMG → drag app to Applications → double-click.")
    print("=" * 56)


if __name__ == "__main__":
    main()
