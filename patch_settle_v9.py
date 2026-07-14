#!/usr/bin/env python3
# patch_settle_v9.py
# Patch 18 v9: settle-window debounce for LiveImageWriter (Camera2 / TikTok feeder).
#
# Root cause (confirmed 2026-07-14): the destroyed-mutex crash is NOT the
# resolution switch -- it is feeding YUV frames into Chromium's ImageReader
# while WebView churns the camera open/closed at startup (multiple ...
# open-camera / rebuild-dummy per launch). onClosed does not fire during that
# churn, so a session-live gate cannot catch it.
#
# Fix: a feeder only pushes frames when (a) it is the NEWEST feeder and
# (b) no new feeder has been born for SETTLE_MS (churn has gone quiet).
# During churn each new session spawns a new feeder -> lastBirthMs keeps
# advancing -> every feeder idles -> nothing is pushed into any tearing-down
# reader -> no crash. Once churn settles, the newest feeder feeds -> picture.
#
# Fully self-contained inside LiveImageWriter (no HookMain edits needed).
# Whitespace-normalized matching, idempotent, writes ONLY if every required
# anchor is found (no partial writes).

import os, sys

MARKER = "Patch 18 v9"
SETTLE_MS = 800


def find_java(root):
    cand = os.path.join(root, "app", "src", "main", "java", "com", "example", "vcam", "LiveImageWriter.java")
    if os.path.isfile(cand):
        return cand
    for dp, dn, fn in os.walk(root):
        if "LiveImageWriter.java" in fn:
            return os.path.join(dp, "LiveImageWriter.java")
    return None


def norm(s):
    return " ".join(s.split())


def indent_of(line):
    n = 0
    for ch in line:
        if ch == " ":
            n += 1
        elif ch == "\t":
            n += 4
        else:
            break
    return n


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    path = find_java(root)
    if not path:
        print("[LiveImageWriter.java] NOT FOUND under", os.path.abspath(root))
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    if MARKER in src:
        print("ALREADY PATCHED (%s)" % MARKER)
        sys.exit(0)

    lines = src.split("\n")

    rot_idx = None
    while_idx = None
    for i, ln in enumerate(lines):
        n = norm(ln)
        if rot_idx is None and "ROT_DEG = 90" in n:
            rot_idx = i
        if while_idx is None and n == "while (running) {":
            while_idx = i

    missing = []
    if rot_idx is None:
        missing.append("ROT_DEG = 90 (field anchor)")
    if while_idx is None:
        missing.append("while (running) { (loop anchor)")
    if missing:
        print("[LiveImageWriter.java] MISS:", "; ".join(missing))
        print("RESULT: FAILED (no write)")
        sys.exit(1)

    field_ind = " " * indent_of(lines[rot_idx])
    field_block = [
        field_ind + "// " + MARKER + ": settle-window debounce (skip feeding during Chromium open/close churn)",
        field_ind + "private static final long SETTLE_MS = %d;" % SETTLE_MS,
        field_ind + "private static volatile long latestGen = 0;",
        field_ind + "private static volatile long lastBirthMs = 0;",
        field_ind + "private final long myGen = nextGen();",
        field_ind + "private static synchronized long nextGen() { lastBirthMs = System.currentTimeMillis(); return ++latestGen; }",
    ]

    gate_ind = " " * (indent_of(lines[while_idx]) + 4)
    gate_block = [
        gate_ind + "// " + MARKER + ": only the newest feeder pushes, and only after churn settles",
        gate_ind + "if (myGen != latestGen || System.currentTimeMillis() - lastBirthMs < SETTLE_MS) {",
        gate_ind + "    try { Thread.sleep(20); } catch (InterruptedException __ie) { }",
        gate_ind + "    continue;",
        gate_ind + "}",
    ]

    out = list(lines)
    # insert gate first (after while_idx, which is > rot_idx so rot_idx stays valid)
    out[while_idx + 1:while_idx + 1] = gate_block
    out[rot_idx + 1:rot_idx + 1] = field_block
    new_src = "\n".join(out)

    def bal(s, o, c):
        return s.count(o) - s.count(c)

    if bal(new_src, "{", "}") != bal(src, "{", "}"):
        print("[LiveImageWriter.java] ABORT: brace imbalance introduced")
        sys.exit(1)
    if bal(new_src, "(", ")") != bal(src, "(", ")"):
        print("[LiveImageWriter.java] ABORT: paren imbalance introduced")
        sys.exit(1)

    with open(path, "w", encoding="utf-8") as f:
        f.write(new_src)
    print("patched:", path)
    print("PATCH OK (%s)" % MARKER)


if __name__ == "__main__":
    main()
