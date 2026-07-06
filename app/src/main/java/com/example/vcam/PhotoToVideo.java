package com.example.vcam;

import android.graphics.Bitmap;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaFormat;
import android.media.MediaMuxer;
import android.util.Log;

import java.nio.ByteBuffer;

public class PhotoToVideo {

    private static final String TAG = "【VCAM】[p2v]";

    public static boolean convert(Bitmap src, int width, int height, int seconds, int fps, String outPath) {
        width = width & ~1;
        height = height & ~1;
        MediaCodec encoder = null;
        MediaMuxer muxer = null;
        try {
            Bitmap scaled = Bitmap.createScaledBitmap(src, width, height, true);
            byte[] nv12 = bitmapToNV12(scaled, width, height);

            MediaFormat format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, width, height);
            format.setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420SemiPlanar);
            format.setInteger(MediaFormat.KEY_BIT_RATE, width * height * 4);
            format.setInteger(MediaFormat.KEY_FRAME_RATE, fps);
            format.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1);

            encoder = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC);
            encoder.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
            encoder.start();

            muxer = new MediaMuxer(outPath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
            int trackIndex = -1;
            boolean muxerStarted = false;

            int totalFrames = seconds * fps;
            int frameIndex = 0;
            long ptsUsPerFrame = 1000000L / fps;
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            boolean inputDone = false;

            while (true) {
                if (!inputDone) {
                    int inIndex = encoder.dequeueInputBuffer(10000);
                    if (inIndex >= 0) {
                        if (frameIndex >= totalFrames) {
                            encoder.queueInputBuffer(inIndex, 0, 0, frameIndex * ptsUsPerFrame, MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                            inputDone = true;
                        } else {
                            ByteBuffer inBuf = encoder.getInputBuffer(inIndex);
                            inBuf.clear();
                            inBuf.put(nv12);
                            encoder.queueInputBuffer(inIndex, 0, nv12.length, frameIndex * ptsUsPerFrame, 0);
                            frameIndex++;
                        }
                    }
                }
                int outIndex = encoder.dequeueOutputBuffer(info, 10000);
                if (outIndex == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    trackIndex = muxer.addTrack(encoder.getOutputFormat());
                    muxer.start();
                    muxerStarted = true;
                } else if (outIndex >= 0) {
                    ByteBuffer outBuf = encoder.getOutputBuffer(outIndex);
                    if ((info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0) {
                        info.size = 0;
                    }
                    if (info.size > 0 && muxerStarted && outBuf != null) {
                        outBuf.position(info.offset);
                        outBuf.limit(info.offset + info.size);
                        muxer.writeSampleData(trackIndex, outBuf, info);
                    }
                    encoder.releaseOutputBuffer(outIndex, false);
                    if ((info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) {
                        break;
                    }
                }
            }
            return true;
        } catch (Throwable t) {
            Log.e(TAG, "convert failed", t);
            return false;
        } finally {
            try { if (encoder != null) { encoder.stop(); encoder.release(); } } catch (Throwable ignored) {}
            try { if (muxer != null) { muxer.stop(); muxer.release(); } } catch (Throwable ignored) {}
        }
    }

    private static byte[] bitmapToNV12(Bitmap bitmap, int width, int height) {
        int[] argb = new int[width * height];
        bitmap.getPixels(argb, 0, width, 0, 0, width, height);
        byte[] yuv = new byte[width * height * 3 / 2];
        int yIndex = 0;
        int uvIndex = width * height;
        for (int j = 0; j < height; j++) {
            for (int i = 0; i < width; i++) {
                int c = argb[j * width + i];
                int r = (c >> 16) & 0xff;
                int g = (c >> 8) & 0xff;
                int b = c & 0xff;
                int y = (int) (0.299 * r + 0.587 * g + 0.114 * b);
                if (y < 0) y = 0; if (y > 255) y = 255;
                yuv[yIndex++] = (byte) y;
                if (j % 2 == 0 && i % 2 == 0) {
                    int u = (int) (-0.169 * r - 0.331 * g + 0.5 * b + 128);
                    int v = (int) (0.5 * r - 0.419 * g - 0.081 * b + 128);
                    if (u < 0) u = 0; if (u > 255) u = 255;
                    if (v < 0) v = 0; if (v > 255) v = 255;
                    yuv[uvIndex++] = (byte) u;
                    yuv[uvIndex++] = (byte) v;
                }
            }
        }
        return yuv;
    }
}
