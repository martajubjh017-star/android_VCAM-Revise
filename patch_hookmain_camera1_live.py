#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Patch 9: live stream injection into the Camera1 preview surface path.

Telegram round-video (кружочки) uses the Camera1 API with setPreviewTexture
(创建预览) and startPreview (开始预览). VCAM currently plays virtual.mp4
onto that preview surface via MediaPlayer. This patch, when vcam_stream.txt
exists, instead drives the SAME preview surface with the live stream via
LiveSurfaceRenderer (GLES) fed by LiveStreamPlayer.latestBitmap, then returns
before the mp4 fallback.

Idempotent. Auto-finds HookMain.java.
"""
import os
import sys

MARKER = "Patch 9: live stream injection (Camera1 preview surface)"
ANCHOR = "start_preview_camera = (Camera) param.thisObject;"


def find_hookmain(start):
    candidates = [
        os.path.join(start, "app", "src", "main", "java", "com", "example", "vcam", "HookMain.java"),
        os.path.join(start, "HookMain.java"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    for root, _dirs, files in os.walk(start):
        if "HookMain.java" in files:
            return os.path.join(root, "HookMain.java")
    return None


def build_block(indent):
    lines = [
        "// === " + MARKER + " ===",
        "try {",
        '    File s_cfg_c1 = new File(video_path + "vcam_stream.txt");',
        "    if (s_cfg_c1.exists()) {",
        '        String s_line_c1 = "";',
        "        java.io.BufferedReader s_br_c1 = new java.io.BufferedReader(new java.io.FileReader(s_cfg_c1));",
        "        try { s_line_c1 = s_br_c1.readLine(); } finally { s_br_c1.close(); }",
        "        if (s_line_c1 != null) { s_line_c1 = s_line_c1.trim(); }",
        '        if (s_line_c1 != null && s_line_c1.contains(":")) {',
        '            String s_host_c1 = s_line_c1.substring(0, s_line_c1.indexOf(":")).trim();',
        '            int s_port_c1 = Integer.parseInt(s_line_c1.substring(s_line_c1.indexOf(":") + 1).trim());',
        "            LiveStreamPlayer.start(s_host_c1, s_port_c1, 0, 0);",
        "            LiveSurfaceRenderer.stopAll();",
        "            if (mplayer1 != null) { try { mplayer1.release(); } catch (Throwable ig1) {} mplayer1 = null; }",
        "            if (mMediaPlayer != null) { try { mMediaPlayer.release(); } catch (Throwable ig2) {} mMediaPlayer = null; }",
        '            String c1_started = "";',
        "            if (ori_holder != null && ori_holder.getSurface() != null && ori_holder.getSurface().isValid()) {",
        '                LiveSurfaceRenderer.startFor(ori_holder.getSurface(), "c1holder");',
        '                c1_started += "holder ";',
        "            }",
        "            if (mSurfacetexture != null) {",
        "                if (mSurface == null) { mSurface = new Surface(mSurfacetexture); }",
        "                else { mSurface.release(); mSurface = new Surface(mSurfacetexture); }",
        '                LiveSurfaceRenderer.startFor(mSurface, "c1tex");',
        '                c1_started += "tex ";',
        "            }",
        '            XposedBridge.log("\u3010VCAM\u3011[c1-live] started " + c1_started);',
        "            return;",
        "        }",
        "    }",
        "} catch (Throwable c1_live_t) {",
        '    XposedBridge.log("\u3010VCAM\u3011[c1-live]" + c1_live_t);',
        "}",
    ]
    return "".join(indent + ln + "\n" for ln in lines)


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    path = find_hookmain(start)
    if not path:
        print("ERROR: HookMain.java not found under", start)
        sys.exit(1)
    print("Found:", path)
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    if MARKER in src:
        print("ALREADY PATCHED")
        return

    idx = src.find(ANCHOR)
    if idx == -1:
        print("ERROR: anchor not found:", ANCHOR)
        sys.exit(2)

    # end of the anchor line
    line_end = src.find("\n", idx)
    if line_end == -1:
        print("ERROR: no newline after anchor")
        sys.exit(3)

    # indentation of the anchor line
    line_start = src.rfind("\n", 0, idx) + 1
    indent = ""
    for ch in src[line_start:idx]:
        if ch in (" ", "\t"):
            indent += ch
        else:
            break

    block = "\n" + build_block(indent)
    new_src = src[:line_end + 1] + block + src[line_end + 1:]
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_src)
    print("PATCH OK")


if __name__ == "__main__":
    main()
