#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Patch 8b: inject live face-swap frames into VCAM's Camera2 path.
#
# What it does to HookMain.java:
#  1) At the top of process_camera2_play(), add a live branch: if
#     DCIM/Camera1/vcam_stream.txt exists, start LiveStreamPlayer and render
#     LiveStreamPlayer.latestBitmap onto the app's Camera2 preview/reader
#     surfaces via LiveSurfaceRenderer (OpenGL), then return -- skipping the
#     original virtual.mp4 MediaPlayer/VideoToFrames path. Without the config
#     file it falls through to the unmodified mp4 behaviour.
#  2) In onOpened() (camera open), add LiveSurfaceRenderer.stopAll() so old
#     renderers are torn down on every (re)open.
#
# Idempotent: re-running detects the marker and does nothing.
# Run from the repo root:  python patch_hookmain_camera2.py

import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CANDIDATES = [
    os.path.join(HERE, "app", "src", "main", "java", "com", "example", "vcam", "HookMain.java"),
    os.path.join(HERE, "HookMain.java"),
]

MARKER = "Patch 8b: live stream injection (Camera2)"
PLAY_ANCHOR = "private void process_camera2_play() {"
OPEN_ANCHOR = u'打开相机C2");'  # inside XposedBridge.log("【VCAM】打开相机C2");

PLAY_LINES = [
    "// === Patch 8b: live stream injection (Camera2) ===",
    "try {",
    "    File s_cfg_c2 = new File(video_path + \"vcam_stream.txt\");",
    "    if (s_cfg_c2.exists()) {",
    "        String s_line_c2 = \"\";",
    "        java.io.BufferedReader s_br_c2 = new java.io.BufferedReader(new java.io.FileReader(s_cfg_c2));",
    "        try { s_line_c2 = s_br_c2.readLine(); } finally { s_br_c2.close(); }",
    "        if (s_line_c2 != null) { s_line_c2 = s_line_c2.trim(); }",
    "        if (s_line_c2 != null && s_line_c2.contains(\":\")) {",
    "            String s_host_c2 = s_line_c2.substring(0, s_line_c2.indexOf(\":\")).trim();",
    "            int s_port_c2 = Integer.parseInt(s_line_c2.substring(s_line_c2.indexOf(\":\") + 1).trim());",
    "            LiveStreamPlayer.start(s_host_c2, s_port_c2, 0, 0);",
    "            LiveSurfaceRenderer.stopAll();",
    "            if (c2_reader_Surfcae != null) { LiveSurfaceRenderer.startFor(c2_reader_Surfcae, \"reader\"); }",
    "            if (c2_reader_Surfcae_1 != null) { LiveSurfaceRenderer.startFor(c2_reader_Surfcae_1, \"reader1\"); }",
    "            if (c2_preview_Surfcae != null) { LiveSurfaceRenderer.startFor(c2_preview_Surfcae, \"preview\"); }",
    "            if (c2_preview_Surfcae_1 != null) { LiveSurfaceRenderer.startFor(c2_preview_Surfcae_1, \"preview1\"); }",
    "            XposedBridge.log(\"【VCAM】[c2-live] started reader=\" + (c2_reader_Surfcae != null) + \" reader1=\" + (c2_reader_Surfcae_1 != null) + \" preview=\" + (c2_preview_Surfcae != null) + \" preview1=\" + (c2_preview_Surfcae_1 != null));",
    "            return;",
    "        }",
    "    }",
    "} catch (Throwable c2_live_t) {",
    "    XposedBridge.log(\"【VCAM】[c2-live]\" + c2_live_t);",
    "}",
    "// === end Patch 8b ===",
]


def find_file():
    for p in CANDIDATES:
        if os.path.isfile(p):
            return p
    for root, _dirs, files in os.walk(HERE):
        if "HookMain.java" in files:
            return os.path.join(root, "HookMain.java")
    return None


def leading_ws(line):
    return line[:len(line) - len(line.lstrip())]


def main():
    path = find_file()
    if not path:
        print("ERROR: HookMain.java not found near", HERE)
        return 1
    with io.open(path, "r", encoding="utf-8") as f:
        src = f.read()
    if MARKER in src:
        print("ALREADY PATCHED:", path)
        return 0

    lines = src.split("\n")
    out = []
    patched_play = False
    patched_open = False
    for line in lines:
        out.append(line)
        if (not patched_play) and (PLAY_ANCHOR in line):
            body_indent = leading_ws(line) + "    "
            for bl in PLAY_LINES:
                out.append(body_indent + bl)
            patched_play = True
            continue
        if (not patched_open) and (OPEN_ANCHOR in line):
            out.append(leading_ws(line) + "LiveSurfaceRenderer.stopAll();")
            patched_open = True
            continue

    if not patched_play:
        print("ERROR: could not find process_camera2_play() anchor:", PLAY_ANCHOR)
        return 2

    with io.open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))

    print("PATCH OK:", path)
    print("  process_camera2_play injected:", patched_play)
    print("  onOpened stopAll injected:   ", patched_open)
    if not patched_open:
        print("  WARN: onOpened anchor not found; renderers still stop at each play call.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
