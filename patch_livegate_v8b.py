#!/usr/bin/env python3
# Patch 17 v8 (v8b): gate the LiveImageWriter feeder to the capture-session
# lifecycle. LiveImageWriter.java ONLY (HookMain.java already patched by v8).
#
# Edits (all anchors confirmed present in the real file):
#   E1 field:  after the ROT_DEG declaration -> add
#              'public static volatile boolean sessionLive = false;'
#   E2 gate:   right after 'while (running) {' -> idle unless session is live
#              and the surface is valid.
#   E3 guard:  'if (ok && running) {' -> 'if (ok && running && sessionLive) {'
#   E4 buffer: ensure ImageWriter.newInstance(surface, N) has N == 2
#              (idempotent: skip if already 2; fix if 1).
#
# Matching is WHITESPACE-NORMALIZED (indentation-independent) to avoid the
# silent anchor misses that killed v7. Each edit is idempotent: an
# already-applied edit is reported ALREADY and skipped, never a failure.
# The file is only rewritten if every REQUIRED anchor was found; on any
# missing required anchor nothing is written (no partial writes).

import os, re, sys

MARKER = "Patch 17 v8"
FNAME = "LiveImageWriter.java"


def find_target():
    cands = [
        os.path.join("app", "src", "main", "java", "com", "example", "vcam", FNAME),
        FNAME,
    ]
    for c in cands:
        if os.path.isfile(c):
            return c
    for root, _dirs, files in os.walk("."):
        if FNAME in files:
            return os.path.join(root, FNAME)
    return None


def norm(s):
    # collapse ALL runs of whitespace to single spaces, strip ends
    return " ".join(s.split())


def indent_of(line):
    m = re.match(r"[ \t]*", line)
    return m.group(0) if m else ""


def imbalance(text):
    return (text.count("{") - text.count("}"), text.count("(") - text.count(")"))


def main():
    path = find_target()
    if not path:
        print("[%s] NOT FOUND (run from repo root)" % FNAME)
        print("RESULT: FAILED")
        sys.exit(1)
    print("[%s] target: %s" % (FNAME, path))

    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    lines = src.split("\n")
    before_imb = imbalance(src)

    out = []
    done = {"field": False, "gate": False, "guard": False, "buffer": False}
    already = {"field": False, "gate": False, "guard": False, "buffer": False}

    # detect pre-existing state
    already["field"] = any("sessionLive" in l and "boolean" in l for l in lines)

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        nl = norm(line)

        # E4 buffer: ImageWriter.newInstance(surface, N)
        if (not done["buffer"] and not already["buffer"]
                and "ImageWriter.newInstance(surface," in nl):
            m = re.search(r"newInstance\(surface,\s*(\d+)\)", line)
            if m:
                if m.group(1) == "2":
                    already["buffer"] = True
                else:
                    line = re.sub(r"(newInstance\(surface,\s*)\d+(\))",
                                  r"\g<1>2\g<2>", line)
                    done["buffer"] = True

        # E3 guard: if (ok && running) {  ->  add && sessionLive
        if not done["guard"] and not already["guard"]:
            if nl.startswith("if (ok && running && sessionLive)"):
                already["guard"] = True
            elif nl == "if (ok && running) {":
                ind = indent_of(line)
                line = ind + "if (ok && running && sessionLive) { // " + MARKER
                done["guard"] = True

        out.append(line)

        # E1 field: insert after the ROT_DEG declaration line
        if (not done["field"] and not already["field"]
                and "private static final int ROT_DEG" in nl):
            ind = indent_of(line)
            out.append(ind + "public static volatile boolean sessionLive = false; // " + MARKER)
            done["field"] = True

        # E2 gate: insert idle-gate right after 'while (running) {'
        if not done["gate"] and not already["gate"] and nl == "while (running) {":
            # look ahead: is the gate already the next non-empty line?
            nxt = ""
            for j in range(i + 1, min(i + 4, n)):
                if lines[j].strip():
                    nxt = norm(lines[j])
                    break
            if "sessionLive" in nxt and "continue" in nxt:
                already["gate"] = True
            else:
                ind = indent_of(line) + "    "
                out.append(ind + "if (!sessionLive || surface == null || !surface.isValid()) { "
                           "try { Thread.sleep(15); } catch (InterruptedException ie) { } "
                           "continue; } // " + MARKER)
                done["gate"] = True

        i += 1

    # required anchors
    required = ["field", "gate", "guard"]
    missing = [k for k in required if not done[k] and not already[k]]
    if missing:
        for k in missing:
            print("[%s] MISS: %s anchor not found" % (FNAME, k))
        print("RESULT: FAILED (no partial writes)")
        sys.exit(1)

    new_src = "\n".join(out)
    after_imb = imbalance(new_src)
    if after_imb != before_imb:
        print("[%s] ABORT: brace/paren imbalance changed %s -> %s"
              % (FNAME, before_imb, after_imb))
        print("RESULT: FAILED (no partial writes)")
        sys.exit(1)

    applied = [k for k in done if done[k]]
    skipped = [k for k in already if already[k]]

    if not applied:
        print("[%s] ALREADY PATCHED (%s)" % (FNAME, ", ".join(sorted(skipped)) or "all"))
        print("PATCH OK (%s)" % MARKER)
        return

    with open(path, "w", encoding="utf-8") as f:
        f.write(new_src)
    print("[%s] applied: %s%s (imbalance stable %s)"
          % (FNAME, ", ".join(sorted(applied)),
             ("; already: " + ", ".join(sorted(skipped))) if skipped else "",
             before_imb))
    print("PATCH OK (%s)" % MARKER)


if __name__ == "__main__":
    main()
