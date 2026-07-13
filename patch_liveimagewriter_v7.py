#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# patch_liveimagewriter_v7.py
#
# Patch 16 v7 -- revert the Patch 14 v5 feeder regression.
#
# WHY:
#   v6 (getOutputSizes pin) already removed the 640x480->1280x720 switch, so
#   the resolution-change teardown that caused the SIGABRT is gone. That means
#   the defensive "stop feeding on the first abandoned surface" logic v5 added
#   is no longer needed -- and it is actively harmful. During normal Chromium /
#   WebView startup (TikTok) the Camera2 session is (re)opened several times
#   ("open camera C2" / "recreate dummy" repeat in the log). v5's buffer=1 +
#   stop-on-abandon made EVERY feeder thread die with pushed=0 before it could
#   push a single frame => black picture. Telegram (Camera1 path) is unaffected,
#   which is why TG shows a picture and TT does not.
#
# WHAT (restores the proven Patch 8b v2 behavior, keeps Patch 13 color + v5
# rotation/aspect crop):
#   1) ImageWriter buffer 1 -> 2  (feeder headroom)
#   2) dequeue error: retry (sleep+continue) instead of running=false; break
#   3) queue error:   retry (sleep+continue) instead of running=false; break
#   stopAll() (called on every camera reopen) still ends the thread cleanly, so
#   a genuinely dead surface is replaced by a fresh feeder rather than spun on.
#
# Idempotent: safe to run twice. Auto-locates LiveImageWriter.java (run it from
# the repo root OR the vcam source dir). ASCII-only anchors (never matches the
# non-ASCII log tag), so it is robust regardless of how the log chars encode.

import io
import os
import sys

TARGET = "LiveImageWriter.java"
MARKER = "Patch 16 v7"

# --- (1) buffer 1 -> 2 ---------------------------------------------------
OLD_BUFFER = "ImageWriter.newInstance(surface, 1);"
NEW_BUFFER = "ImageWriter.newInstance(surface, 2); // Patch 16 v7: buffer 1->2 (feeder headroom, reverts v5)"

# --- (2) dequeue: stop -> retry -----------------------------------------
# Anchor starts AFTER the log tag (at "] dequeue") so no non-ASCII is matched.
OLD_DEQUEUE = (
    "] dequeue abandoned -> stop \" + dq);\n"
    "                    running = false;\n"
    "                    break;"
)
NEW_DEQUEUE = (
    "] dequeue retry \" + dq); // Patch 16 v7\n"
    "                    try { Thread.sleep(80); } catch (InterruptedException ie) { }\n"
    "                    continue;"
)

# --- (3) queue: stop -> retry (single line) -----------------------------
OLD_QUEUE = (
    "] queue abandoned -> stop \" + qq); try { img.close(); } catch (Throwable ig) { } running = false; break; } // Patch 14 v5"
)
NEW_QUEUE = (
    "] queue retry \" + qq); try { img.close(); } catch (Throwable ig) { } try { Thread.sleep(80); } catch (InterruptedException ie) { } continue; } // Patch 16 v7"
)


def find_target():
    canonical = os.path.join("app", "src", "main", "java", "com", "example", "vcam", TARGET)
    if os.path.isfile(canonical):
        return canonical
    if os.path.isfile(TARGET):
        return TARGET
    for root, _dirs, files in os.walk("."):
        if TARGET in files:
            return os.path.join(root, TARGET)
    return None


def main():
    path = find_target()
    if path is None:
        print("NOT FOUND: could not locate " + TARGET + " under " + os.getcwd())
        print("Run this from the repo root (C:\\Users\\Artur\\android_VCAM-Revise) or the vcam source folder.")
        return 4
    print("target: " + path)

    with io.open(path, "r", encoding="utf-8") as f:
        s = f.read()

    if MARKER in s:
        print("ALREADY PATCHED (" + MARKER + ")")
        return 0

    missing = []
    if OLD_BUFFER not in s:
        missing.append("buffer newInstance(surface, 1)")
    if OLD_DEQUEUE not in s:
        missing.append("dequeue stop-on-abandon block")
    if OLD_QUEUE not in s:
        missing.append("queue stop-on-abandon line")
    if missing:
        print("ANCHOR MISS: " + "; ".join(missing))
        return 2

    orig = s
    s = s.replace(OLD_BUFFER, NEW_BUFFER, 1)
    s = s.replace(OLD_DEQUEUE, NEW_DEQUEUE, 1)
    s = s.replace(OLD_QUEUE, NEW_QUEUE, 1)

    if s == orig:
        print("NO-OP (nothing replaced)")
        return 3

    with io.open(path, "w", encoding="utf-8") as f:
        f.write(s)
    print("PATCH OK (" + MARKER + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
