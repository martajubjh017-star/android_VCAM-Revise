#!/usr/bin/env python3
# Patch 14 v5 for VCAM-Revise -- LiveImageWriter.java only.
#
# GOAL: reduce the Chromium/WebView "destroyed mutex" SIGABRT on TikTok's in-app
# camera, and fix the stretched / width<->height-swapped picture.
#
# CHANGES:
#  1) ImageWriter buffer count 2 -> 1 (minimal in-flight frames = smallest window
#     in which a queued buffer can be consumed by a capture device being torn down).
#  2) dequeueInputImage failure = reader surface abandoned (session teardown /
#     resolution switch) -> STOP this feeder instead of retrying, so we never push
#     into a surface whose native capture device is being destroyed.
#  3) queueInputImage wrapped in stop-on-abandon try/catch, and we only queue while
#     still running.
#  4) centerCrop auto-rotates 90 deg when the source (portrait) and the reader
#     buffer (landscape) have opposite orientation -> fixes the swapped/stretched
#     picture. Flip ROT_DEG 90 -> 270 if the result is upside-down.
#
# Idempotent (marker "Patch 14 v5"). Whitespace-tolerant. Each edit must hit exactly
# once or the script aborts WITHOUT writing. Run from repo root or pass the path.

import os, re, sys, glob

MARKER = "Patch 14 v5"

def find_target():
    if len(sys.argv) > 1:
        return sys.argv[1]
    pref = os.path.join("app", "src", "main", "java", "com", "example", "vcam", "LiveImageWriter.java")
    if os.path.isfile(pref):
        return pref
    hits = glob.glob(os.path.join("**", "LiveImageWriter.java"), recursive=True)
    if not hits:
        print("ERROR: LiveImageWriter.java not found. Run from repo root or pass its path.")
        sys.exit(1)
    hits.sort(key=lambda p: (0 if "com/example/vcam" in p.replace("\\", "/") else 1, len(p)))
    return hits[0]

def sub_once(pattern, repl_fn, text, label, flags=0):
    new, n = re.subn(pattern, repl_fn, text, flags=flags)
    if n != 1:
        print("ERROR: edit '%s' matched %d times (expected exactly 1). Aborting, no changes written." % (label, n))
        sys.exit(2)
    return new

def main():
    path = find_target()
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    if MARKER in src:
        print("ALREADY PATCHED (%s): %s" % (MARKER, path))
        return

    txt = src

    # --- Edit 1: ImageWriter buffer count 2 -> 1 ---
    txt = sub_once(r"ImageWriter\.newInstance\(\s*surface\s*,\s*2\s*\)",
                   lambda m: "ImageWriter.newInstance(surface, 1)",
                   txt, "buffer-count")

    # --- Edit 2: dequeue catch -> stop-on-abandon ---
    dq_pat = r"catch\s*\(\s*Throwable\s+dq\s*\)\s*\{\s*//[^\n]*\n\s*Thread\.sleep\(\s*10\s*\)\s*;\s*continue\s*;\s*\}"
    dq_repl = ("catch (Throwable dq) {\n"
               "                    // " + MARKER + ": reader surface abandoned (session teardown /\n"
               "                    // resolution switch) -> stop feeding a dying surface; never hand a\n"
               "                    // buffer to a capture device that is being destroyed.\n"
               "                    XposedBridge.log(\"\u3010VCAM\u3011[c2-iw:\" + tag + \"] dequeue abandoned -> stop \" + dq);\n"
               "                    running = false;\n"
               "                    break;\n"
               "                }")
    txt = sub_once(dq_pat, lambda m: dq_repl, txt, "dequeue-stop-on-abandon", flags=re.DOTALL)

    # --- Edit 3a: only queue while still running ---
    txt = sub_once(r"if\s*\(\s*ok\s*\)\s*\{", lambda m: "if (ok && running) {", txt, "queue-guard-running")

    # --- Edit 3b: wrap queueInputImage in stop-on-abandon try/catch ---
    q_pat = r"writer\.queueInputImage\(\s*img\s*\)\s*;[^\n]*"
    q_repl = ("try { writer.queueInputImage(img); } "
              "catch (Throwable qq) { "
              "XposedBridge.log(\"\u3010VCAM\u3011[c2-iw:\" + tag + \"] queue abandoned -> stop \" + qq); "
              "try { img.close(); } catch (Throwable ig) { } "
              "running = false; break; } // " + MARKER)
    txt = sub_once(q_pat, lambda m: q_repl, txt, "queue-stop-on-abandon")

    # --- Edit 4a: declare ROT_DEG constant next to the scratch field ---
    txt = sub_once(r"(private\s+Bitmap\s+scratch\s*;[^\n]*)",
                   lambda m: m.group(1) + "\n    private static final int ROT_DEG = 90; // " + MARKER + ": reader-buffer rotation compensation (flip to 270 if upside-down)",
                   txt, "rot-deg-field")

    # --- Edit 4b: rotation-aware center-crop matrix ---
    cc_pat = (r"float\s+scale\s*=\s*Math\.max\(\s*w\s*/\s*sw\s*,\s*h\s*/\s*sh\s*\)\s*;\s*"
              r"float\s+dx\s*=\s*\(\s*w\s*-\s*sw\s*\*\s*scale\s*\)\s*/\s*2f\s*;\s*"
              r"float\s+dy\s*=\s*\(\s*h\s*-\s*sh\s*\*\s*scale\s*\)\s*/\s*2f\s*;\s*"
              r"Matrix\s+m\s*=\s*new\s+Matrix\(\)\s*;\s*"
              r"m\.postScale\(\s*scale\s*,\s*scale\s*\)\s*;\s*"
              r"m\.postTranslate\(\s*dx\s*,\s*dy\s*\)\s*;")
    cc_repl = ("// " + MARKER + ": the app rotates the camera buffer by the sensor\n"
               "        // orientation before display; storing our upright frame without matching\n"
               "        // that makes the picture come out sideways/stretched with W & H swapped.\n"
               "        // Rotate 90 deg when source & reader buffer have opposite orientation.\n"
               "        boolean srcPortrait = sh > sw;\n"
               "        boolean dstPortrait = h > w;\n"
               "        int rot = (srcPortrait != dstPortrait) ? ROT_DEG : 0;\n"
               "        float ew = (rot == 90 || rot == 270) ? sh : sw;\n"
               "        float eh = (rot == 90 || rot == 270) ? sw : sh;\n"
               "        float scale = Math.max(w / ew, h / eh);\n"
               "        Matrix m = new Matrix();\n"
               "        m.postTranslate(-sw / 2f, -sh / 2f);\n"
               "        if (rot != 0) m.postRotate(rot);\n"
               "        m.postScale(scale, scale);\n"
               "        m.postTranslate(w / 2f, h / 2f);")
    txt = sub_once(cc_pat, lambda m: cc_repl, txt, "centercrop-rotation", flags=re.DOTALL)

    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)
    print("PATCH OK (%s): %s" % (MARKER, path))

if __name__ == "__main__":
    main()
