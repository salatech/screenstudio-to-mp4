#!/usr/bin/env python3
"""Prepare a Screen-Studio-faithful ffmpeg render from a .screenstudio bundle.

Reads project.json / recording/metadata.json / mouse data, then writes into --work:
  bg.png screen_mask.png webcam_mask.png shadow.png     (composite assets)
  filter_full.txt filter_preview.txt                    (filtergraphs)
  render_full.sh render_preview.sh audio_build.sh mux.sh (runnable steps)
  plan.json                                             (geometry, zooms, verify points, warnings)

Key invariants baked in (learned the hard way):
  * Display captures are VFR -> the chain ALWAYS starts fps=<fps>,setpts=PTS-STARTPTS
    before zoompan, else video drifts ahead of audio proportionally to dropped frames.
  * Stream synchronization: PTS-STARTPTS zero-bases timestamps to prevent progressive audio drift.
  * zoompan (not crop) is used for animated zoom; its clock is output frame index: t=(on/fps).
  * Cursor layer (if enabled) is overlaid AFTER the fps fix and BEFORE zoompan so it
    rides zoom/pan exactly like Screen Studio.
  * Image loop inputs are capped with -t so concat graphs terminate.
  * ffmpeg 8.x: single-image outputs need -update 1; filter script files use -/filter_complex.
"""
import argparse, json, math, os, subprocess, sys
from PIL import Image, ImageDraw, ImageFilter

from render_lib import (
    audio_slice_filters,
    clean_path,
    find_captions,
    find_channel_media,
    find_system_wallpaper,
    fit_zoom,
    hide_cursor_enable,
    output_duration,
    parse_slices,
    screen_frac_from_padding,
    resolve_audio_sources,
    shell_quote,
    source_to_output_time,
    video_timeline_filter,
)


def even(x):
    return int(round(x)) - (int(round(x)) % 2)


def probe_video(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate",
                          "-show_entries", "format=duration", "-of", "json", path],
                         capture_output=True, text=True).stdout
    j = json.loads(out)
    s = j["streams"][0]
    return {"w": s["width"], "h": s["height"],
            "r": s["r_frame_rate"], "avg": s["avg_frame_rate"],
            "dur": float(j["format"]["duration"])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--output", default=None, help="final mp4 path (default: ~/Documents/<bundle>.mp4)")
    # background ("frame picture")
    ap.add_argument("--frame", default=None, help="user background image; else project gradient/color")
    ap.add_argument("--frame-blur", type=float, default=2.0)
    ap.add_argument("--frame-darken", type=float, default=0.03)
    # composition
    ap.add_argument("--out-width", type=int, default=0,
                    help="output width in pixels; 0 = match the recording")
    ap.add_argument("--screen-frac", type=float, default=None,
                    help="screen/canvas width; default = project padding")
    ap.add_argument("--webcam", choices=["auto", "on", "off"], default="auto")
    ap.add_argument("--webcam-margin", type=int, default=40)
    # effects
    ap.add_argument("--zooms", choices=["on", "off"], default="on")
    ap.add_argument("--zoom-ease", type=float, default=0.6)
    ap.add_argument("--cursor", choices=["auto", "on", "off"], default="auto")
    # quality
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--preset", default="slow")
    # audio
    ap.add_argument("--audio", choices=["auto", "mic", "enhanced", "mic+system", "system", "silence"],
                    default="auto")
    ap.add_argument("--audio-cleanup", choices=["none", "loudnorm", "voice"], default="loudnorm")
    ap.add_argument("--captions", default=None, help="optional .srt/.vtt to burn in")
    ap.add_argument("--motion-blur", choices=["auto", "on", "off"], default="auto")
    a = ap.parse_args()

    bundle = clean_path(a.bundle)
    rec = os.path.join(bundle, "recording")
    work = clean_path(a.work)
    os.makedirs(work, exist_ok=True)
    warnings = []

    proj = json.load(open(os.path.join(bundle, "project.json")))["json"]
    cfg = proj["config"]
    scenes = proj.get("scenes") or [{}]
    scene = scenes[0]
    if len(scenes) > 1:
        warnings.append(f"multiple scenes ({len(scenes)}); concatenating slices in order")
    meta = json.load(open(os.path.join(rec, "metadata.json")))
    chans = {r["id"]: r for r in meta["recorders"]}

    disp_id = next(i for i, r in chans.items() if r.get("type") == "display")
    disp_sess = chans[disp_id]["sessions"][0]
    display = os.path.join(rec, disp_id + "-0.m3u8")
    dp = probe_video(display)
    SRC_W, SRC_H = dp["w"], dp["h"]
    num, _, den = dp["r"].partition("/")
    FPS = int(round(int(num) / int(den or 1)))
    FPS = int(round(disp_sess.get("displayRefreshRate") or FPS)) or 60
    DUR = disp_sess.get("durationMs", dp["dur"] * 1000) / 1000.0
    bounds = disp_sess.get("bounds") or {"width": SRC_W, "height": SRC_H}
    PT2PX = SRC_W / bounds["width"]

    t0_ms = None
    for r in chans.values():
        if r.get("type") == "input":
            t0_ms = r["sessions"][0]["processTimeStartMs"]
    if t0_ms is None:
        t0_ms = disp_sess["processTimeStartMs"]

    # recordingCrop (fraction of full frame)
    rc = cfg.get("recordingCrop") or {"x": 0, "y": 0, "width": 1, "height": 1}
    CRX, CRY = int(rc["x"] * SRC_W), int(rc["y"] * SRC_H)
    CRW, CRH = even(rc["width"] * SRC_W), even(rc["height"] * SRC_H)
    cropped = (CRX, CRY, CRW, CRH) != (0, 0, SRC_W, SRC_H)

    # webcam
    cam_id = next((i for i, r in chans.items()
                   if r.get("type") == "camera" or "webcam" in i), None)
    use_cam = (a.webcam == "on") or (a.webcam == "auto" and cam_id and not cfg.get("hideCamera"))
    webcam = find_channel_media(rec, cam_id, exts=(".m3u8", ".mp4")) if cam_id else None
    if use_cam and not cam_id:
        warnings.append("webcam requested but no channel found; disabled")
        use_cam = False
    if use_cam and not webcam:
        warnings.append("webcam channel has no media; disabled")
        use_cam = False

    # audio choice
    enhanced = None
    enh_dir = os.path.join(rec, "enhanced")
    if os.path.isdir(enh_dir):
        for f in sorted(os.listdir(enh_dir)):
            if f.endswith((".m4a", ".wav", ".mp3")):
                enhanced = os.path.join(enh_dir, f)
    sys_id = next((i for i, r in chans.items() if r.get("type") == "systemAudio"), None)
    sys_probe = find_channel_media(rec, sys_id)
    system_silent = None
    if a.audio == "auto" and not enhanced and sys_probe:
        mic_id = next((i for i, r in chans.items() if r.get("type") == "microphone"), None)
        if not find_channel_media(rec, mic_id):
            err = subprocess.run(
                ["ffmpeg", "-hide_banner", "-ss", "5", "-t", "8", "-i", sys_probe,
                 "-af", "volumedetect", "-f", "null", "-"],
                capture_output=True, text=True,
            ).stderr
            for line in err.splitlines():
                if "mean_volume" in line:
                    try:
                        system_silent = float(line.split("mean_volume:")[1].split()[0]) < -70
                    except ValueError:
                        system_silent = False
    audio_mode, voice_src, sysa, audio_warns = resolve_audio_sources(
        rec, chans, a.audio,
        mute_mic=bool(cfg.get("muteMicrophone")),
        mute_system=bool(cfg.get("muteSystemAudio")),
        enhanced=enhanced,
        system_silent=system_silent,
    )
    warnings.extend(audio_warns)

    # cursor
    use_cursor = (a.cursor == "on") or (a.cursor == "auto" and not cfg.get("hideCursor")
                                        and os.path.exists(os.path.join(rec, "mousemoves-0.json")))

    # ---- composition geometry (canvas and zoom follow the project) ----
    if a.out_width and a.out_width > 0:
        OUT_W = even(a.out_width)
    else:
        OUT_W = even(CRW)
    OUT_H = even(OUT_W * CRH / CRW)
    if a.screen_frac is not None:
        screen_frac = float(a.screen_frac)
    else:
        screen_frac = screen_frac_from_padding(cfg.get("backgroundPaddingRatio"))
    screen_frac = max(0.5, min(1.0, screen_frac))
    Sw = even(OUT_W * screen_frac)
    Sh = even(Sw * CRH / CRW)
    OX, OY = even((OUT_W - Sw) / 2), even((OUT_H - Sh) / 2)
    Rs = max(int(round(cfg.get("windowBorderRadius", 12) * Sw / bounds["width"])),
             int(round(Sw * 0.014)))
    sc = Sw / bounds["width"]
    SH_BLUR = cfg.get("shadowBlur", 20) * sc
    SH_ANG = math.radians(cfg.get("shadowAngle", 90))
    SH_DIST = cfg.get("shadowDistance", 25) * sc
    SH_DX, SH_DY = int(round(math.cos(SH_ANG) * SH_DIST)), int(round(math.sin(SH_ANG) * SH_DIST))
    SH_A = 0.45 * cfg.get("shadowIntensity", 1)

    cam_size = (cfg.get("defaultLayout") or {}).get("cameraSize") or cfg.get("cameraSize", 0.25)
    Cw = even(OUT_W * cam_size); Ch = even(Cw * 9 / 16)
    Rc = int(round(cfg.get("cameraRoundness", 0.2) * min(Cw, Ch) / 2))
    pp = cfg.get("cameraPositionPoint") or {"x": 1, "y": 1}
    M = a.webcam_margin
    CamX = M if pp["x"] == 0 else (OUT_W - Cw - M if pp["x"] == 1 else even((OUT_W - Cw) / 2))
    CamY = M if pp["y"] == 0 else (OUT_H - Ch - M if pp["y"] == 1 else even((OUT_H - Ch) / 2))

    # ---- slices (cuts + speed ramps from every scene) ----
    raw_slices = []
    for sc in scenes:
        raw_slices.extend(sc.get("slices") or [])
    if not raw_slices:
        raw_slices = [{"sourceStartMs": 0, "sourceEndMs": DUR * 1000, "timeScale": 1}]
    slc = parse_slices(raw_slices, DUR)
    ramps = [s for s in slc if abs(s.time_scale - 1.0) > 1e-6]
    if ramps:
        warnings.append(
            f"applied {len(ramps)} speed edit(s) (timeScale=1/speed); "
            f"output {output_duration(slc):.1f}s from {DUR:.1f}s source"
        )
    seg = [(s.start, s.end) for s in slc]
    out_dur = output_duration(slc)

    # ---- assets ----
    def rounded(w, h, r):
        m = Image.new("L", (w, h), 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
        return m
    rounded(Sw, Sh, Rs).save(f"{work}/screen_mask.png")
    if use_cam:
        rounded(Cw, Ch, Rc).save(f"{work}/webcam_mask.png")
    sh_img = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    ImageDraw.Draw(sh_img).rounded_rectangle(
        [OX + SH_DX, OY + SH_DY, OX + SH_DX + Sw - 1, OY + SH_DY + Sh - 1],
        radius=Rs, fill=(0, 0, 0, int(255 * SH_A)))
    sh_img.filter(ImageFilter.GaussianBlur(SH_BLUR)).save(f"{work}/shadow.png")

    # background: user frame > system wallpaper on disk > project gradient > solid
    frame_path = clean_path(a.frame) if a.frame else None
    used_studio_wallpaper = False
    if not frame_path and (cfg.get("backgroundType") == "system" or cfg.get("backgroundSystemName")):
        frame_path = find_system_wallpaper(cfg.get("backgroundSystemName"))
        if frame_path:
            used_studio_wallpaper = True
            warnings.append(f"using Screen Studio wallpaper {cfg.get('backgroundSystemName')}")
        else:
            warnings.append(
                f"project uses Screen Studio wallpaper '{cfg.get('backgroundSystemName')}' "
                f"but it could not be extracted (is Screen Studio.app installed?). "
                f"Falling back to project gradient/color. Pass --frame to override."
            )
    if frame_path:
        vf = (f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
              f"crop={OUT_W}:{OUT_H}")
        blur = a.frame_blur
        if blur <= 0 and cfg.get("backgroundBlur"):
            blur = float(cfg.get("backgroundBlur") or 0)
        if blur > 0:
            vf += f",gblur=sigma={blur}"
        if not used_studio_wallpaper and a.frame_darken:
            vf += f",eq=brightness=-{a.frame_darken}:saturation=1.05"
        subprocess.run(["ffmpeg", "-hide_banner", "-y", "-i", frame_path, "-vf", vf,
                        "-frames:v", "1", "-update", "1", f"{work}/bg.png"], check=True)
        bg_desc = f"Screen Studio wallpaper {cfg.get('backgroundSystemName')}" if used_studio_wallpaper \
            else f"frame image {frame_path}"
    else:
        g = cfg.get("backgroundGradient")
        if g and g.get("stops"):
            c0 = g["stops"][0]["color"].lstrip("#"); c1 = g["stops"][-1]["color"].lstrip("#")
            x0, y0 = g["start"]["x"] * OUT_W, g["start"]["y"] * OUT_H
            x1, y1 = g["end"]["x"] * OUT_W, g["end"]["y"] * OUT_H
            src = (f"gradients=s={OUT_W}x{OUT_H}:c0=0x{c0}:c1=0x{c1}:"
                   f"x0={x0:.0f}:y0={y0:.0f}:x1={x1:.0f}:y1={y1:.0f}:type=linear:d=1")
            bg_desc = f"project gradient #{c0}->#{c1}"
        else:
            col = (cfg.get("backgroundColor") or "#222222").lstrip("#")
            src = f"color=c=0x{col}:s={OUT_W}x{OUT_H}:d=1"
            bg_desc = f"solid #{col}"
        subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", src,
                        "-frames:v", "1", "-update", "1", f"{work}/bg.png"], check=True)

    # ---- zoom expression ----
    clicks = []
    mc = os.path.join(rec, "mouseclicks-0.json")
    if os.path.exists(mc):
        clicks = json.load(open(mc))
    zooms = []
    if a.zooms == "on":
        for sc in scenes:
            zooms.extend(z for z in sc.get("zoomRanges", []) if not z.get("isDisabled"))
    zrows = []
    T = f"(on/{FPS})"
    DE = a.zoom_ease

    def ss(arg):
        u = f"clip({arg},0,1)"
        return f"({u}*{u}*(3-2*{u}))"

    if zooms:
        e, tx, ty = [], [], []
        for z in zooms:
            za, zb, zf = z["startTime"] / 1000, z["endTime"] / 1000, float(z["zoom"])
            snap = float(z.get("snapToEdgesRatio") or 0.25)
            if z["type"] == "manual":
                cx = z["manualTargetPoint"]["x"] * SRC_W - CRX
                cy = z["manualTargetPoint"]["y"] * SRC_H - CRY
                n = -1
            else:
                pts = [c for c in clicks
                       if c.get("type") == "mouseDown"
                       and za - 0.2 <= (c["processTimeMs"] - t0_ms) / 1000 <= zb + 0.2]
                if pts:
                    xs = [c["x"] * PT2PX - CRX for c in pts]
                    ys = [c["y"] * PT2PX - CRY for c in pts]
                    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
                    zf = fit_zoom(zf, max(xs) - min(xs), max(ys) - min(ys),
                                  CRW, CRH, snap)
                else:
                    cx, cy = CRW / 2, CRH / 2
                n = len(pts)
            zf = max(1.0, min(zf, float(z["zoom"])))
            e.append(f"between({T},{za:.4f},{zb:.4f})*{zf-1:.3f}*"
                     f"min({ss(f'({T}-{za:.4f})/{DE}')},{ss(f'({zb:.4f}-{T})/{DE}')})")
            tx.append(f"between({T},{za:.4f},{zb:.4f})*{cx:.1f}")
            ty.append(f"between({T},{za:.4f},{zb:.4f})*{cy:.1f}")
            zrows.append({"id": z["id"], "start": za, "end": zb, "zoom": round(zf, 3),
                          "tx": round(cx), "ty": round(cy), "nclicks": n})
        Z = "(1+(" + "+".join(e) + "))"
        TX = "(" + "+".join(tx) + ")"
        TY = "(" + "+".join(ty) + ")"
        zoomf = (f"zoompan=z='{Z}':x='clip(({TX})-(iw/{Z})/2,0,iw-iw/{Z})':"
                 f"y='clip(({TY})-(ih/{Z})/2,0,ih-ih/{Z})':d=1:s={Sw}x{Sh}:fps={FPS}")
    else:
        zoomf = f"scale={Sw}:{Sh}:flags=lanczos"

    use_blur = (a.motion_blur == "on") or (
        a.motion_blur == "auto" and float(cfg.get("motionBlurAmount") or 0) > 0
    )
    if use_blur:
        mb = max(2, min(5, int(round(1 + 2 * float(cfg.get("motionBlurAmount") or 1)))))
        weights = " ".join(str(i) for i in range(1, mb + 1))
        zoomf += f",tmix=frames={mb}:weights='{weights}'"

    # ---- filtergraph (input order is fixed and mirrored in the shell scripts) ----
    # 0 display, 1 mic-or-voice(placeholder, video render maps [vout] only),
    # then loop images, then optional webcam, then optional cursor. To keep indices
    # simple we emit: 0 display, [1 webcam?], [2.. images], [cursor last].
    inputs = [("display", display)]
    if use_cam:
        inputs.append(("webcam", webcam))
    img_start = len(inputs)
    imgs = ["bg.png", "shadow.png", "screen_mask.png"] + (["webcam_mask.png"] if use_cam else [])
    for im in imgs:
        inputs.append(("img", f"{work}/{im}"))
    cursor_idx = None
    if use_cursor:
        cursor_idx = len(inputs)
        inputs.append(("cursor", f"{work}/cursor_layer.mov"))
    iBG, iSH, iSM = img_start, img_start + 1, img_start + 2
    iCM = img_start + 3 if use_cam else None
    iCAM = 1 if use_cam else None

    pre = f"crop={CRW}:{CRH}:{CRX}:{CRY}," if cropped else ""
    head = []
    if use_cursor:
        head.append(f"[{cursor_idx}:v]scale={even(bounds['width']*PT2PX)}:{even(bounds['height']*PT2PX)}[curs]")
        head.append(f"[0:v]{pre}fps={FPS},setpts=PTS-STARTPTS[vfix]")
        ox = -CRX if cropped else 0
        oy = -CRY if cropped else 0
        ov = f"[vfix][curs]overlay={ox}:{oy}:eof_action=repeat:format=auto"
        hide = hide_cursor_enable(slc)
        if hide:
            ov += f":enable='{hide}'"
        head.append(f"{ov}[vcur]")
        head.append(f"[vcur]{zoomf},setsar=1[scr]")
    else:
        head.append(f"[0:v]{pre}fps={FPS},setpts=PTS-STARTPTS,{zoomf},setsar=1[scr]")

    comp = [f"[{iSM}:v]format=gray[sm]",
            "[scr][sm]alphamerge[scrA]",
            f"[{iBG}:v]scale={OUT_W}:{OUT_H},setsar=1[bg]",
            f"[bg][{iSH}:v]overlay=0:0[bg2]",
            f"[bg2][scrA]overlay={OX}:{OY}[c0]"]
    last = "c0"
    if use_cam:
        comp += [f"[{iCAM}:v]scale={Cw}:{Ch}:flags=lanczos,setsar=1[cam]",
                 f"[{iCM}:v]format=gray[cm]", "[cam][cm]alphamerge[camA]",
                 f"[c0][camA]overlay={CamX}:{CamY}[comp]"]
        last = "comp"

    cap_path = a.captions or find_captions(bundle)
    timeline_out = "vout" if not cap_path else "vedit"
    cut = [video_timeline_filter(slc, FPS, DUR, last, timeline_out)]
    if cap_path:
        # subtitles filter: escape the few chars ffmpeg treats specially
        cap_esc = cap_path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        cut.append(
            f"[{timeline_out}]subtitles='{cap_esc}':force_style="
            f"'FontName=Helvetica,FontSize=22,PrimaryColour=&H00FFFFFF,Outline=2'"
            f"[vout]"
        )
        warnings.append(f"burning captions from {cap_path}")
    elif cfg.get("showTranscript"):
        warnings.append("showTranscript=true but no .srt/.vtt in the bundle; captions skipped")

    fg = ";\n".join(head + comp + cut)
    open(f"{work}/filter_full.txt", "w").write(fg)

    # preview: same composite, but a 9 s trim window (around the first zoom) instead of the cut
    pv_start = max(0.0, (zrows[0]["start"] - 1.5) if zrows else DUR * 0.1)
    pfg = ";\n".join(head + comp) + \
        f";\n[{last}]trim={pv_start:.2f}:{pv_start+9:.2f},setpts=PTS-STARTPTS[vout]"
    open(f"{work}/filter_preview.txt", "w").write(pfg)

    # ---- shell steps ----
    out_mp4 = clean_path(a.output) if a.output else os.path.expanduser(
        f"~/Downloads/{os.path.basename(bundle).rsplit('.', 1)[0]}.mp4")
    cap = int(DUR) + 5

    def input_args(preview=False):
        parts = []
        for kind, path in inputs:
            q = shell_quote(path)
            if kind == "img":
                parts.append(f"-loop 1 -framerate {FPS} -t {cap} -i {q}")
            else:
                parts.append(f"-i {q}")
        return " \\\n  ".join(parts)

    open(f"{work}/render_full.sh", "w").write(f"""#!/bin/zsh
set -e
cd {shell_quote(work)}
ffmpeg -hide_banner -y -progress pipe:1 -nostats \\
  {input_args()} \\
  -/filter_complex filter_full.txt \\
  -map "[vout]" -an \\
  -c:v libx264 -preset {a.preset} -crf {a.crf} -pix_fmt yuv420p -profile:v high \\
  -fps_mode cfr -r {FPS} -movflags +faststart \\
  video_only.mp4
echo RENDER_EXIT=$?
""")
    open(f"{work}/render_preview.sh", "w").write(f"""#!/bin/zsh
set -e
cd {shell_quote(work)}
ffmpeg -hide_banner -y \\
  {input_args(True)} \\
  -/filter_complex filter_preview.txt \\
  -map "[vout]" -t 9 -c:v libx264 -preset ultrafast -crf 20 -pix_fmt yuv420p preview.mp4
echo PREVIEW_EXIT=$?
""")

    # audio: slices + speed ramps + optional cleanup -> voice.m4a ; mux.sh joins
    CLEAN = {"none": "",
             "loudnorm": "loudnorm=I=-16:TP=-1.5:LRA=11",
             "voice": ("highpass=f=80,equalizer=f=130:t=q:w=1.1:g=-5,"
                        "equalizer=f=250:t=q:w=1.3:g=-3,afftdn=nf=-28:nr=10,"
                        "equalizer=f=3500:t=q:w=1.6:g=2,loudnorm=I=-16:TP=-1.5:LRA=11")}[a.audio_cleanup]
    if audio_mode == "silence" or not voice_src:
        open(f"{work}/audio_build.sh", "w").write(f"""#!/bin/zsh
set -e
cd {shell_quote(work)}
ffmpeg -hide_banner -y -f lavfi -i anullsrc=r=48000:cl=stereo -t {out_dur:.3f} \\
  -c:a aac -b:a 192k voice.m4a
echo AUDIO_EXIT=$?
""")
    else:
        mixed = audio_mode == "mic+system" and bool(sysa)
        afilter = audio_slice_filters(slc, CLEAN if audio_mode != "silence" else "", mixed=mixed)
        extra = f" -i {shell_quote(sysa)}" if mixed else ""
        open(f"{work}/audio_build.sh", "w").write(f"""#!/bin/zsh
set -e
cd {shell_quote(work)}
ffmpeg -hide_banner -y -progress pipe:1 -nostats -i {shell_quote(voice_src)}{extra} \\
  -filter_complex "{afilter}" \\
  -map "[out]" -c:a aac -b:a 192k voice.m4a
echo AUDIO_EXIT=$?
""")
    open(f"{work}/mux.sh", "w").write(f"""#!/bin/zsh
set -e
cd {shell_quote(work)}
ffmpeg -hide_banner -y -i video_only.mp4 -i voice.m4a \\
  -map 0:v:0 -map 1:a:0 -c copy -movflags +faststart -shortest {shell_quote(out_mp4)}
echo MUX_EXIT=$?  OUTPUT={shell_quote(out_mp4)}
""")
    for f in ("render_full.sh", "render_preview.sh", "audio_build.sh", "mux.sh"):
        os.chmod(f"{work}/{f}", 0o755)

    # verification points: src times inside slices mapped to output times
    ver = []
    for s in slc:
        for frac in (0.3, 0.7):
            st = s.start + s.src_dur * frac
            ot = source_to_output_time(st, slc)
            if ot is not None:
                ver.append({"src_t": round(st, 2), "out_t": round(ot, 2)})
    ver = ver[:4]

    plan = {"bundle": bundle, "output": out_mp4, "work": work,
            "source": {"px": [SRC_W, SRC_H], "pt": [bounds["width"], bounds["height"]],
                       "pt2px": PT2PX, "fps": FPS, "dur_s": round(DUR, 3),
                       "vfr": dp["r"] != dp["avg"], "t0_ms": t0_ms,
                       "crop": [CRX, CRY, CRW, CRH] if cropped else None},
            "composition": {"out": [OUT_W, OUT_H], "screen": [Sw, Sh, OX, OY, Rs],
                            "screen_frac": screen_frac,
                            "webcam": [Cw, Ch, CamX, CamY, Rc] if use_cam else None,
                            "background": bg_desc, "motion_blur": use_blur},
            "audio": {"mode": audio_mode, "cleanup": a.audio_cleanup, "src": voice_src},
            "cursor": use_cursor, "zooms": zrows,
            "captions": cap_path,
            "slices": [{"start": s.start, "end": s.end, "time_scale": s.time_scale,
                        "out_dur": round(s.out_dur, 3)} for s in slc],
            "out_dur_s": round(out_dur, 2),
            "preview_start_src_s": round(pv_start, 2),
            "verify_points": ver, "warnings": warnings}
    json.dump(plan, open(f"{work}/plan.json", "w"), indent=2)
    print(json.dumps(plan, indent=2))


if __name__ == "__main__":
    main()
