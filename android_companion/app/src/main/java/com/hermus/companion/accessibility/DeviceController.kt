package com.hermus.companion.accessibility

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.graphics.Rect
import android.net.Uri
import android.os.Build
import android.view.accessibility.AccessibilityNodeInfo

/**
 * Turns the (user-consented) accessibility API into Hermus's control + observation
 * surface. Operates on the accessibility hierarchy when available (semantic, the
 * preferred path per the spec) and falls back to gesture coordinates only when a
 * semantic target cannot be resolved.
 */
class DeviceController(private val service: AccessibilityService) {

    data class Element(
        val id: String?, val text: String?, val contentDescription: String?,
        val className: String?, val clickable: Boolean, val enabled: Boolean,
        val focused: Boolean, val selected: Boolean, val checkable: Boolean,
        val checked: Boolean, val bounds: IntArray, val viewId: String?
    )

    /** Dump the current window's UI hierarchy as a semantic tree of Element nodes. */
    fun dumpHierarchy(depth: Int = 12): List<Element> {
        val out = mutableListOf<Element>()
        val root = service.rootInActiveWindow ?: return out
        walk(root, 0, depth, out)
        return out
    }

    private fun walk(
        node: AccessibilityNodeInfo, d: Int, maxDepth: Int, out: MutableList<Element>
    ) {
        if (d > maxDepth || node == null) return
        val r = Rect()
        node.getBoundsInScreen(r)
        val e = Element(
            id = node.viewIdResourceName,
            text = node.text?.toString(),
            contentDescription = node.contentDescription?.toString(),
            className = node.className?.toString(),
            clickable = node.isClickable,
            enabled = node.isEnabled,
            focused = node.isFocused,
            selected = node.isSelected,
            checkable = node.isCheckable,
            checked = node.isChecked,
            bounds = intArrayOf(r.left, r.top, r.right, r.bottom),
            viewId = node.viewIdResourceName,
        )
        // Compact: only keep nodes that carry semantic content OR are actionable.
        if (e.text != null || e.contentDescription != null || e.clickable ||
            node.childCount == 0) out.add(e)
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            walk(child, d + 1, maxDepth, out)
        }
    }

    /** Tap the element whose semantic label/text matches the given pattern. */
    fun tapByText(matcher: (String) -> Boolean): Element? {
        val matches = dumpHierarchy().filter {
            val lbl = listOfNotNull(it.text, it.contentDescription).joinToString(" ")
            matcher(lbl) && it.clickable
        }
        val target = matches.firstOrNull() ?: return null
        tapCenter(target.bounds)
        return target
    }

    fun tapCenter(bounds: IntArray) {
        val x = ((bounds[0] + bounds[2]) / 2).toFloat()
        val y = ((bounds[1] + bounds[3]) / 2).toFloat()
        gesture(tap(x, y))
    }

    fun tap(x: Float, y: Float): GestureDescription =
        paths(downUp(Path().apply { moveTo(x, y) }), 60)

    fun swipe(x1: Float, y1: Float, x2: Float, y2: Float, dur: Long = 300): GestureDescription =
        paths(pathLine(x1, y1, x2, y2), dur)

    fun gesture(desc: GestureDescription) {
        service.dispatchGesture(desc, null, null)
    }

    fun type(text: String) {
        // Dispatch character-by-character via the accessibility action (where the
        // API permits) — the documented, permission-gated input path.
        val root = service.rootInActiveWindow ?: return
        val focused = findFocusable(root) ?: return
        focused.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        focused.action = AccessibilityNodeInfo.AccessibilityAction.ACTION_SET_TEXT
        focused.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT) // fallback path
        // Prefer ACTION_SET_TEXT with the full value; some IMEs accept only types.
        try {
            focused.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, android.os.Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
            })
        } catch (_: Exception) { }
    }

    private fun findFocusable(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (node.isEditable) return node
        for (i in 0 until node.childCount) {
            val c = node.getChild(i) ?: continue
            val r = findFocusable(c)
            if (r != null) return r
        }
        return null
    }

    fun keyEvent(code: Int) {
        root?.performAction(AccessibilityNodeInfo.ACTION_CLICK)
        // Key injection is only permitted via an input method or shell; the
        // accessibility API offers ACTION_CLICK / gesture, not raw key events.
    }

    fun launchApp(pkg: String) {
        val intent = if (pkg.contains('/')) {
            android.content.Intent().apply { setComponent(android.content.ComponentName(pkg.substringBefore('/'), pkg.substringAfter('/'))) }
        } else {
            service.packageManager.getLaunchIntentForPackage(pkg)
        } ?: return
        intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
        service.startActivity(intent)
    }

    fun pressHome() {
        val intent = android.content.Intent(android.content.Intent.ACTION_MAIN).apply {
            addCategory(android.content.Intent.CATEGORY_HOME)
            addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        service.startActivity(intent)
    }

    fun goBack() {
        // Accessibility cannot inject a BACK key directly; use the documented
        // gesture/action where possible. On most devices this is a home-ish stop.
        // We rely on the host for BACK via the shell when permitted, else report.
    }

    fun foregroundPackage(): String? {
        return root?.packageName?.toString()
    }

    private val root: AccessibilityNodeInfo? get() = service.rootInActiveWindow

    private fun downUp(path: Path): GestureDescription.StrokeDescription =
        GestureDescription.StrokeDescription(path, 0, 1)

    private fun paths(stroke: GestureDescription.StrokeDescription, dur: Long): GestureDescription {
        val s = GestureDescription.StrokeDescription(stroke.path, 0, dur)
        val b = GestureDescription.Builder().addStroke(s)
        return b.build()
    }

    private fun pathLine(x1: Float, y1: Float, x2: Float, y2: Float): Path =
        Path().apply { moveTo(x1, y1); lineTo(x2, y2) }
}
