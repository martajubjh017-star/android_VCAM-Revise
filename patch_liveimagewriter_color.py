#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Patch 13: fix the blue tint in the Camera2 ImageWriter YUV path.

LiveImageWriter.fillImage() read the ARGB_8888 pixel as if it were ABGR:
    int r = rgb & 0xFF;          // this is actually BLUE
    int g = (rgb >> 8) & 0xFF;
    int b = (rgb >> 16) & 0xFF;  // this is actually RED
Android Bitmap.getPixels returns 0xAARRGGBB, so R and B were swapped ->
reddish skin got encoded as blue. Fix = correct channel extraction:
    int r = (rgb >> 16) & 0xFF;
    int g = (rgb >> 8) & 0xFF;
    int b = rgb & 0xFF;

Only touches LiveImageWriter.java (Camera2 reader / TikTok path). The Telegram
Camera1 path draws via a GL texture and is unaffected.

Idempotent (marker 'Patch 13'). Whitespace-tolerant. Asserts exactly 1 match.
"""
import os, re, sys

MARKER = "Patch 13"

SWAPPED = re.compile(
    r"int\s+r\s*=\s*rgb\s*&\s*0xFF\s*;"
    r"\s*int\s+g\s*=\s*\(\s*rgb\s*>>\s*8\s*\)\s*&\s*0xFF\s*;"
    r"\s*int\s+b\s*=\s*\(\s*rgb\s*>>\s*16\s*\)\s*&\s*0xFF\s*;"
)
FIXED = (
    "// === Patch 13: R/B channel fix (ARGB_8888 is 0xAARRGGBB) ===\n"
    "                int r = (rgb >> 16) & 0xFF;\n"
    "                int g = (rgb >> 8) & 0xFF;\n"
    "                int b = rgb & 0xFF;"
)


def find_file(root):
    cand = os.path.join(
        root, "app", "src", "main", "java", "com", "example", "vcam",
        "LiveImageWriter.java",
    )
    if os.path.isfile(cand):
        return cand
    for dirpath, _dirs, files in os.walk(root):
        if "LiveImageWriter.java" in files:
            return os.path.join(dirpath, "LiveImageWriter.java")
    return None


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    path = find_file(root)
    if not path:
        print("ERROR: LiveImageWriter.java not found under", root)
        sys.exit(2)
    print("Found:", path)
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read()

    if MARKER in src:
        print("ALREADY PATCHED (" + MARKER + ")")
        return

    n = len(SWAPPED.findall(src))
    if n != 1:
        print("ERROR: expected exactly 1 swapped-channel block, found", n)
        sys.exit(3)
    src = SWAPPED.sub(FIXED, src)

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src)
    print("PATCH OK (Patch 13: R/B channel fix -> correct colors on Camera2)")


if __name__ == "__main__":
    main()
