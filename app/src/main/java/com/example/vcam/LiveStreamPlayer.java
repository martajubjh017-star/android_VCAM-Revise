package com.example.vcam;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;

import java.io.DataInputStream;
import java.net.Socket;

import de.robv.android.xposed.XposedBridge;

// Live MJPEG-over-TCP receiver for VCAM (Patch 8).
// Protocol: repeated [4-byte big-endian length][JPEG bytes].
// With `adb reverse tcp:8080 tcp:8080`, connect to 127.0.0.1:8080 -> PC.
// Decodes each JPEG, scales to the app's preview size, converts to NV21
// and writes it straight into HookMain.data_buffer (same path VCAM uses
// for its vcam_frames folder), so onPreviewFrame streams it live.
public class LiveStreamPlayer {
    public static volatile boolean gotFirstFrame = false;

    private static Thread thread;
    private static volatile boolean stop = false;
    private static volatile String currentTarget = "";
    private static volatile int outW = 0;
    private static volatile int outH = 0;

    public static void start(final String host, final int port, final int w, final int h) {
        final String target = host + ":" + port + "@" + w + "x" + h;
        if (thread != null && thread.isAlive() && target.equals(currentTarget)) {
            return;
        }
        stop = true;
        if (thread != null) {
            try { thread.join(200); } catch (InterruptedException e) { }
        }
        stop = false;
        gotFirstFrame = false;
        currentTarget = target;
        outW = w;
        outH = h;
        thread = new Thread(new Runnable() {
            public void run() {
                while (!stop) {
                    Socket sock = null;
                    try {
                        sock = new Socket(host, port);
                        sock.setTcpNoDelay(true);
                        DataInputStream in = new DataInputStream(sock.getInputStream());
                        byte[] lenBuf = new byte[4];
                        while (!stop) {
                            in.readFully(lenBuf);
                            int len = ((lenBuf[0] & 0xFF) << 24) | ((lenBuf[1] & 0xFF) << 16)
                                    | ((lenBuf[2] & 0xFF) << 8) | (lenBuf[3] & 0xFF);
                            if (len <= 0 || len > 20 * 1024 * 1024) {
                                break;
                            }
                            byte[] jpeg = new byte[len];
                            in.readFully(jpeg);
                            Bitmap bmp = BitmapFactory.decodeByteArray(jpeg, 0, len);
                            if (bmp == null) {
                                continue;
                            }
                            int tw = outW > 0 ? outW : bmp.getWidth();
                            int th = outH > 0 ? outH : bmp.getHeight();
                            Bitmap scaled = Bitmap.createScaledBitmap(bmp, tw, th, true);
                            byte[] nv = bitmapToNV21(scaled);
                            if (nv != null) {
                                HookMain.data_buffer = nv;
                                gotFirstFrame = true;
                            }
                            if (scaled != bmp) {
                                scaled.recycle();
                            }
                            bmp.recycle();
                        }
                    } catch (Throwable t) {
                        XposedBridge.log("【VCAM】[stream]" + t.toString());
                    } finally {
                        if (sock != null) {
                            try { sock.close(); } catch (Throwable ignored) { }
                        }
                    }
                    if (!stop) {
                        try { Thread.sleep(500); } catch (InterruptedException e) { }
                    }
                }
            }
        });
        thread.setDaemon(true);
        thread.start();
        XposedBridge.log("【VCAM】[stream] player started -> " + host + ":" + port + " (" + w + "x" + h + ")");
    }

    public static void stopPlayer() {
        stop = true;
    }

    // Same RGB->YUV(NV21) formula VCAM already uses in HookMain.rgb2YCbCr420,
    // so colors behave identically to the normal virtual.mp4 replacement.
    private static byte[] bitmapToNV21(Bitmap bitmap) {
        if (bitmap == null) {
            return null;
        }
        int width = bitmap.getWidth();
        int height = bitmap.getHeight();
        int size = width * height;
        int[] pixels = new int[size];
        bitmap.getPixels(pixels, 0, width, 0, 0, width, height);
        int len = width * height;
        byte[] yuv = new byte[len * 3 / 2];
        int y, u, v;
        for (int i = 0; i < height; i++) {
            for (int j = 0; j < width; j++) {
                int rgb = pixels[i * width + j] & 0x00FFFFFF;
                int r = rgb & 0xFF;
                int g = (rgb >> 8) & 0xFF;
                int b = (rgb >> 16) & 0xFF;
                y = ((66 * r + 129 * g + 25 * b + 128) >> 8) + 16;
                u = ((-38 * r - 74 * g + 112 * b + 128) >> 8) + 128;
                v = ((112 * r - 94 * g - 18 * b + 128) >> 8) + 128;
                y = y < 16 ? 16 : Math.min(y, 255);
                u = u < 0 ? 0 : Math.min(u, 255);
                v = v < 0 ? 0 : Math.min(v, 255);
                yuv[i * width + j] = (byte) y;
                yuv[len + (i >> 1) * width + (j & ~1)] = (byte) u;
                yuv[len + (i >> 1) * width + (j & ~1) + 1] = (byte) v;
            }
        }
        return yuv;
    }
}
