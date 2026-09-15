#!/usr/bin/env python3
"""Re-time an animated GIF without re-rendering or re-quantising it.

Why this exists: matplotlib's PillowWriter COLLAPSES consecutive duplicate
frames, so the usual trick for a pause -- appending the final frame N times --
silently does nothing. A hold has to be applied as an explicit per-frame
duration instead. This script does that on an existing file, losslessly: frames
are copied in palette (P) mode, so only timing metadata changes.

Usage:
    retime_gif.py race.gif --factor 4                  # 4x slower, holds preserved
    retime_gif.py race.gif --frame-ms 280              # set every frame to 280ms
    retime_gif.py race.gif --factor 2 --first-ms 1200 --last-ms 4000
    retime_gif.py race.gif --factor 2 --out slow.gif   # write a copy instead

Notes:
  - GIF stores delays in 10ms units, so durations are rounded to the nearest 10
    and floored at 20ms (browsers treat <20ms as 100ms).
  - --first-ms/--last-ms are ABSOLUTE, applied after scaling. Scaling a hold
    along with playback speed is almost never what you want: 4x on a 4s end
    hold gives a 16s freeze.
"""
from __future__ import annotations
import argparse, os, sys

def main() -> int:
    ap = argparse.ArgumentParser(description="Re-time a GIF losslessly.")
    ap.add_argument("path")
    ap.add_argument("--factor", type=float, help="multiply every frame duration by this")
    ap.add_argument("--frame-ms", type=int, help="set every frame to this duration (overrides --factor)")
    ap.add_argument("--first-ms", type=int, help="absolute duration for the opening frame")
    ap.add_argument("--last-ms", type=int, help="absolute duration for the final frame (the end hold)")
    ap.add_argument("--out", help="output path (default: rewrite in place)")
    a = ap.parse_args()
    if a.factor is None and a.frame_ms is None:
        ap.error("pass --factor or --frame-ms")
    try:
        from PIL import Image, ImageSequence
    except ImportError:
        print("needs Pillow: pip install pillow", file=sys.stderr); return 1

    im = Image.open(a.path)
    frames, durs = [], []
    for f in ImageSequence.Iterator(im):
        frames.append(f.copy())                       # keeps mode P + palette
        durs.append(f.info.get("duration", 100))
    if not frames:
        print("no frames", file=sys.stderr); return 1

    new = [a.frame_ms if a.frame_ms else d * a.factor for d in durs]
    new = [max(20, int(round(d / 10.0)) * 10) for d in new]
    if a.first_ms is not None: new[0] = max(20, a.first_ms)
    if a.last_ms is not None:  new[-1] = max(20, a.last_ms)

    out = a.out or a.path
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=new,
                   loop=0, optimize=True, disposal=1)
    print(f"{os.path.basename(out)}: {len(frames)} frames, "
          f"{sum(durs)/1000:.1f}s -> {sum(new)/1000:.1f}s, "
          f"{os.path.getsize(out)/1024:.0f} KB")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
