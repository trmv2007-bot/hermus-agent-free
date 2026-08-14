"""Computer control & vision — screen recording, frame sampling, verification.

Hybrid recording system: a rolling screen buffer (last N seconds) instead of
sending the whole recording to a model continuously. Frames are only promoted
to the vision/state layer when something actually changes (motion/event
detection), keeping cost low.

Submodules:
- recorder      — rolling screen buffer (threaded), start/stop/status
- frame_sampler — change/event detection, important-frame selection
- verifier      — confirm expected UI state from before/after frames
- permissions   — GUI/ADMIN actions are gated via core.permissions

This package is headless-safe: the default screen source falls back to a
no-op when a display (or PIL.ImageGrab) is unavailable, and every component
accepts an injected source for testing.
"""
from .recorder import ScreenRecorder, ScreenSource, ImageGrabSource, NullSource
from .frame_sampler import FrameSampler
from .verifier import ScreenVerifier

__all__ = [
    "ScreenRecorder",
    "ScreenSource",
    "ImageGrabSource",
    "NullSource",
    "FrameSampler",
    "ScreenVerifier",
]
