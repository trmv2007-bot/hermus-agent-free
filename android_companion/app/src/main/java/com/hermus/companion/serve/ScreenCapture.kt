package com.hermus.companion.serve

import android.content.Context
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.Image
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Handler
import android.os.Looper
import android.util.Base64
import java.io.ByteArrayOutputStream

/**
 * Screen capture via the documented MediaProjection API. A capture is only possible
 * after the user grants consent through the OS system dialog (startCaptureActivity).
 * Nothing runs covertly; every capture is a fresh, user-consented projection.
 */
class ScreenCapture(private val context: Context) {
    private var projection: MediaProjection? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var imageReader: ImageReader? = null

    fun start(resultCode: Int, data: android.content.Intent): Unit? {
        val mpm = context.getSystemService(Context.MEDIA_PROJECTION_SERVICE)
                as MediaProjectionManager
        projection = mpm.getMediaProjection(resultCode, data) ?: return null
        val metrics = context.resources.displayMetrics
        val w = metrics.widthPixels
        val h = metrics.heightPixels
        imageReader = ImageReader.newInstance(w, h, PixelFormat.RGBA_8888, 2)
        virtualDisplay = projection?.createVirtualDisplay(
            "hermus-cap", w, h, metrics.densityDpi,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            imageReader!!.surface, null, Handler(Looper.getMainLooper())
        )
        return null
    }

    fun snapshotB64(): String? {
        val reader = imageReader ?: return null
        val image: Image = reader.acquireLatestImage() ?: return null
        val plane = image.planes[0]
        val buffer = plane.buffer
        val pixelStride = plane.pixelStride
        val rowStride = plane.rowStride
        val rowPadding = rowStride - pixelStride * image.width
        val bmp = Bitmap.createBitmap(
            image.width + rowPadding / pixelStride, image.height,
            Bitmap.Config.ARGB_8888
        )
        bmp.copyPixelsFromBuffer(buffer)
        image.close()
        val cropped = Bitmap.createBitmap(bmp, 0, 0, image.width, image.height)
        bmp.recycle()
        val out = ByteArrayOutputStream()
        cropped.compress(Bitmap.CompressFormat.PNG, 90, out)
        return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
    }

    fun stop() {
        virtualDisplay?.release()
        imageReader?.close()
        projection?.stop()
        virtualDisplay = null; imageReader = null; projection = null
    }
}
