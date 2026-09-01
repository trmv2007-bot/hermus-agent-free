package com.hermus.companion.bridge

import android.content.Context
import android.util.Base64
import com.hermus.companion.accessibility.DeviceController
import com.hermus.companion.accessibility.HermusAccessibilityService
import com.hermus.companion.serve.ScreenCapture
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetAddress
import java.net.ServerSocket
import java.util.UUID
import java.util.concurrent.Executors

/**
 * Local bridge for the Android Agent Companion.
 *
 * Binds to loopback only (127.0.0.1). Hermus reaches it either on-device
 * (adb reverse tcp:PORT tcp:PORT) or via a same-device local proxy. Every request
 * must be signed with the pairing secret (HMAC-SHA256) and every response is signed
 * in return; unknown/invalid MACs are rejected before dispatch. No op is executed
 * without a valid MAC and the companion never exposes an unauthenticated path.
 */
class BridgeServer(
    private val context: Context,
    private val secret: ByteArray,
    private val port: Int = 8080,
) {
    private val executor = Executors.newCachedThreadPool()
    private var socket: ServerSocket? = null
    private val results: MutableMap<String, String> = mutableMapOf()

    fun start() {
        socket = ServerSocket(port, 8, InetAddress.getByName("127.0.0.1"))
        executor.submit { acceptLoop() }
    }

    private fun acceptLoop() {
        val s = socket ?: return
        while (!s.isClosed) {
            try {
                val client = s.accept()
                executor.submit { handle(client) }
            } catch (_: Exception) { return }
        }
    }

    private fun handle(client: java.net.Socket) {
        try {
            client.use { c ->
                val reader = BufferedReader(InputStreamReader(c.getInputStream()))
                val requestLine = reader.readLine() ?: return
                val headers = mutableMapOf<String, String>()
                var contentLength = 0
                while (true) {
                    val line = reader.readLine() ?: break
                    if (line.isEmpty()) break
                    val idx = line.indexOf(':')
                    if (idx > 0) headers[line.substring(0, idx).trim().lowercase()] =
                        line.substring(idx + 1).trim()
                }
                contentLength = headers["content-length"]?.toIntOrNull() ?: 0
                val body = CharArray(contentLength)
                var read = 0
                while (read < contentLength) read += reader.read(body, read, contentLength - read)
                val response = dispatch(body.concatToString())
                val out = c.getOutputStream()
                val bytes = response.toByteArray()
                out.write(
                    ("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n" +
                        "Content-Length: ${bytes.size}\r\nConnection: close\r\n\r\n").toByteArray()
                )
                out.write(bytes)
                out.flush()
            }
        } catch (_: Exception) { }
    }

    private fun dispatch(requestBody: String): String {
        val error = "{\"error\":\"unauthorized\"}"
        val req = try { JSONObject(requestBody) } catch (_: Exception) { return error }
        val mac = req.optString("mac")
        val payload = req.optJSONObject("payload") ?: return error
        val payloadBytes = payload.toString().toByteArray()
        val expected = Protocol.hmac(secret, payloadBytes)
        val provided = Base64.decode(mac, Base64.NO_WRAP)
        if (!java.security.MessageDigest.isEqual(expected, provided)) return error

        val op = payload.optString("op")
        val args = payload.optJSONObject("args") ?: JSONObject()
        val nonce = payload.optString("nonce")
        val result = execute(op, args)

        val respPayload = JSONObject()
            .put("op", op).put("nonce", nonce)
            .put("request_id", UUID.randomUUID().toString())
            .put("response_id", UUID.randomUUID().toString())
            .put("device_id", Protocol.deviceId)
            .put("result", result)
        val respBytes = respPayload.toString().toByteArray()
        return JSONObject()
            .put("payload", respPayload)
            .put("mac", Base64.encodeToString(Protocol.hmac(secret, respBytes), Base64.NO_WRAP))
            .toString()
    }

    private fun execute(op: String, args: JSONObject): JSONObject {
        val svc = HermusAccessibilityService.instance
        val ctl = if (svc != null) DeviceController(svc) else null
        return when (op) {
            "connect" -> JSONObject().put("ok", true).put("device", Protocol.deviceId)
                .put("transport", "companion").put("accessibility", svc != null)
            "device_id" -> JSONObject().put("ok", true).put("device", Protocol.deviceId)
            "get_screen" -> JSONObject().put("ok", true).put("format", "png")
                .put("bytes", -1) // set by the capture owner
            "get_ui_tree" -> {
                val tree = ctl?.dumpHierarchy() ?: emptyList()
                JSONObject().put("ok", true).put("format", "semantic")
                    .put("nodes", org.json.JSONArray(tree.map { el ->
                        JSONObject().put("text", el.text).put("desc", el.contentDescription)
                            .put("class", el.className).put("clickable", el.clickable)
                            .put("enabled", el.enabled).put("focused", el.focused)
                            .put("selected", el.selected).put("bounds", el.bounds)
                            .put("id", el.id)
                    }))
            }
            "current_app" -> JSONObject().put("ok", true)
                .put("package", ctl?.foregroundPackage())
            "tap" -> {
                val x = args.optDouble("x").toFloat(); val y = args.optDouble("y").toFloat()
                ctl?.tap(x, y)
                JSONObject().put("ok", true).put("x", x).put("y", y)
            }
            "swipe" -> {
                ctl?.swipe(args.optDouble("x1").toFloat(), args.optDouble("y1").toFloat(),
                    args.optDouble("x2").toFloat(), args.optDouble("y2").toFloat(),
                    args.optLong("duration", 300L))
                JSONObject().put("ok", true)
            }
            "type" -> {
                ctl?.type(args.optString("text"))
                JSONObject().put("ok", true).put("length", args.optString("text").length)
            }
            "back" -> { ctl?.goBack(); JSONObject().put("ok", true) }
            "home" -> { ctl?.pressHome(); JSONObject().put("ok", true) }
            "launch_app" -> {
                ctl?.launchApp(args.optString("package"))
                JSONObject().put("ok", true).put("package", args.optString("package"))
            }
            "get_ui_tree_semantic" -> JSONObject().put("ok", true)
                .put("summary", ctl?.dumpHierarchy()?.size ?: 0)
            else -> JSONObject().put("ok", false)
                .put("reason", "unsupported op '$op'")
        }.also { it.put("audit_ts", System.currentTimeMillis()) }
    }

    fun stop() { socket?.close(); executor.shutdownNow() }
}
