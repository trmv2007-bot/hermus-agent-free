package com.hermus.companion.accessibility

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent

/**
 * The accessibility bridge per Hermus's consent model. This service only becomes
 * active when the user explicitly enables it in Settings > Accessibility (a system
 * consent surface). It presents the current window's hierarchy to DeviceController
 * so Hermus can observe the semantic UI (text, labels, buttons, bounds, state).
 */
class HermusAccessibilityService : AccessibilityService() {
    companion object {
        @Volatile var instance: HermusAccessibilityService? = null
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) { /* app-driven queries */ }

    override fun onInterrupt() {}
}
