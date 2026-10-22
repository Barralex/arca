#!/usr/bin/env python3
"""Render the README banner and the link preview from the masthead itself.

    uv run python design/banner/build.py

Writes assets/brand/banner.png (1280x240, README header) and assets/brand/og-cover.png
(1280x640, GitHub's social preview: upload it by hand under Settings > Social preview). Both
are arca.ui.masthead.Scene, the same pixels the terminal draws, ten screen pixels to one scene
pixel: no browser and no dependency. Run it after touching the masthead. The Chrome runner the
README screenshot uses lives here too.
"""
import os
import pathlib
import struct
import subprocess
import sys
import zlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
BRAND = ROOT / "assets" / "brand"
sys.path.insert(0, str(ROOT))

WIDTH = 128  # scene pixels: wide enough for the dove between the name and the moon
SCALE = 10
OG_HEIGHT = 640  # GitHub's social preview is 2:1; sky above and sea below fill the rest

CHROMES = [os.environ.get("CHROME", ""),
           r"C:\Program Files\Google\Chrome\Application\chrome.exe",
           r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
           "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
           "google-chrome", "chromium"]


def shoot(url, out, width, height, transparent=False):
    chrome = next(c for c in CHROMES if c and (os.path.exists(c) or "/" not in c and "\\" not in c))
    args = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--allow-file-access-from-files",
            f"--window-size={width},{height}", "--virtual-time-budget=3000", f"--screenshot={out}", url]
    if transparent:
        args.insert(-1, "--default-background-color=00000000")
    subprocess.run(args, check=True, capture_output=True)


def write_png(path, rows):
    """A plain RGB PNG from rows of (r, g, b): the standard library is enough."""
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in rows)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    head = struct.pack(">IIBBBBB", len(rows[0]), len(rows), 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head) + chunk(b"IDAT", zlib.compress(raw, 9))
                     + chunk(b"IEND", b""))


def main():
    from arca.ui.masthead import Scene

    scene = Scene(WIDTH).paint()
    banner = [[px for px in row for _ in range(SCALE)] for row in scene.px for _ in range(SCALE)]
    write_png(BRAND / "banner.png", banner)
    width = len(banner[0])
    pad = (OG_HEIGHT - len(banner)) // 2
    sky, sea = scene.px[0][0], scene.px[-1][0]
    write_png(BRAND / "og-cover.png", [[sky] * width] * pad + banner + [[sea] * width] * pad)
    for name in ("banner.png", "og-cover.png"):
        out = BRAND / name
        print("wrote", out.relative_to(ROOT).as_posix(), f"{out.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
