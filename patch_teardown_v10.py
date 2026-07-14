#!/usr/bin/env python3
# patch_teardown_v10.py
# Patch 19 v10: hook android.media.ImageReader.close() and stop the
# LiveImageWriter feeder BEFORE Chromium natively tears down its reader.
# This is the teardown-synchronization point that v3..v9 could not observe
# (CameraCaptureSession.onClosed does NOT fire during the WebView open/close
# churn). Chromium creates the reader via Java ImageReader.newInstance, so it
# very likely also closes it via Java ImageReader.close -> our hook fires there.
# Touches ONLY HookMain.java. Additive to all prior patches. Idempotent.
import os
import sys

MARKER = "Patch 19 v10"
CANONICAL = os.path.join("app", "src", "main", "java", "com", "example", "vcam", "HookMain.java")
ANCHOR = 'XposedHelpers.findAndHookMethod("android.media.ImageReader", lpparam.classLoader, "newInstance", int.class, int.class, int.class, int.class, new XC_MethodHook() {'


def norm(s):
    return " ".join(s.split())


def locate():
    if os.path.isfile(CANONICAL):
        return CANONICAL
    for root, _dirs, files in os.walk("."):
        if "HookMain.java" in files:
            return os.path.join(root, "HookMain.java")
    return None


def build_block(indent):
    body = [
        "// === " + MARKER + ": stop feeder before Chromium tears down its reader ===",
        "try {",
        '    XposedHelpers.findAndHookMethod("android.media.ImageReader", lpparam.classLoader, "close", new XC_MethodHook() {',
        "        @Override",
        "        protected void beforeHookedMethod(MethodHookParam param) {",
        "            try {",
        "                LiveImageWriter.sessionLive = false;",
        "                LiveImageWriter.stopAll();",
        '                XposedBridge.log("【VCAM】[c2-teardown] ImageReader.close -> feeder stopped");',
        "                Thread.sleep(60);",
        '            } catch (Throwable c2_td_in) { XposedBridge.log("【VCAM】[c2-teardown] " + c2_td_in); }',
        "        }",
        "    });",
        '} catch (Throwable c2_td_h) { XposedBridge.log("【VCAM】[c2-teardown] hook failed " + c2_td_h); }',
    ]
    return [indent + ln for ln in body]


def main():
    path = locate()
    if path is None:
        print("MISS: HookMain.java not found")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    if MARKER in src:
        print("ALREADY PATCHED (" + MARKER + ")")
        sys.exit(0)
    lines = src.split("\n")
    target = norm(ANCHOR)
    hits = [i for i, ln in enumerate(lines) if norm(ln) == target]
    if len(hits) == 0:
        print("MISS: ImageReader.newInstance hook anchor not found")
        sys.exit(1)
    if len(hits) > 1:
        print("MISS: anchor found %d times (expected 1)" % len(hits))
        sys.exit(1)
    if "LiveImageWriter.stopAll" not in src:
        print("MISS: LiveImageWriter.stopAll not referenced (unexpected file state)")
        sys.exit(1)
    idx = hits[0]
    raw = lines[idx]
    indent = raw[:len(raw) - len(raw.lstrip())]
    block = build_block(indent)
    new_lines = lines[:idx] + block + lines[idx:]
    new_src = "\n".join(new_lines)
    for op, cl, name in (("{", "}", "brace"), ("(", ")", "paren")):
        if (src.count(op) - src.count(cl)) != (new_src.count(op) - new_src.count(cl)):
            print("ABORT: " + name + " balance delta changed")
            sys.exit(1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_src)
    print("patched: " + path)
    print("PATCH OK (" + MARKER + ")")
    sys.exit(0)


if __name__ == "__main__":
    main()
