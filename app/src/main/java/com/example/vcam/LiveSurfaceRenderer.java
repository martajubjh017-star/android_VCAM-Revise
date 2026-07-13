package com.example.vcam;

import android.graphics.Bitmap;
import android.opengl.EGL14;
import android.opengl.EGLConfig;
import android.opengl.EGLContext;
import android.opengl.EGLDisplay;
import android.opengl.EGLSurface;
import android.opengl.GLES20;
import android.opengl.GLUtils;
import android.view.Surface;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.util.ArrayList;
import java.util.List;

import de.robv.android.xposed.XposedBridge;

// Patch 8b: draws LiveStreamPlayer.latestBitmap onto an arbitrary output
// Surface using OpenGL ES 2.0 + EGL. Used to feed the app's Camera2/Camera1
// preview surfaces with live face-swapped frames instead of virtual.mp4.
// One instance == one Surface == one render thread.
public class LiveSurfaceRenderer implements Runnable {

    private static final List<LiveSurfaceRenderer> ACTIVE = new ArrayList<LiveSurfaceRenderer>();

    // Stop and forget every running renderer (called on camera (re)open).
    public static void stopAll() {
        synchronized (ACTIVE) {
            for (int i = 0; i < ACTIVE.size(); i++) {
                ACTIVE.get(i).running = false;
            }
            ACTIVE.clear();
        }
    }

    public static LiveSurfaceRenderer startFor(Surface surface, String tag) {
        LiveSurfaceRenderer r = new LiveSurfaceRenderer(surface, tag);
        Thread t = new Thread(r, "vcam-gl-" + tag);
        t.setDaemon(true);
        r.thread = t;
        synchronized (ACTIVE) { ACTIVE.add(r); }
        t.start();
        return r;
    }

    private final Surface surface;
    private final String tag;
    private volatile boolean running = true;
    private Thread thread;

    private EGLDisplay eglDisplay = EGL14.EGL_NO_DISPLAY;
    private EGLContext eglContext = EGL14.EGL_NO_CONTEXT;
    private EGLSurface eglSurface = EGL14.EGL_NO_SURFACE;

    private int program;
    private int texId;
    private int aPosLoc, aTexLoc, uTexLoc;
    private FloatBuffer vtxBuf, texBuf;
    private int surfW = 0, surfH = 0;

    private static final String VERT =
        "attribute vec4 aPos;\n" +
        "attribute vec2 aTex;\n" +
        "varying vec2 vTex;\n" +
        "void main() {\n" +
        "  gl_Position = aPos;\n" +
        "  vTex = aTex;\n" +
        "}\n";

    private static final String FRAG =
        "precision mediump float;\n" +
        "varying vec2 vTex;\n" +
        "uniform sampler2D uTex;\n" +
        "void main() {\n" +
        "  gl_FragColor = texture2D(uTex, vTex);\n" +
        "}\n";

    private LiveSurfaceRenderer(Surface surface, String tag) {
        this.surface = surface;
        this.tag = tag;
    }

    @Override
    public void run() {
        try {
            initEGL();
            initGL();
            XposedBridge.log("\u3010VCAM\u3011[c2-gl:" + tag + "] init ok " + surfW + "x" + surfH);
        } catch (Throwable t) {
            XposedBridge.log("\u3010VCAM\u3011[c2-gl:" + tag + "] init FAIL " + t);
            releaseEGL();
            return;
        }
        int drawn = 0;
        while (running) {
            try {
                Bitmap bmp = LiveStreamPlayer.latestBitmap;
                if (bmp != null && !bmp.isRecycled()) {
                    drawFrame(bmp);
                    drawn++;
                    if (drawn == 1) {
                        XposedBridge.log("\u3010VCAM\u3011[c2-gl:" + tag + "] first frame drawn");
                    }
                }
                Thread.sleep(16);
            } catch (Throwable t) {
                XposedBridge.log("\u3010VCAM\u3011[c2-gl:" + tag + "] loop " + t);
                try { Thread.sleep(200); } catch (InterruptedException ie) { }
            }
        }
        releaseEGL();
        XposedBridge.log("\u3010VCAM\u3011[c2-gl:" + tag + "] stopped (drawn=" + drawn + ")");
    }

    private void initEGL() {
        eglDisplay = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY);
        if (eglDisplay == EGL14.EGL_NO_DISPLAY) {
            throw new RuntimeException("no EGL display");
        }
        int[] version = new int[2];
        if (!EGL14.eglInitialize(eglDisplay, version, 0, version, 1)) {
            throw new RuntimeException("eglInitialize failed");
        }
        int[] attribList = {
            EGL14.EGL_RED_SIZE, 8,
            EGL14.EGL_GREEN_SIZE, 8,
            EGL14.EGL_BLUE_SIZE, 8,
            EGL14.EGL_ALPHA_SIZE, 8,
            EGL14.EGL_RENDERABLE_TYPE, EGL14.EGL_OPENGL_ES2_BIT,
            EGL14.EGL_NONE
        };
        EGLConfig[] configs = new EGLConfig[1];
        int[] numConfig = new int[1];
        if (!EGL14.eglChooseConfig(eglDisplay, attribList, 0, configs, 0, 1, numConfig, 0) || numConfig[0] <= 0) {
            throw new RuntimeException("eglChooseConfig failed");
        }
        int[] ctxAttrib = { EGL14.EGL_CONTEXT_CLIENT_VERSION, 2, EGL14.EGL_NONE };
        eglContext = EGL14.eglCreateContext(eglDisplay, configs[0], EGL14.EGL_NO_CONTEXT, ctxAttrib, 0);
        if (eglContext == EGL14.EGL_NO_CONTEXT) {
            throw new RuntimeException("eglCreateContext failed");
        }
        int[] surfAttrib = { EGL14.EGL_NONE };
        eglSurface = EGL14.eglCreateWindowSurface(eglDisplay, configs[0], surface, surfAttrib, 0);
        if (eglSurface == EGL14.EGL_NO_SURFACE) {
            throw new RuntimeException("eglCreateWindowSurface failed err=" + EGL14.eglGetError());
        }
        if (!EGL14.eglMakeCurrent(eglDisplay, eglSurface, eglSurface, eglContext)) {
            throw new RuntimeException("eglMakeCurrent failed");
        }
        int[] w = new int[1];
        int[] h = new int[1];
        EGL14.eglQuerySurface(eglDisplay, eglSurface, EGL14.EGL_WIDTH, w, 0);
        EGL14.eglQuerySurface(eglDisplay, eglSurface, EGL14.EGL_HEIGHT, h, 0);
        surfW = w[0];
        surfH = h[0];
    }

    private void initGL() {
        int vs = compileShader(GLES20.GL_VERTEX_SHADER, VERT);
        int fs = compileShader(GLES20.GL_FRAGMENT_SHADER, FRAG);
        program = GLES20.glCreateProgram();
        GLES20.glAttachShader(program, vs);
        GLES20.glAttachShader(program, fs);
        GLES20.glLinkProgram(program);
        int[] linked = new int[1];
        GLES20.glGetProgramiv(program, GLES20.GL_LINK_STATUS, linked, 0);
        if (linked[0] == 0) {
            String log = GLES20.glGetProgramInfoLog(program);
            throw new RuntimeException("link failed: " + log);
        }
        aPosLoc = GLES20.glGetAttribLocation(program, "aPos");
        aTexLoc = GLES20.glGetAttribLocation(program, "aTex");
        uTexLoc = GLES20.glGetUniformLocation(program, "uTex");

        float[] verts = { -1f, -1f, 1f, -1f, -1f, 1f, 1f, 1f };
        vtxBuf = toFloatBuffer(verts);
        // texcoords are computed per-frame in drawFrame (aspect center-crop).
        texBuf = toFloatBuffer(new float[] { 0f, 1f, 1f, 1f, 0f, 0f, 1f, 0f });

        int[] tex = new int[1];
        GLES20.glGenTextures(1, tex, 0);
        texId = tex[0];
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, texId);
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR);
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR);
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE);
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE);
    }

    private void drawFrame(Bitmap bmp) {
        int vw = surfW > 0 ? surfW : bmp.getWidth();
        int vh = surfH > 0 ? surfH : bmp.getHeight();
        GLES20.glViewport(0, 0, vw, vh);
        GLES20.glClearColor(0f, 0f, 0f, 1f);
        GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT);
        GLES20.glUseProgram(program);

        // Patch 10: aspect-preserving center-crop. Sample a centered sub-rect of
        // the bitmap so it fills the surface without stretching.
        float bw = bmp.getWidth();
        float bh = bmp.getHeight();
        float sAspect = (float) vw / (float) vh;
        float bAspect = bw / bh;
        float u0 = 0f, u1 = 1f, v0 = 0f, v1 = 1f;
        if (bAspect > sAspect) {
            float vis = sAspect / bAspect; // visible fraction of bitmap width
            u0 = (1f - vis) / 2f;
            u1 = 1f - u0;
        } else if (bAspect < sAspect) {
            float vis = bAspect / sAspect; // visible fraction of bitmap height
            v0 = (1f - vis) / 2f;
            v1 = 1f - v0;
        }
        // texcoords flipped vertically (bitmap top-left -> GL bottom-left)
        float[] texs = { u0, v1, u1, v1, u0, v0, u1, v0 };
        texBuf = toFloatBuffer(texs);

        GLES20.glActiveTexture(GLES20.GL_TEXTURE0);
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, texId);
        GLUtils.texImage2D(GLES20.GL_TEXTURE_2D, 0, bmp, 0);
        GLES20.glUniform1i(uTexLoc, 0);

        GLES20.glEnableVertexAttribArray(aPosLoc);
        GLES20.glVertexAttribPointer(aPosLoc, 2, GLES20.GL_FLOAT, false, 0, vtxBuf);
        GLES20.glEnableVertexAttribArray(aTexLoc);
        GLES20.glVertexAttribPointer(aTexLoc, 2, GLES20.GL_FLOAT, false, 0, texBuf);

        GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4);

        GLES20.glDisableVertexAttribArray(aPosLoc);
        GLES20.glDisableVertexAttribArray(aTexLoc);

        EGL14.eglSwapBuffers(eglDisplay, eglSurface);
    }

    private int compileShader(int type, String src) {
        int shader = GLES20.glCreateShader(type);
        GLES20.glShaderSource(shader, src);
        GLES20.glCompileShader(shader);
        int[] ok = new int[1];
        GLES20.glGetShaderiv(shader, GLES20.GL_COMPILE_STATUS, ok, 0);
        if (ok[0] == 0) {
            String log = GLES20.glGetShaderInfoLog(shader);
            GLES20.glDeleteShader(shader);
            throw new RuntimeException("shader compile failed: " + log);
        }
        return shader;
    }

    private static FloatBuffer toFloatBuffer(float[] data) {
        ByteBuffer bb = ByteBuffer.allocateDirect(data.length * 4);
        bb.order(ByteOrder.nativeOrder());
        FloatBuffer fb = bb.asFloatBuffer();
        fb.put(data);
        fb.position(0);
        return fb;
    }

    private void releaseEGL() {
        try {
            if (eglDisplay != EGL14.EGL_NO_DISPLAY) {
                EGL14.eglMakeCurrent(eglDisplay, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT);
                if (eglSurface != EGL14.EGL_NO_SURFACE) {
                    EGL14.eglDestroySurface(eglDisplay, eglSurface);
                }
                if (eglContext != EGL14.EGL_NO_CONTEXT) {
                    EGL14.eglDestroyContext(eglDisplay, eglContext);
                }
                // NOTE: do NOT call eglTerminate; the display is shared with the host app.
            }
        } catch (Throwable ignored) { }
        eglDisplay = EGL14.EGL_NO_DISPLAY;
        eglContext = EGL14.EGL_NO_CONTEXT;
        eglSurface = EGL14.EGL_NO_SURFACE;
    }
}
