#!/usr/bin/env python3
"""build_app.py: Automated packager for building a standalone macOS .app and .dmg.

Embeds Python runtime, Pillow, scripts, and static ffmpeg binaries into a 
drag-and-drop macOS Desktop Application bundle.
"""

import os
import sys
import shutil
import urllib.request
import subprocess
from pathlib import Path


def locate_or_download_ffmpeg(target_dir: str) -> str:
    """Ensure a static executable ffmpeg binary exists in target_dir for embedding."""
    os.makedirs(target_dir, exist_ok=True)
    target_ffmpeg = os.path.join(target_dir, "ffmpeg")

    if os.path.exists(target_ffmpeg) and os.access(target_ffmpeg, os.X_OK):
        print(f"✅ Found static ffmpeg at: {target_ffmpeg}")
        return target_ffmpeg

    # Check local system ffmpeg paths to copy
    for sys_path in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", shutil.which("ffmpeg")]:
        if sys_path and os.path.exists(sys_path) and os.access(sys_path, os.X_OK):
            print(f"📦 Copying system ffmpeg from {sys_path}...")
            shutil.copy2(sys_path, target_ffmpeg)
            os.chmod(target_ffmpeg, 0o755)
            return target_ffmpeg

    raise RuntimeError("Could not locate a valid ffmpeg executable to bundle. Please install ffmpeg via `brew install ffmpeg`.")


def check_and_install_pyinstaller():
    """Ensure PyInstaller is installed."""
    try:
        import PyInstaller
    except ImportError:
        print("📦 PyInstaller not found. Installing PyInstaller via pip...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)


def build_macos_app():
    """Build the standalone screenstudio-to-mp4.app and screenstudio-to-mp4.dmg."""
    project_root = os.path.abspath(os.path.dirname(__file__))
    build_dir = os.path.join(project_root, "build_assets")
    dist_dir = os.path.join(project_root, "dist")

    print("🚀 Starting screenstudio-to-mp4 macOS Standalone Build Engine...")

    # 1. Install/verify PyInstaller
    check_and_install_pyinstaller()

    # 2. Locate & stage static ffmpeg binary
    ffmpeg_binary = locate_or_download_ffmpeg(build_dir)

    # 3. Construct PyInstaller command
    # macOS Bundle options
    app_name = "screenstudio-to-mp4"
    entry_script = os.path.join(project_root, "gui.py")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",  # macOS GUI app (no terminal window)
        "--name", app_name,
        "--add-data", f"{os.path.join(project_root, 'scripts')}:scripts",
        "--add-binary", f"{ffmpeg_binary}:.",
        "--workpath", os.path.join(project_root, "build"),
        "--distpath", dist_dir,
        entry_script
    ]

    print(f"🔧 Running PyInstaller command: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

    app_path = os.path.join(dist_dir, f"{app_name}.app")
    print(f"🎉 Successfully generated macOS Application Bundle at:\n   {app_path}")

    # 4. Generate .dmg Disk Image using macOS native hdiutil
    dmg_path = os.path.join(dist_dir, f"{app_name}-macOS.dmg")
    if os.path.exists(dmg_path):
        os.remove(dmg_path)

    print(f"💿 Creating macOS Disk Image (.dmg) at:\n   {dmg_path}")
    dmg_cmd = [
        "hdiutil", "create",
        "-volname", app_name,
        "-srcfolder", app_path,
        "-ov",
        "-format", "UDZO",
        dmg_path
    ]
    subprocess.run(dmg_cmd, check=True)

    print("\n" + "="*60)
    print("✨ BUILD COMPLETE!")
    print(f"📦 macOS App Bundle: {app_path}")
    print(f"📀 macOS DMG Installer: {dmg_path}")
    print("="*60 + "\n")


if __name__ == "__main__":
    build_macos_app()
