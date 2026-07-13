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

    private LiveImageWriter(Surface surface, String tag) {
        this.surface = surface;
        this.tag = tag;
    }

    @Override
    public void run() {
        try {
            writer = ImageWriter.newInstance(surface, 2);
            XposedBridge.log("\u3010VCAM\u3011[c2-iw:" + tag + "] init ok");
        } catch (Throwable t) {
            XposedBridge.log("\u3010VCAM\u3011[c2-iw:" + tag + "] init FAIL " + t);
            return;
        }
        int pushed = 0;
        int loggedFormat = -1;
        while (running) {
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
                    // queue full / not ready yet
                    Thread.sleep(10);
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
                if (ok) {
                    writer.queueInputImage(img); // hands the buffer to the app's ImageReader
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
                int r = rgb & 0xFF;
                int g = (rgb >> 8) & 0xFF;
                int b = (rgb >> 16) & 0xFF;
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
        float scale = Math.max(w / sw, h / sh);
        float dx = (w - sw * scale) / 2f;
        float dy = (h - sh * scale) / 2f;
        Matrix m = new Matrix();
        m.postScale(scale, scale);
        m.postTranslate(dx, dy);
        Paint p = new Paint(Paint.FILTER_BITMAP_FLAG);
        c.drawBitmap(src, m, p);
        return scratch;
    }
}
