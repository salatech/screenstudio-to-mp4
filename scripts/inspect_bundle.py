#!/usr/bin/env python3
"""Inspect a .screenstudio bundle: channels, geometry, config, and render-relevant warnings.

Usage: python3 inspect_bundle.py /path/to/Project.screenstudio
"""
import json, os, subprocess, sys
from render_lib import clean_path, find_captions, find_system_wallpaper, output_duration, parse_slices

FFPROBE = "ffprobe"
FFMPEG = "ffmpeg"


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def probe(path, entries="stream=codec_name,codec_type,width,height,r_frame_rate,avg_frame_rate:format=duration"):
    out = sh([FFPROBE, "-v", "error", "-show_entries", entries,
              "-of", "json", path])
    try:
        return json.loads(out)
    except Exception:
        return {}


def main(bundle):
    bundle = clean_path(bundle)
    rec = os.path.join(bundle, "recording")
    warnings = []

    meta_app = json.load(open(os.path.join(bundle, "meta.json")))
    proj = json.load(open(os.path.join(bundle, "project.json")))["json"]
    cfg = proj["config"]
    meta = json.load(open(os.path.join(rec, "metadata.json")))

    print(f"=== bundle: {os.path.basename(bundle)} ===")
    print(f"screen studio version: {meta_app['json'].get('version')}")
    print(f"scenes: {len(proj['scenes'])}")
    if len(proj["scenes"]) > 1:
        warnings.append(f"MULTIPLE SCENES: slices from all {len(proj['scenes'])} scenes are concatenated.")
    scene = proj["scenes"][0]

    # --- channels ---
    print("\n=== channels ===")
    channels = {}
    for r in meta.get("recorders", []):
        rid = r.get("id", "?")
        sess = (r.get("sessions") or [{}])[0]
        if len(r.get("sessions") or []) > 1:
            warnings.append(f"{rid}: multiple sessions; only session 0 handled.")
        channels[rid] = {"recorder": r, "session": sess}
        print(f"- {rid} (type={r.get('type')})")

    # display
    disp = None
    for rid, c in channels.items():
        if c["recorder"].get("type") == "display":
            disp = c
            m3u8 = os.path.join(rec, rid + "-0.m3u8")
            info = probe(m3u8)
            vs = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
            if vs:
                s = vs[0]
                px_w, px_h = s["width"], s["height"]
                b = c["session"].get("bounds") or {}
                pt_w, pt_h = b.get("width"), b.get("height")
                rfr, afr = s.get("r_frame_rate"), s.get("avg_frame_rate")
                dur = float(info.get("format", {}).get("duration", 0))
                print(f"  display: {px_w}x{px_h}px, bounds {pt_w}x{pt_h}pt "
                      f"(pt->px x{px_w/pt_w if pt_w else '?'}), r={rfr} avg={afr}, dur={dur:.2f}s")
                if rfr != afr:
                    warnings.append(
                        f"VFR CAPTURE: r_frame_rate={rfr} but avg={afr}. "
                        "MANDATORY: 'fps=<fps>,setpts=PTS-STARTPTS' before zoompan or A/V drifts.")

    # audio channels + loudness sample
    for rid, c in channels.items():
        t = c["recorder"].get("type")
        if t in ("systemAudio", "microphone"):
            m4a = os.path.join(rec, rid + "-0.m4a")
            if os.path.exists(m4a):
                out = sh([FFMPEG, "-hide_banner", "-ss", "30", "-t", "60", "-i", m4a,
                          "-af", "volumedetect", "-f", "null", "-"] )
                # volumedetect writes to stderr
                err = subprocess.run([FFMPEG, "-hide_banner", "-ss", "30", "-t", "60", "-i", m4a,
                                      "-af", "volumedetect", "-f", "null", "-"],
                                     capture_output=True, text=True).stderr
                mean = [l for l in err.splitlines() if "mean_volume" in l]
                lvl = mean[0].split("mean_volume:")[1].strip() if mean else "?"
                print(f"  {t}: {os.path.basename(m4a)} mean_volume(sample)={lvl}")
                if t == "systemAudio" and mean and float(lvl.split()[0]) < -70:
                    warnings.append("system audio appears SILENT; exclude it from the mix.")
    enh = os.path.join(rec, "enhanced")
    if os.path.isdir(enh):
        for f in os.listdir(enh):
            print(f"  enhanced audio available: enhanced/{f}")

    # webcam
    if not any(c["recorder"].get("type") == "camera" or "webcam" in rid
               for rid, c in channels.items()):
        warnings.append("no webcam channel found; render without camera bubble.")

    # --- timing anchor ---
    inp = next((c for c in channels.values() if c["recorder"].get("type") == "input"), None)
    t0 = (inp or disp)["session"].get("processTimeStartMs")
    print(f"\ntime anchor processTimeStartMs = {t0}")

    # --- config highlights ---
    print("\n=== project config ===")
    for k in ("backgroundType", "backgroundColor", "backgroundSystemName", "backgroundImage",
              "backgroundPaddingRatio", "windowBorderRadius", "cameraSize", "cameraPosition",
              "cameraRoundness", "hideCamera", "hideCursor", "cursorSize", "clickEffect",
              "showTranscript", "recordingCrop", "muteMicrophone", "muteSystemAudio"):
        print(f"  {k} = {cfg.get(k)}")
    if cfg.get("backgroundType") == "system" or cfg.get("backgroundSystemName"):
        wallpaper = find_system_wallpaper(cfg.get("backgroundSystemName"))
        if wallpaper:
            warnings.append(f"Screen Studio wallpaper: {cfg.get('backgroundSystemName')} -> {wallpaper}")
        else:
            warnings.append("Screen Studio wallpaper not found (install Screen Studio.app "
                            "or pass --frame <image>).")
    caps = find_captions(bundle)
    if caps:
        warnings.append(f"captions file found: {caps} (will be burned in)")
    elif cfg.get("showTranscript"):
        warnings.append("showTranscript=true but bundle has no .srt/.vtt; captions skipped.")
    crop = cfg.get("recordingCrop") or {}
    if crop and (crop.get("x"), crop.get("y"), crop.get("width"), crop.get("height")) != (0, 0, 1, 1):
        warnings.append(f"recordingCrop={crop}: display must be cropped; cursor overlay offset accordingly.")

    # --- scene ---
    raw_slices = []
    for sc in proj["scenes"]:
        raw_slices.extend(sc.get("slices") or [])
    slices = raw_slices
    slc = parse_slices(slices, 0)
    zooms = []
    for sc in proj["scenes"]:
        zooms.extend(z for z in sc.get("zoomRanges", []) if not z.get("isDisabled"))
    print(f"\n=== scene ===\n  slices: {len(slices)}")
    for s in slices:
        print(f"    {s['sourceStartMs']/1000:.2f}s -> {s['sourceEndMs']/1000:.2f}s  timeScale={s.get('timeScale',1)}")
    if any(abs(s.time_scale - 1.0) > 1e-6 for s in slc):
        warnings.append(
            f"speed edits (timeScale=1/speed): output {output_duration(slc):.1f}s "
            f"(follows the Screen Studio timeline, not raw source length)."
        )
    print(f"  zoomRanges (enabled): {len(zooms)}")
    for z in zooms[:20]:
        print(f"    {z['startTime']/1000:.2f}-{z['endTime']/1000:.2f}s zoom={z['zoom']} type={z['type']}")
    if any(z["type"] not in ("follow-click-groups", "manual") for z in zooms):
        warnings.append("zoom type beyond follow-click-groups/manual present; target falls back to center.")

    # input data for cursor
    mm = os.path.join(rec, "mousemoves-0.json")
    mc = os.path.join(rec, "mouseclicks-0.json")
    if os.path.exists(mm) and os.path.exists(mc):
        moves = json.load(open(mm)); clicks = json.load(open(mc))
        downs = [c for c in clicks if c.get("type") == "mouseDown"]
        print(f"\ncursor data: {len(moves)} moves, {len(clicks)} click events ({len(downs)} mouseDown)")
        print("  NOTE: click events are mouseDown/mouseUp PAIRS; ripples fire on mouseDown only.")
    else:
        warnings.append("mouse data missing; cursor layer unavailable.")

    print("\n=== WARNINGS ===")
    for w in warnings or ["(none)"]:
        print(f"  ! {w}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
