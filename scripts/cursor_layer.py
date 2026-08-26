#!/usr/bin/env python3
"""Render a transparent cursor-overlay video from a .screenstudio bundle's mouse data.

Output: RGBA video (qtrle/argb) in POINT space (bounds WxH), CFR at the display fps,
starting t=0, containing only the animated cursor sprite + click ripples. Overlay it
(scaled by the pt->px factor) on the display stream AFTER the fps/CFR fix and BEFORE
zoompan, so it rides zooms exactly like Screen Studio.

Facts this encodes (learned from a real bundle):
  * mouse coords are in POINTS (display bounds space), not pixels.
  * t_video = (processTimeMs - processTimeStartMs_of_input_recorder) / 1000.
  * mouseclicks-0.json holds mouseDown/mouseUp PAIRS -> ripples on mouseDown only.
  * hotSpot/standardSize in cursors.json are in point units; on-screen size =
    standardSize * config.cursorSize.
  * the recorder only logs positions while the mouse moves -> plain lerp between
    samples is correct even across long gaps (next sample is spatially adjacent).

Usage:
  python3 cursor_layer.py --bundle X.screenstudio --work WORKDIR [--out cursor_layer.mov]
                          [--scale F] [--no-ripples] [--ema 0.35] [--frames N M ...]
"""
import argparse, json, math, os, subprocess, sys
from PIL import Image, ImageDraw
from render_lib import clean_path

RIPPLE_DUR, RIPPLE_R0, RIPPLE_DR = 0.45, 8.0, 37.0
RIPPLE_STROKE, RIPPLE_A0 = 3.0, 0.35
HOLD_GAP, LERP_TAIL = 0.5, 0.05


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--scale", type=float, default=None, help="override config.cursorSize")
    ap.add_argument("--ema", type=float, default=0.35)
    ap.add_argument("--no-ripples", action="store_true")
    ap.add_argument("--codec", choices=["qtrle", "png"], default="qtrle")
    ap.add_argument("--frames", type=int, nargs="*", default=None,
                    help="debug: render these frame indices as PNGs instead")
    a = ap.parse_args()

    bundle = clean_path(a.bundle)
    rec = os.path.join(bundle, "recording")
    work = clean_path(a.work)
    out = a.out or os.path.join(work, "cursor_layer.mov")
    os.makedirs(work, exist_ok=True)

    proj = json.load(open(os.path.join(bundle, "project.json")))["json"]
    meta = json.load(open(os.path.join(rec, "metadata.json")))
    chans = {r["id"]: r for r in meta["recorders"]}
    disp = next(r for r in chans.values() if r.get("type") == "display")["sessions"][0]
    inp = next((r for r in chans.values() if r.get("type") == "input"), None)
    sess = (inp["sessions"][0]) if inp else disp

    OFF = sess["processTimeStartMs"]
    b = disp.get("bounds") or {}
    W, H = int(b.get("width", 1710)), int(b.get("height", 1107))
    FPS = int(round(disp.get("displayRefreshRate") or 60))
    DUR = disp.get("durationMs", 0) / 1000.0
    NF = int(math.ceil(DUR * FPS)) + 1
    SCALE = a.scale if a.scale is not None else proj["config"].get("cursorSize", 1.5)

    cmeta = {c["id"]: c for c in json.load(open(os.path.join(rec, "cursors.json")))}
    mm = json.load(open(sess.get("mouseMovesFilename") and os.path.join(rec, sess["mouseMovesFilename"])
                        or os.path.join(rec, "mousemoves-0.json")))
    mc = json.load(open(sess.get("mouseClicksFilename") and os.path.join(rec, sess["mouseClicksFilename"])
                        or os.path.join(rec, "mouseclicks-0.json")))

    def t(ms):
        return max(0.0, (ms - OFF) / 1000.0)

    # sprites: any cursorId seen in the data whose png exists; fallback arrow
    seen = {e.get("cursorId", "arrow") for e in mm + mc}
    sprites = {}
    for cid in seen | {"arrow"}:
        p = os.path.join(rec, "cursors", f"{cid}.png")
        key = cid if os.path.exists(p) and cid in cmeta else "arrow"
        if key in sprites:
            continue
        im = Image.open(os.path.join(rec, "cursors", f"{key}.png")).convert("RGBA")
        c = cmeta[key]
        th = c["standardSize"]["height"] * SCALE
        s = th / im.height
        img = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
        sprites[key] = {"img": img, "hx": c["hotSpot"]["x"] * SCALE, "hy": c["hotSpot"]["y"] * SCALE}

    def skey(cid):
        return cid if cid in sprites else "arrow"

    moves = sorted((t(m["processTimeMs"]), float(m["x"]), float(m["y"])) for m in mm)
    cids = sorted((t(e["processTimeMs"]), e.get("cursorId", "arrow")) for e in mm + mc)
    downs = sorted((t(e["processTimeMs"]), float(e["x"]), float(e["y"]))
                   for e in mc if e.get("type") == "mouseDown")
    if not moves:
        raise RuntimeError("no mouse move data")

    # per-frame positions: lerp within gaps<=HOLD_GAP; hold+tail-lerp across longer gaps; EMA
    ts = [m[0] for m in moves]; xs = [m[1] for m in moves]; ys = [m[2] for m in moves]
    px = [0.0] * NF; py = [0.0] * NF
    j = 0
    for f in range(NF):
        tf = f / FPS
        if tf <= ts[0]:
            px[f], py[f] = xs[0], ys[0]; continue
        if tf >= ts[-1]:
            px[f], py[f] = xs[-1], ys[-1]; continue
        while j + 1 < len(ts) and ts[j + 1] <= tf:
            j += 1
        t0v, t1v = ts[j], ts[j + 1]
        gap = t1v - t0v
        if gap <= HOLD_GAP:
            fr = (tf - t0v) / gap if gap > 0 else 0
        else:
            ls = t1v - LERP_TAIL
            fr = 0.0 if tf <= ls else min(1.0, (tf - ls) / LERP_TAIL)
        px[f] = xs[j] + (xs[j + 1] - xs[j]) * fr
        py[f] = ys[j] + (ys[j + 1] - ys[j]) * fr
    al = a.ema
    for f in range(1, NF):
        px[f] = al * px[f] + (1 - al) * px[f - 1]
        py[f] = al * py[f] + (1 - al) * py[f - 1]

    # per-frame sprite key
    kts = [c[0] for c in cids]; kks = [skey(c[1]) for c in cids]
    keys = ["arrow"] * NF
    j = 0
    for f in range(NF):
        tf = f / FPS
        while j + 1 < len(kts) and kts[j + 1] <= tf:
            j += 1
        keys[f] = kks[j] if kts[j] <= tf else kks[0]

    # ripples by frame
    rip = {}
    if not a.no_ripples:
        for (tc, cx, cy) in downs:
            for f in range(max(0, math.ceil(tc * FPS)),
                           min(NF - 1, math.floor((tc + RIPPLE_DUR) * FPS)) + 1):
                p = (f / FPS - tc) / RIPPLE_DUR
                if 0 <= p <= 1:
                    rip.setdefault(f, []).append((cx, cy, p))

    def ripple_tile(radius, alpha):
        ext = radius + RIPPLE_STROKE / 2 + 2
        size = int(math.ceil(2 * ext)); ssf = 4
        big = Image.new("RGBA", (size * ssf, size * ssf), (0, 0, 0, 0))
        d = ImageDraw.Draw(big)
        c = size * ssf / 2
        rr = (radius + RIPPLE_STROKE / 2) * ssf
        d.ellipse([c - rr, c - rr, c + rr, c + rr], outline=(0, 0, 0, 255),
                  width=max(1, round(RIPPLE_STROKE * ssf)))
        tile = big.resize((size, size), Image.LANCZOS)
        r, g, bl, alp = tile.split()
        return Image.merge("RGBA", (r, g, bl, alp.point(lambda v: int(v * alpha)))), size / 2

    def clip_paste(canvas, tile, tx, ty):
        tw, th = tile.size
        ix, iy = int(math.floor(tx)), int(math.floor(ty))
        x0, y0 = max(0, ix), max(0, iy)
        x1, y1 = min(W, ix + tw), min(H, iy + th)
        if x0 >= x1 or y0 >= y1:
            return None
        sub = tile if (x0, y0, x1, y1) == (ix, iy, ix + tw, iy + th) else \
            tile.crop((x0 - ix, y0 - iy, x1 - ix, y1 - iy))
        canvas.alpha_composite(sub, dest=(x0, y0))
        return (x0, y0, x1, y1)

    def draw(canvas, f, state):
        if state.get("prev"):
            canvas.paste((0, 0, 0, 0), state["prev"])
        boxes = []
        for (cx, cy, p) in rip.get(f, ()):
            tile, off = ripple_tile(RIPPLE_R0 + RIPPLE_DR * p, RIPPLE_A0 * (1 - p))
            bb = clip_paste(canvas, tile, cx - off, cy - off)
            if bb: boxes.append(bb)
        sp = sprites[keys[f]]
        bb = clip_paste(canvas, sp["img"], px[f] - sp["hx"], py[f] - sp["hy"])
        if bb: boxes.append(bb)
        state["prev"] = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                         max(b[2] for b in boxes), max(b[3] for b in boxes)) if boxes else None

    if a.frames is not None:
        for f in a.frames:
            canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            draw(canvas, f, {})
            p = os.path.join(a.work, f"cltest_frame{f}.png")
            canvas.save(p)
            print(f"{p} key={keys[f]} pos=({px[f]:.1f},{py[f]:.1f}) ripples={len(rip.get(f, ()))}")
        return

    vargs = ["-c:v", "qtrle", "-pix_fmt", "argb"] if a.codec == "qtrle" else \
            ["-c:v", "png", "-pix_fmt", "rgba"]
    proc = subprocess.Popen(["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
                             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"] + vargs + [out],
                            stdin=subprocess.PIPE)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    state = {}
    try:
        for f in range(NF):
            draw(canvas, f, state)
            proc.stdin.write(canvas.tobytes())
            if f % 2000 == 0:
                print(f"frame {f}/{NF} t={f/FPS:.1f}s", flush=True)
    finally:
        proc.stdin.close()
    rc = proc.wait()
    print(f"done: {out} frames={NF} fps={FPS} canvas={W}x{H}pt exit={rc}")
    if rc:
        raise RuntimeError(f"cursor layer ffmpeg exited {rc}")


if __name__ == "__main__":
    main()
