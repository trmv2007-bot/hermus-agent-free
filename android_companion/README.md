# HERMUS Android Agent Companion

This is the **on-device** half of the Hermus Android bridge. It is the legitimate,
user-consented way for a Hermus backend to observe and drive an Android device. It
does **not** bypass Android security — it uses the documented, permission-gated APIs
(accessibility service + MediaProjection) and only acts on explicitly granted consent.

> Status: **reference implementation provided; NOT compiled/verified on a device.**
> Building and running it requires an Android SDK + a device/emulator. The
> physical-device E2E is marked **NOT VERIFIED** in `FINAL_REPORT.md`.

## What it does
- **Bridge service** (`HermusBridgeService`) — a loopback-only control socket on
  `127.0.0.1:8080` that speaks the same signed JSON protocol as
  `core.android.transport.BridgeAndroidTransport`.
- **Accessibility service** (`HermusAccessibilityService` + `DeviceController`) —
  reads the semantic UI hierarchy (text, labels, buttons, bounds, focus/enabled/
  selected state) and performs tap/swipe/type gestures.
- **Screen capture** (`ScreenCapture`) — MediaProjection screenshot (each capture is
  a fresh system-consented projection).
- **Pairing consent** (`PairingActivity`) — user enables accessibility, grants
  capture, and starts the bridge. Device ID + port + pairing hint are shown.

## Signing / security
Both the request and response are HMAC-SHA256-signed with the pairing secret
(`core.android.secure.new_pairing_secret()` on the host). The bridge binds to
loopback only; on a host you reach it with `adb reverse tcp:8080 tcp:8080`.
Unknown/invalid MACs are rejected before any op is dispatched.

## Build
```bash
cd android_companion
# Android Studio: File > Open this folder, then Build.
# or CLI (requires Android SDK + JDK 17):
./gradlew assembleDebug
# output: app/build/outputs/apk/debug/app-debug.apk
```

## Pair a Hermus backend
The host backend loads/stores a pairing secret via `core.android.secure.load_or_create_secret`
(`HERMUS_ANDROID_SECRET` path, default `data/android_pairing_secret.bin`). The
companion stores its own copy in app-private storage. For a real pairing flow the app
should display the secret and the host stores it (or the host generates a QR/secret the
app scans). **Implement and test this on your device** — it is not verified here.

## Permissions required (all user-granted)
- Accessibility service (Settings, explicit) — observation + gestures.
- MediaProjection (system dialog per capture) — screenshots.
- Foreground service + notifications — keeps the bridge alive only while used.

## Unsupported here
- Raw keyevent injection (not permitted via accessibility) — keys are the host/ADB
  path; the companion reports this honestly.
- Everything requiring a real device/emulator — see `FINAL_REPORT.md` §48 for exact
  verification steps.
