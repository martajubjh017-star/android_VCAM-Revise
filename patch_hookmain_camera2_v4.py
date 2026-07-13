#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Patch 12 v4: stop the destroyed-mutex crash by NEVER releasing the dummy
virtual surface (leak it) while giving each camera-open a FRESH valid surface.

Background:
- The crash is a native abort inside System WebView 70 (thread VideoCaptureCam):
  FORTIFY pthread_mutex_lock on a destroyed mutex, during the camera
  resolution switch (640x480 -> 1280x720), i.e. session #1 teardown vs
  session #2 setup.
- v3 tried "reuse one dummy surface across opens" -> crash still fired, maybe
  sooner (session #2 reused a surface whose BufferQueue session #1 teardown
  may have abandoned).
- New hypothesis: the trigger is core-VCAM RELEASING the old dummy surface /
  SurfaceTexture while Chromium's still-tearing-down capture holds it -> its
  native BufferQueue+mutex are freed under Chromium's feet -> destroyed mutex.

v4 fix (two edits):
  (1) REVERT the v3 gate in onOpened back to an unconditional
      'need_recreate = true; create_virtual_surface();' so every open builds a
      fresh, valid dummy surface (session #2 never reuses an abandoned one).
  (2) In create_virtual_surface(), REMOVE the two '.release()' blocks so the
      previous dummy Surface + SurfaceTexture are LEAKED (never freed). A few
      leaked SurfaceTextures across a handful of opens is negligible; nothing
      is freed under Chromium's capture teardown -> no destroyed mutex.

Idempotent (marker 'Patch 12 v4'). Whitespace-tolerant. Requires push +
rebuild + reinstall. Only touches HookMain.java; the v2 java files are
unchanged.
"""
import os, re, sys

MARKER = "Patch 12 v4"

# --- Edit 1: revert the Patch 11 v3 gate back to the unconditional call ---
V3_BLOCK = re.compile(
    r"// === Patch 11 v3:.*?// === end Patch 11 v3 ===",
    re.DOTALL,
)
V3_REVERT = "need_recreate = true;\n        create_virtual_surface();"

# --- Edit 2: remove the two release() blocks in create_virtual_surface() ---
# Matches:
#   if (c2_virtual_surfaceTexture != null) { c2_virtual_surfaceTexture.release(); c2_virtual_surfaceTexture = null; }
#   if (c2_virtual_surface != null) { c2_virtual_surface.release(); c2_virtual_surface = null; }
RELEASE_BLOCKS = re.compile(
    r"if\s*\(\s*c2_virtual_surfaceTexture\s*!=\s*null\s*\)\s*\{"
    r"\s*c2_virtual_surfaceTexture\s*\.\s*release\s*\(\s*\)\s*;"
    r"\s*c2_virtual_surfaceTexture\s*=\s*null\s*;\s*\}"
    r"\s*if\s*\(\s*c2_virtual_surface\s*!=\s*null\s*\)\s*\{"
    r"\s*c2_virtual_surface\s*\.\s*release\s*\(\s*\)\s*;"
    r"\s*c2_virtual_surface\s*=\s*null\s*;\s*\}"
)
RELEASE_REPLACE = (
    "// === Patch 12 v4: leak old dummy surface/texture (no release) ===\n"
    "        // Do NOT free a BufferQueue Chromium's capture teardown may still hold."
)


def find_hookmain(root):
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

    # Edit 1: revert v3 gate if present (optional).
    n_v3 = len(V3_BLOCK.findall(src))
    if n_v3 > 1:
        print("ERROR: found", n_v3, "Patch 11 v3 blocks, expected 0 or 1")
        sys.exit(3)
    if n_v3 == 1:
        src = V3_BLOCK.sub(V3_REVERT, src)
        print("reverted Patch 11 v3 gate -> unconditional create_virtual_surface()")
    else:
        print("no Patch 11 v3 gate present (ok)")

    # Edit 2: strip the two release() blocks (required).
    n_rel = len(RELEASE_BLOCKS.findall(src))
    if n_rel != 1:
        print("ERROR: expected exactly 1 release-block pair, found", n_rel)
        sys.exit(4)
    src = RELEASE_BLOCKS.sub(RELEASE_REPLACE, src)

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src)
    print("PATCH OK (Patch 12 v4: fresh dummy per open, old dummy leaked/not released)")


if __name__ == "__main__":
    main()
