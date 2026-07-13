# Patch 8: inserts the live-stream source into HookMain.onPreviewFrame.
# Put this file anywhere inside your local VCAM repo and run:
#   python patch_hookmain.py
# (auto-finds HookMain.java), or pass an explicit path:
#   python patch_hookmain.py path\to\HookMain.java
import sys
import os

if len(sys.argv) > 1:
    path = sys.argv[1]
else:
    path = None
    for root, dirs, files in os.walk("."):
        if "HookMain.java" in files:
            path = os.path.join(root, "HookMain.java")
            break
    if path is None:
        print("HookMain.java not found - run from the repo folder or pass the path as an argument")
        sys.exit(1)

print("Target:", path)
src = open(path, encoding="utf-8").read()

if "vcam_stream.txt" in src:
    print("ALREADY PATCHED")
    sys.exit(0)

anchor = 'File c1_frames_dir = new File(video_path + "vcam_frames");'
block = [
    '// ==== VCAM live stream (MJPEG over TCP) - Patch 8 ====',
    'File vcam_stream_cfg = new File(video_path + "vcam_stream.txt");',
    'if (vcam_stream_cfg.exists()) {',
    '    try {',
    '        java.io.BufferedReader s_br = new java.io.BufferedReader(new java.io.FileReader(vcam_stream_cfg));',
    '        String s_line = s_br.readLine();',
    '        s_br.close();',
    '        if (s_line != null && s_line.trim().length() > 0) {',
    '            s_line = s_line.trim();',
    '            String s_host = "127.0.0.1";',
    '            int s_port = 8080;',
    "            int s_colon = s_line.lastIndexOf(':');",
    '            if (s_colon > 0) {',
    '                s_host = s_line.substring(0, s_colon).trim();',
    '                s_port = Integer.parseInt(s_line.substring(s_colon + 1).trim());',
    '            } else {',
    '                s_host = s_line;',
    '            }',
    '            LiveStreamPlayer.start(s_host, s_port, mwidth, mhight);',
    '            long s_wait = System.currentTimeMillis();',
    '            while (!LiveStreamPlayer.gotFirstFrame && System.currentTimeMillis() - s_wait < 3000) { }',
    '            if (LiveStreamPlayer.gotFirstFrame && data_buffer != null) {',
    '                System.arraycopy(data_buffer, 0, paramd.args[0], 0, Math.min(data_buffer.length, ((byte[]) paramd.args[0]).length));',
    '                return;',
    '            }',
    '        }',
    '    } catch (Throwable s_t) {',
    '        XposedBridge.log("【VCAM】[stream-cfg]" + s_t.toString());',
    '    }',
    '}',
    '// ==== end live stream ====',
]

lines = src.split("\n")
out = []
done = False
for line in lines:
    if (not done) and anchor in line:
        indent = line[: len(line) - len(line.lstrip())]
        for b in block:
            out.append(indent + b)
        done = True
    out.append(line)

if done:
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("PATCH OK - live stream hook inserted into HookMain.java")
else:
    print("PATTERN NOT FOUND - send me the onPreviewFrame block again")
