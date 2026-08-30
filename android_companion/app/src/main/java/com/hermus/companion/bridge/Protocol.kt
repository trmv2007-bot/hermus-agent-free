package com.hermus.companion.bridge

import java.security.MessageDigest
import java.util.UUID
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * Wire protocol shared with the Hermus backend (core.android.transport).
 *
 * Every command is a signed envelope:
 *   { "payload": {"op": ..., "args": {...}, "nonce": ...}, "mac": "base64(hmac)" }
 * `payload` is first serialized to UTF-8 bytes, then `mac` is the HMAC-SHA256 of
 * those bytes under the pairing secret. Both request and response are signed so a
 * MITM / rogue companion cannot inject or replay (nonce prevents replay).
 *
 * Ops supported by the companion:
 *   connect, device_id, get_screen, get_ui_tree,
 *   tap(x,y), swipe(x1,y1,x2,y2,dur), scroll, type(text),
 *   keyevent(code), back, home, recent, launch_app(package),
 *   current_app, clipboard_read, clipboard_write(text)
 */
object Protocol {
    val deviceId: String by lazy { UUID.randomUUID().toString() }

    fun opClasses(): Set<String> = setOf(
        "connect", "device_id", "get_screen", "get_ui_tree", "clipboard_read",
        "tap", "swipe", "scroll", "type", "keyevent", "back", "home", "recent",
        "launch_app", "current_app", "clipboard_write",
    )

    fun allowed(secret: ByteArray, payload: ByteArray): ByteArray =
        hmac(secret, payload)

    fun hmac(secret: ByteArray, data: ByteArray): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(secret, "HmacSHA256"))
        return mac.doFinal(data)
    }

    fun sha256(data: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(data)
}
