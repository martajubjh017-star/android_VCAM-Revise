#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Patch 11 v3: stop virtual-surface churn on Camera2 resolution switch.

Problem: TikTok / System WebView (Chromium 70) camera opens the device TWICE
during a 640x480 -> 1280x720 resolution switch. On EACH CameraDevice.onOpened,
core-VCAM did:
        need_recreate = true;
        create_virtual_surface();     // <-- releases old c2_virtual_surface
                                      //     + its SurfaceTexture(15), builds new
Releasing the dummy sink Surface while Chromium's still-tearing-down native
capture (thread VideoCaptureCam) still references its BufferQueue frees the
native mutex -> Chromium locks a destroyed mutex -> SIGABRT.

Fix (surgical, core code): create the virtual surface ONCE and reuse it across
reopens. Never release/recreate it on the second onOpened. This removes the
churn that races with Chromium's capture teardown.

Only the onOpened call site is changed:
        need_recreate = true;
        create_virtual_surface();
-> guarded so it only runs when c2_virtual_surface == null.

The recursive create_virtual_surface() method itself is NOT touched (its own
'need_recreate = true;' line is preceded by 'c2_virtual_surface =', so the
regex below can't match it).

Idempotent (marker 'Patch 11 v3'). Whitespace-tolerant. Requires push +
rebuild + reinstall.
"""
import os, re, sys

MARKER = "Patch 11 v3"

# Match ONLY the onOpened call site:
#   need_recreate = true; <ws> create_virtual_surface();
# In create_virtual_surface() the call is 'c2_virtual_surface = create_virtual_surface();'
# so the bare 'create_virtual_surface();' right after 'need_recreate = true;' is unique.
PAT = re.compile(
    r"need_recreate\s*=\s*true\s*;\s*create_virtual_surface\s*\(\s*\)\s*;"
)

REPLACEMENT = (
    "// === Patch 11 v3: stable virtual surface (no churn on resolution switch) ===\n"
    "        if (c2_virtual_surface == null) { need_recreate = true; create_virtual_surface(); }\n"
    "        // === end Patch 11 v3 ==="
)


def find_hookmain(root):
    # Prefer the canonical path.
    cand = os.path.join(
        root, "app", "src", "main", "java", "com", "example", "vcam", "HookMain.java"
    )
    if os.path.isfile(cand):
        return cand
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f == "HookMain.java" and dirpath.replace("\\", "/").endswith(
                "com/example/vcam"
            ):
                return os.path.join(dirpath, f)
    # Loose fallback
    for dirpath, _dirs, files in os.walk(root):
        if "HookMain.java" in files:
            return os.path.join(dirpath, "HookMain.java")
    return None


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    path = find_hookmain(root)
    if not path:
        print("ERROR: HookMain.java not found under", root)
        sys.exit(2)
    print("Found:", path)
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read()

    if MARKER in src:
        print("ALREADY PATCHED (" + MARKER + ")")
        return

    matches = list(PAT.finditer(src))
    if len(matches) == 0:
        print("ERROR: onOpened call site not found")
        print("       (expected 'need_recreate = true; create_virtual_surface();')")
        sys.exit(3)
    if len(matches) > 1:
        print("ERROR: expected exactly 1 match, found", len(matches))
        print("       aborting to avoid touching create_virtual_surface() itself")
        sys.exit(4)

    new_src = src[: matches[0].start()] + REPLACEMENT + src[matches[0].end() :]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new_src)
    print("PATCH OK (Patch 11 v3: virtual surface reused across reopens)")


if __name__ == "__main__":
    main()
