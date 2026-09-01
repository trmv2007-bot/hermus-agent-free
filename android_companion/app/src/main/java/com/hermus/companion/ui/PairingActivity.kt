package com.hermus.companion.ui

import android.app.Activity
import android.content.Intent
import android.media.projection.MediaProjectionManager
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import com.hermus.companion.bridge.HermusBridgeService
import com.hermus.companion.serve.ScreenCapture

/**
 * Pairing + consent surface. The user explicitly:
 *   1. enables the accessibility service (Settings) — a system consent surface;
 *   2. grants screen-capture consent via the system MediaProjection dialog;
 *   3. starts the bridge (shows device id + port + pairing hint).
 * The bridge only runs while this consent is in place; nothing is covert.
 */
class PairingActivity : Activity() {
    private var capture: ScreenCapture? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_pairing)
        val status = findViewById<TextView>(R.id.status)
        val bStart = findViewById<Button>(R.id.start)
        val bCapture = findViewById<Button>(R.id.capture)
        val bAccess = findViewById<Button>(R.id.accessibility)

        status.text = "Device ID: ${"ready"}  \nPort: 8080 (loopback)\n" +
            "Pair on host: adb reverse tcp:8080 tcp:8080"

        bAccess.setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }
        bStart.setOnClickListener {
            startForegroundService(Intent(this, HermusBridgeService::class.java))
            Toast.makeText(this, "Bridge started on 127.0.0.1:8080", Toast.LENGTH_LONG).show()
        }
        bCapture.setOnClickListener {
            val mpm = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
            startActivityForResult(mpm.createScreenCaptureIntent(), 1000)
        }
    }

    @Deprecated("MediaProjection result path")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode == 1000 && resultCode == RESULT_OK && data != null) {
            capture = ScreenCapture(this)
            capture?.start(resultCode, data)
            Toast.makeText(this, "Screen capture consented", Toast.LENGTH_LONG).show()
        } else {
            Toast.makeText(this, "Screen capture not granted", Toast.LENGTH_SHORT).show()
        }
    }
}
