#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# patch_livegate_v8.py
#
# Patch 17 v8 -- synchronize the Camera2 feeder to the capture-session
# lifecycle so we ONLY push frames while a session is genuinely live.
#
# Root cause (confirmed from the LSPosed log + source):
#   The feeder (LiveImageWriter) runs on its own thread and blindly pushes into
#   the app's ImageReader surface. TikTok's Chromium/WebView capture reopens the
#   camera several times on startup. If our frame lands while Chromium is tearing
#   the session down, its native VideoCaptureCam thread locks an already-destroyed
#   mutex => SIGABRT (crash). If instead the reader is abandoned first, the v5
#   feeder does `running=false; break` and dies with pushed=0 => black picture.
#   Same code, different timing each launch => nondeterministic crash / black.
#
# Fix: bind pushing to the session state that HookMain already observes but only
# logs today (onConfigured / onClosed / onOpened):
#   - onConfigured  -> LiveImageWriter.sessionLive = true   (safe to feed)
#   - onClosed      -> sessionLive = false + stopAll()       (stop BEFORE teardown)
#   - onOpened      -> sessionLive = false                   (stop at (re)open)
#   - feeder pushes only while (sessionLive && surface.isValid())
#   - buffer 1 -> 2 (headroom); abandon => retry (do NOT kill the thread)
#
# Touches: LiveImageWriter.java (feeder) + HookMain.java (gate set/clear).
# Idempotent. ASCII-only inserts. Line-based, indentation-independent matching
# (fixes the v7 silent anchor miss). Auto-locates both files.

import io
import os
import sys

MARK = "Patch 17 v8"


def indent(line):
    return line[:len(line) - len(line.lstrip())]


def find(lines, pred, start=0):
    for k in range(start, len(lines)):
        if pred(lines[k]):
            return k
    return -1


def locate(name):
    canonical = os.path.join("app", "src", "main", "java", "com", "example", "vcam", name)
    if os.path.isfile(canonical):
        return canonical
    if os.path.isfile(name):
        return name
    for root, _d, files in os.walk("."):
        if name in files:
            return os.path.join(root, name)
    return None


def patch_lwi(text):
    if MARK in text:
        return text, ["ALREADY PATCHED"]
    lines = text.split("\n")
    log = []

    i = find(lines, lambda l: l.strip().startswith("private static final int ROT_DEG"))
    if i < 0:
        return None, ["MISS: ROT_DEG field anchor"]
    lines.insert(i + 1, indent(lines[i]) + "public static volatile boolean sessionLive = false; // " + MARK)
    log.append("field sessionLive")

    i = find(lines, lambda l: "ImageWriter.newInstance(surface, 1)" in l)
    if i < 0:
        return None, ["MISS: newInstance(surface, 1)"]
    lines[i] = lines[i].replace("ImageWriter.newInstance(surface, 1)", "ImageWriter.newInstance(surface, 2)")
    log.append("buffer 1->2")

    i = find(lines, lambda l: l.strip() == "while (running) {")
    if i < 0:
        return None, ["MISS: while (running) {"]
    lines.insert(i + 1, indent(lines[i]) + "if (!sessionLive || !surface.isValid()) { try { Thread.sleep(15); } catch (InterruptedException ie) { } continue; } // " + MARK)
    log.append("loop gate")

    d = find(lines, lambda l: "dequeue abandoned" in l)
    if d < 0:
        return None, ["MISS: dequeue abandoned log"]
    j = find(lines, lambda l: l.strip() == "running = false;", d + 1)
    if j < 0 or j > d + 5 or lines[j + 1].strip() != "break;":
        return None, ["MISS: dequeue running=false/break"]
    lines[j:j + 2] = [indent(lines[j]) + "try { Thread.sleep(80); } catch (InterruptedException ie) { } continue; // " + MARK]
    log.append("dequeue retry")

    # NB: 'dequeue abandoned' contains the substring 'queue abandoned', so
    # exclude the dequeue line explicitly when locating the queue line.
    i = find(lines, lambda l: "queue abandoned" in l and "dequeue" not in l)
    if i < 0 or "running = false; break;" not in lines[i]:
        return None, ["MISS: queue running=false; break;"]
    lines[i] = lines[i].replace("running = false; break;", "try { Thread.sleep(80); } catch (InterruptedException ie) { } continue;")
    log.append("queue retry")

    i = find(lines, lambda l: l.strip() == "if (ok && running) {")
    if i < 0:
        return None, ["MISS: if (ok && running) {"]
    lines[i] = lines[i].replace("if (ok && running)", "if (ok && running && sessionLive)")
    log.append("queue guard gate")

    return "\n".join(lines), log


def patch_hook(text):
    if MARK in text:
        return text, ["ALREADY PATCHED"]
    lines = text.split("\n")
    log = []

    i = find(lines, lambda l: "XposedBridge.log" in l and "onConfigured" in l)
    if i < 0:
        return None, ["MISS: onConfigured log"]
    lines.insert(i + 1, indent(lines[i]) + "LiveImageWriter.sessionLive = true; // " + MARK)
    log.append("onConfigured -> live")

    i = find(lines, lambda l: "XposedBridge.log" in l and "onClosed" in l)
    if i < 0:
        return None, ["MISS: onClosed log"]
    lines.insert(i + 1, indent(lines[i]) + "LiveImageWriter.sessionLive = false; LiveImageWriter.stopAll(); // " + MARK)
    log.append("onClosed -> stop")

    idx = -1
    for k in range(len(lines) - 1):
        if lines[k].strip() == "need_recreate = true;" and lines[k + 1].strip() == "create_virtual_surface();":
            idx = k
            break
    if idx < 0:
        return None, ["MISS: onOpened need_recreate/create_virtual_surface"]
    lines.insert(idx, indent(lines[idx]) + "LiveImageWriter.sessionLive = false; // " + MARK)
    log.append("onOpened -> stop")

    return "\n".join(lines), log


def imbalance(s):
    return (s.count("{") - s.count("}"), s.count("(") - s.count(")"))


def process(name, fn):
    path = locate(name)
    if path is None:
        print("NOT FOUND: " + name + " under " + os.getcwd())
        return None
    with io.open(path, "r", encoding="utf-8") as f:
        src = f.read()
    im0 = imbalance(src)
    out, log = fn(src)
    print("[" + name + "] target: " + path)
    print("[" + name + "] " + ", ".join(log))
    if out is None:
        return False
    if out == src:
        return True  # already patched
    im1 = imbalance(out)
    if im1 != im0:
        print("[" + name + "] ABORT: brace/paren imbalance changed " + str(im0) + " -> " + str(im1))
        return False
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(out)
    print("[" + name + "] written (imbalance stable " + str(im1) + ")")
    return True


def main():
    r1 = process("LiveImageWriter.java", patch_lwi)
    r2 = process("HookMain.java", patch_hook)
    if r1 is None or r2 is None:
        return 4
    if r1 is False or r2 is False:
        print("RESULT: FAILED (no partial writes for the failing file)")
        return 2
    print("PATCH OK (" + MARK + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
