#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Patch 8b v2 for VCAM-Revise HookMain.java (Camera2 live stream injection).

What it does:
  1. Replaces the existing "Patch 8b" live block inside process_camera2_play()
     with a v2 block that:
       * logs video_path + which surfaces exist + imageReaderFormat (diagnostics),
       * logs clearly when vcam_stream.txt is NOT found,
       * feeds READER surfaces via LiveImageWriter (ImageWriter / YUV planes),
         instead of the GL renderer (GL/eglCreateWindowSurface is unreliable on
         YUV_420_888 ImageReader surfaces and was implicated in the crash),
       * keeps the GL renderer (LiveSurfaceRenderer) for PREVIEW surfaces.
  2. Adds LiveImageWriter.stopAll() next to LiveSurfaceRenderer.stopAll() in the
     Camera2 onOpened hook, so feeders are torn down on (re)open.

Idempotent: safe to run multiple times. Needs LiveImageWriter.java present in the
same package (com.example.vcam) and the updated LiveSurfaceRenderer.java.

Usage: run from the repo root (or anywhere above the sources):
    python3 patch_hookmain_camera2_v2.py
"""

import os
import re
import sys

START_MARK = "// === Patch 8b"
END_MARK_RE = r"// === end Patch 8b[^\n]*"
V2_TAG = "Patch 8b v2: live stream injection (Camera2)"

NEW_BLOCK = (
    '        // === Patch 8b v2: live stream injection (Camera2) ===\n'
    '        try {\n'
    '            XposedBridge.log("\u3010VCAM\u3011[c2-live] enter video_path=" + video_path\n'
    '                + " reader=" + (c2_reader_Surfcae != null)\n'
    '                + " reader1=" + (c2_reader_Surfcae_1 != null)\n'
    '                + " preview=" + (c2_preview_Surfcae != null)\n'
    '                + " preview1=" + (c2_preview_Surfcae_1 != null)\n'
    '                + " imageReaderFormat=" + imageReaderFormat);\n'
    '            File s_cfg_c2 = new File(video_path + "vcam_stream.txt");\n'
    '            if (!s_cfg_c2.exists()) {\n'
    '                XposedBridge.log("\u3010VCAM\u3011[c2-live] no vcam_stream.txt at " + video_path + " -> mp4 fallback");\n'
    '            } else {\n'
    '                String s_line_c2 = "";\n'
    '                java.io.BufferedReader s_br_c2 = new java.io.BufferedReader(new java.io.FileReader(s_cfg_c2));\n'
    '                try { s_line_c2 = s_br_c2.readLine(); } finally { s_br_c2.close(); }\n'
    '                if (s_line_c2 != null) { s_line_c2 = s_line_c2.trim(); }\n'
    '                if (s_line_c2 != null && s_line_c2.contains(":")) {\n'
    '                    String s_host_c2 = s_line_c2.substring(0, s_line_c2.indexOf(":")).trim();\n'
    '                    int s_port_c2 = Integer.parseInt(s_line_c2.substring(s_line_c2.indexOf(":") + 1).trim());\n'
    '                    LiveStreamPlayer.start(s_host_c2, s_port_c2, 0, 0);\n'
    '                    LiveSurfaceRenderer.stopAll();\n'
    '                    LiveImageWriter.stopAll();\n'
    '                    // Reader surfaces are ImageReader-backed (often YUV_420_888): GL is unreliable\n'
    '                    // there, so feed them via ImageWriter (YUV planes, center-cropped).\n'
    '                    if (c2_reader_Surfcae != null) { LiveImageWriter.startFor(c2_reader_Surfcae, "reader"); }\n'
    '                    if (c2_reader_Surfcae_1 != null) { LiveImageWriter.startFor(c2_reader_Surfcae_1, "reader1"); }\n'
    '                    // Preview surfaces are RGB/opaque (SurfaceView/TextureView): GL renderer is fine.\n'
    '                    if (c2_preview_Surfcae != null) { LiveSurfaceRenderer.startFor(c2_preview_Surfcae, "preview"); }\n'
    '                    if (c2_preview_Surfcae_1 != null) { LiveSurfaceRenderer.startFor(c2_preview_Surfcae_1, "preview1"); }\n'
    '                    XposedBridge.log("\u3010VCAM\u3011[c2-live] started(v2) reader=" + (c2_reader_Surfcae != null)\n'
    '                        + " reader1=" + (c2_reader_Surfcae_1 != null)\n'
    '                        + " preview=" + (c2_preview_Surfcae != null)\n'
    '                        + " preview1=" + (c2_preview_Surfcae_1 != null));\n'
    '                    return;\n'
    '                } else {\n'
    '                    XposedBridge.log("\u3010VCAM\u3011[c2-live] bad vcam_stream.txt content=" + s_line_c2);\n'
    '                }\n'
    '            }\n'
    '        } catch (Throwable c2_live_t) {\n'
    '            XposedBridge.log("\u3010VCAM\u3011[c2-live] EX " + c2_live_t);\n'
    '        }\n'
    '        // === end Patch 8b v2 ==='
)


def find_hookmain():
    best = None
    for root, _dirs, files in os.walk("."):
        if "HookMain.java" in files:
            p = os.path.join(root, "HookMain.java")
            norm = p.replace("\\", "/")
            if "com/example/vcam" in norm:
                return p
            if best is None:
                best = p
    return best


def replace_block(src):
    # Match from the first "// === Patch 8b" marker to the end of its
    # "// === end Patch 8b ..." line (works for both v1 and v2 markers).
    pat = re.compile(r"[ \t]*" + re.escape(START_MARK) + r".*?" + END_MARK_RE, re.DOTALL)
    if not pat.search(src):
        return src, False
    new_src = pat.sub(lambda m: NEW_BLOCK, src, count=1)
    return new_src, True


def add_onopen_companion(src):
    # Already has the companion right after the onOpened stopAll?
    if re.search(
        r"\u6253\u5f00\u76f8\u673aC2\"\);\s*LiveSurfaceRenderer\.stopAll\(\);\s*LiveImageWriter\.stopAll\(\);",
        src,
    ):
        return src, False
    pat = re.compile(
        r"(\u6253\u5f00\u76f8\u673aC2\"\);)(\s*\n)([ \t]*)(LiveSurfaceRenderer\.stopAll\(\);)"
    )
    def rep(m):
        return m.group(1) + m.group(2) + m.group(3) + m.group(4) + "\n" + m.group(3) + "LiveImageWriter.stopAll();"
    new_src, n = pat.subn(rep, src, count=1)
    return new_src, n > 0


def main():
    path = find_hookmain()
    if not path:
        print("ERROR: HookMain.java not found under", os.getcwd())
        sys.exit(1)
    print("Target:", path)
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    orig = src
    already_v2 = V2_TAG in src

    src, replaced = replace_block(src)
    if not replaced:
        print("ERROR: could not find a 'Patch 8b' block to replace.")
        print("       Make sure Patch 8b (v1) is present in process_camera2_play().")
        sys.exit(2)

    src, companion = add_onopen_companion(src)

    if src == orig:
        print("ALREADY PATCHED (no changes needed).")
        return

    with open(path, "w", encoding="utf-8") as f:
        f.write(src)

    if already_v2:
        print("PATCH OK (v2 block refreshed).")
    else:
        print("PATCH OK (upgraded Patch 8b -> v2).")
    print("  - reader surfaces  -> LiveImageWriter (ImageWriter / YUV)")
    print("  - preview surfaces -> LiveSurfaceRenderer (GL)")
    print("  - onOpened companion LiveImageWriter.stopAll():", "added" if companion else "already present")
    print("\nRemember to add LiveImageWriter.java and the updated LiveSurfaceRenderer.java")
    print("to app/src/main/java/com/example/vcam/ before building.")


if __name__ == "__main__":
    main()
