#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Patch 15 v6 -- advertise a SINGLE capture resolution to suppress the
640x480 -> 1280x720 reconfigure that makes WebView (Chromium 70) destroy its
capture mutex and SIGABRT.

Strategy (NEW class of fix, not the ImageWriter feeder like v3-v5):
Hook android.hardware.camera2.params.StreamConfigurationMap.getOutputSizes(int)
and filter the returned Size[] down to ONE size (prefer 1280x720, else the
largest). Chromium picks the capture resolution from this list -> if only one
size is offered it opens at that size once and (ideally) never reconfigures.

Touches ONLY HookMain.java. Idempotent (marker 'Patch 15 v6'). Inserts the hook
immediately before the existing ImageReader.newInstance hook so it lives in the
same per-package handleLoadPackage scope (uses the local `lpparam`).

Usage: python patch_hookmain_resolution_v6.py [path/to/HookMain.java]
"""
import sys, os, re, glob

MARKER = "Patch 15 v6"

BLOCK = r'''// === Patch 15 v6: advertise single capture resolution (suppress 640x480 -> 1280x720 switch) ===
            try {
                XposedHelpers.findAndHookMethod("android.hardware.camera2.params.StreamConfigurationMap", lpparam.classLoader, "getOutputSizes", int.class, new XC_MethodHook() {
                    @Override
                    protected void afterHookedMethod(MethodHookParam vres) {
                        try {
                            android.util.Size[] arr = (android.util.Size[]) vres.getResult();
                            if (arr == null || arr.length <= 1) return;
                            android.util.Size pick = null;
                            for (android.util.Size s : arr) { if (s.getWidth() == 1280 && s.getHeight() == 720) { pick = s; break; } }
                            if (pick == null) { for (android.util.Size s : arr) { if (pick == null || (long) s.getWidth() * s.getHeight() > (long) pick.getWidth() * pick.getHeight()) pick = s; } }
                            if (pick != null) { vres.setResult(new android.util.Size[]{ pick }); XposedBridge.log("【VCAM】[c2-res] getOutputSizes(fmt=" + vres.args[0] + ") " + arr.length + " sizes -> pinned " + pick.getWidth() + "x" + pick.getHeight()); }
                        } catch (Throwable vt) { XposedBridge.log("【VCAM】[c2-res] " + vt); }
                    }
                });
            } catch (Throwable vh) { XposedBridge.log("【VCAM】[c2-res] hook failed " + vh); }
            '''

def find_file(argv):
    if len(argv) > 1 and os.path.isfile(argv[1]):
        return argv[1]
    pref = "app/src/main/java/com/example/vcam/HookMain.java"
    if os.path.isfile(pref):
        return pref
    hits = glob.glob("**/HookMain.java", recursive=True)
    hits.sort(key=lambda p: (0 if "com/example/vcam" in p.replace("\\", "/") else 1, len(p)))
    return hits[0] if hits else None

def main():
    path = find_file(sys.argv)
    if not path:
        print("ERROR: HookMain.java not found", file=sys.stderr); sys.exit(2)
    src = open(path, encoding="utf-8").read()
    if MARKER in src:
        print("ALREADY PATCHED (%s): %s" % (MARKER, path)); return

    # Insert BLOCK immediately before the ImageReader.newInstance hook.
    pat = re.compile(r'(?P<lead>\n(?P<ind>[ \t]*))(?P<call>XposedHelpers\.findAndHookMethod\(\s*"android\.media\.ImageReader")')
    def repl(m):
        ind = m.group('ind')
        block_indented = BLOCK.replace("\n", "\n" + ind).rstrip() + "\n" + ind
        return m.group('lead') + block_indented + m.group('call')
    new, n = pat.subn(repl, src, count=1)
    if n != 1:
        print("ERROR: anchor (ImageReader.newInstance hook) matched %d times, expected 1" % n, file=sys.stderr); sys.exit(3)
    open(path, "w", encoding="utf-8").write(new)
    print("PATCH OK (%s): %s" % (MARKER, path))

if __name__ == "__main__":
    main()
