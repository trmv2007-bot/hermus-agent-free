package com.hermus.companion.bridge

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import java.io.File

/**
 * Foreground service that hosts the local control bridge. It stays alive while a
 * mission wants device access (and only then) so the bridge is not an always-on,
 * covert channel. Pairing secret is loaded/created from the app's private storage
 * (created by the helper below) so a restart can re-pair without re-entering it.
 */
class HermusBridgeService : Service() {
    private var server: BridgeServer? = null

    companion object {
        private const val CHANNEL_ID = "hermus_bridge"
        fun secretFile(context: Context): File = File(context.filesDir, "pairing_secret.bin")
    }

    override fun onCreate() {
        super.onCreate()
        startForeground(1, notification())
        val secret = loadOrCreateSecret()
        server = BridgeServer(this, secret)
        server?.start()
    }

    override fun onDestroy() {
        server?.stop()
        server = null
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun loadOrCreateSecret(): ByteArray {
        val f = secretFile(this)
        if (f.exists()) return f.readBytes()
        val s = Protocol.sha256("hermus-${System.currentTimeMillis()}".toByteArray())
        f.writeBytes(s)
        return s
    }

    private fun notification(): Notification {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val ch = NotificationChannel(CHANNEL_ID, "Hermus Bridge", NotificationManager.IMPORTANCE_LOW)
        nm.createNotificationChannel(ch)
        return Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("Hermus Companion bridge active")
            .setContentText("Waiting for an authorised Hermus backend.")
            .setSmallIcon(android.R.drawable.ic_menu_manage)
            .build()
    }
}
