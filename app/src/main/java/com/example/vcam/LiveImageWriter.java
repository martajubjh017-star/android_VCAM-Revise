package com.example.vcam;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.ImageFormat;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.media.Image;
import android.media.ImageWriter;
import android.view.Surface;

import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.List;

import de.robv.android.xposed.XposedBridge;

// Patch 8b v2: feeds LiveStreamPlayer.latestBitmap into an app Camera2
// *reader* Surface (ImageReader-backed, typically YUV_420_888) using an
// ImageWriter. This replaces the fragile GL path for reader surfaces
// (eglCreateWindowSurface tends to fail / crash on YUV_420_888 readers).
// Frames are aspect-preserved (center-crop) so the picture is not stretched.
// One instance == one Surface == one feeder thread.
public class LiveImageWriter implements Runnable {

    private static final List<LiveImageWriter> ACTIVE = new ArrayList<LiveImageWriter>();

    // Stop and forget every running feeder (called on camera (re)open).
    public static void stopAll() {
        synchronized (ACTIVE) {
            for (int i = 0; i < ACTIVE.size(); i++) {
                ACTIVE.get(i).running = false;
            }
            ACTIVE.clear();
        }
    }

    public static LiveImageWriter startFor(Surface surface, String tag) {
        LiveImageWriter w = new LiveImageWriter(surface, tag);
        Thread t = new Thread(w, "vcam-iw-" + tag);
        t.setDaemon(true);
        w.thread = t;
        synchronized (ACTIVE) { ACTIVE.add(w); }
        t.start();
        return w;
    }

    private final Surface surface;
    private final String tag;
    private volatile boolean running = true;
    private Thread thread;
    private ImageWriter writer;
    private Bitmap scratch; // reusable ARGB target for center-crop scaling
    private static final int ROT_DEG = 90; // Patch 14 v5: reader-buffer rotation compensation (flip to 270 if upside-down)
    // Patch 18 v9: settle-window debounce (skip feeding during Chromium open/close churn)
    private static final long SETTLE_MS = 800;
    private static volatile long latestGen = 0;
    private static volatile long lastBirthMs = 0;
    private final long myGen = nextGen();
    private static synchronized long nextGen() { lastBirthMs = System.currentTimeMillis(); return ++latestGen; }
    public static volatile boolean sessionLive = false; // Patch 17 v8

    private LiveImageWriter(Surface surface, String tag) {
        this.surface = surface;
        this.tag = tag;
    }

    @Override
    public void run() {
        try {
            writer = ImageWriter.newInstance(surface, 2); // Patch 16 v7: buffer 1->2 (feeder headroom, reverts v5)
            XposedBridge.log("\u3010VCAM\u3011[c2-iw:" + tag + "] init ok");
        } catch (Throwable t) {
            XposedBridge.log("\u3010VCAM\u3011[c2-iw:" + tag + "] init FAIL " + t);
            return;
        }
        int pushed = 0;
        int loggedFormat = -1;
        while (running) {
            // Patch 18 v9: only the newest feeder pushes, and only after churn settles
            if (myGen != latestGen || System.currentTimeMillis() - lastBirthMs < SETTLE_MS) {
                try { Thread.sleep(20); } catch (InterruptedException __ie) { }
                continue;
            }
            if (!sessionLive || surface == null || !surface.isValid()) { try { Thread.sleep(15); } catch (InterruptedException ie) { } continue; } // Patch 17 v8
            try {
                Bitmap bmp = LiveStreamPlayer.latestBitmap;
                if (bmp == null || bmp.isRecycled()) {
                    Thread.sleep(20);
                    continue;
                }
                Image img;
                try {
                    img = writer.dequeueInputImage();
                } catch (Throwable dq) {
                    // Patch 14 v5: reader surface abandoned (session teardown /
                    // resolution switch) -> stop feeding a dying surface; never hand a
                    // buffer to a capture device that is being destroyed.
                    XposedBridge.log("【VCAM】[c2-iw:" + tag + "] dequeue retry " + dq); // Patch 16 v7
                    try { Thread.sleep(80); } catch (InterruptedException ie) { }
                    continue;
                }
                if (img == null) { Thread.sleep(10); continue; }
                int fmt = img.getFormat();
                if (loggedFormat != fmt) {
                    loggedFormat = fmt;
                    XposedBridge.log("\u3010VCAM\u3011[c2-iw:" + tag + "] image "
                        + img.getWidth() + "x" + img.getHeight() + " fmt=" + fmt);
                }
                boolean ok = false;
                try {
                    ok = fillImage(img, bmp);
                } catch (Throwable ff) {
                    XposedBridge.log("\u3010VCAM\u3011[c2-iw:" + tag + "] fill " + ff);
                }
                if (ok && running && sessionLive) { // Patch 17 v8
                    try { writer.queueInputImage(img); } catch (Throwable qq) { XposedBridge.log("【VCAM】[c2-iw:" + tag + "] queue retry " + qq); try { img.close(); } catch (Throwable ig) { } try { Thread.sleep(80); } catch (InterruptedException ie) { } continue; } // Patch 16 v7
                    pushed++;
                    if (pushed == 1) {
                        XposedBridge.log("\u3010VCAM\u3011[c2-iw:" + tag + "] first frame pushed");
                    }
                } else {
                    try { img.close(); } catch (Throwable ignored) { }
                }
                Thread.sleep(28);
            } catch (Throwable t) {
                XposedBridge.log("\u3010VCAM\u3011[c2-iw:" + tag + "] loop " + t);
                try { Thread.sleep(150); } catch (InterruptedException ie) { }
            }
        }
        try { if (writer != null) writer.close(); } catch (Throwable ignored) { }
        if (scratch != null) { try { scratch.recycle(); } catch (Throwable ignored) { } scratch = null; }
        XposedBridge.log("\u3010VCAM\u3011[c2-iw:" + tag + "] stopped (pushed=" + pushed + ")");
    }

    // Fill an ImageWriter Image from the source bitmap, aspect-preserving
    // center-crop. Supports YUV_420_888. Returns false for unsupported formats.
    private boolean fillImage(Image img, Bitmap src) {
        if (img.getFormat() != ImageFormat.YUV_420_888) {
            return false;
        }
        int w = img.getWidth();
        int h = img.getHeight();
        Bitmap dst = centerCrop(src, w, h);

        int[] argb = new int[w * h];
        dst.getPixels(argb, 0, w, 0, 0, w, h);

        Image.Plane[] planes = img.getPlanes();
        ByteBuffer yb = planes[0].getBuffer();
        ByteBuffer ub = planes[1].getBuffer();
        ByteBuffer vb = planes[2].getBuffer();
        int yRow = planes[0].getRowStride();
        int uRow = planes[1].getRowStride();
        int vRow = planes[2].getRowStride();
        int uPix = planes[1].getPixelStride();
        int vPix = planes[2].getPixelStride();

        // NOTE: same RGB->YUV mapping as LiveStreamPlayer.bitmapToNV21 (keeps
        // colors consistent with the Camera1 path; R/B correction is a separate
        // global tweak if needed).
        for (int j = 0; j < h; j++) {
            int rowBase = j * w;
            int yLine = j * yRow;
            int cLine = (j >> 1);
            int uLine = cLine * uRow;
            int vLine = cLine * vRow;
            for (int i = 0; i < w; i++) {
                int rgb = argb[rowBase + i];
                // === Patch 13: R/B channel fix (ARGB_8888 is 0xAARRGGBB) ===
                int r = (rgb >> 16) & 0xFF;
                int g = (rgb >> 8) & 0xFF;
                int b = rgb & 0xFF;
                int y = ((66 * r + 129 * g + 25 * b + 128) >> 8) + 16;
                if (y < 16) y = 16; else if (y > 255) y = 255;
                yb.put(yLine + i, (byte) y);
                if ((i & 1) == 0 && (j & 1) == 0) {
                    int u = ((-38 * r - 74 * g + 112 * b + 128) >> 8) + 128;
                    int v = ((112 * r - 94 * g - 18 * b + 128) >> 8) + 128;
                    if (u < 0) u = 0; else if (u > 255) u = 255;
                    if (v < 0) v = 0; else if (v > 255) v = 255;
                    int ci = (i >> 1);
                    ub.put(uLine + ci * uPix, (byte) u);
                    vb.put(vLine + ci * vPix, (byte) v);
                }
            }
        }
        return true;
    }

    // CENTER_CROP scale of src into a w x h ARGB bitmap (cover, no stretch).
    private Bitmap centerCrop(Bitmap src, int w, int h) {
        if (scratch == null || scratch.getWidth() != w || scratch.getHeight() != h) {
            if (scratch != null) { try { scratch.recycle(); } catch (Throwable ignored) { } }
            scratch = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        }
        Canvas c = new Canvas(scratch);
        c.drawColor(0xFF000000);
        float sw = src.getWidth();
        float sh = src.getHeight();
        // Patch 14 v5: the app rotates the camera buffer by the sensor
        // orientation before display; storing our upright frame without matching
        // that makes the picture come out sideways/stretched with W & H swapped.
        // Rotate 90 deg when source & reader buffer have opposite orientation.
        boolean srcPortrait = sh > sw;
        boolean dstPortrait = h > w;
        int rot = (srcPortrait != dstPortrait) ? ROT_DEG : 0;
        float ew = (rot == 90 || rot == 270) ? sh : sw;
        float eh = (rot == 90 || rot == 270) ? sw : sh;
        float scale = Math.max(w / ew, h / eh);
        Matrix m = new Matrix();
        m.postTranslate(-sw / 2f, -sh / 2f);
        if (rot != 0) m.postRotate(rot);
        m.postScale(scale, scale);
        m.postTranslate(w / 2f, h / 2f);
        Paint p = new Paint(Paint.FILTER_BITMAP_FLAG);
        c.drawBitmap(src, m, p);
        return scratch;
    }
}
