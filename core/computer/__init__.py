"""Hermus Computer Agent v1: Record → Detect → Understand → Verify.

The package is headless-safe and dependency-injectable. Capture uses a bounded
compressed RAM buffer; full sessions can stream to MP4/WebM; only debounced
important frames are promoted to local vision analysis.
"""
from .event_detector import EventDetector, StreamingEventDetector
from .frame_sampler import FrameSampler
from .permissions import RecordingPolicy, recording_policy
from .recorder import (
    CallableSource,
    ImageGrabSource,
    NullSource,
    ScreenRecorder,
    ScreenSource,
    decode_frame,
    encode_image,
)
from .timeline import TaskArtifacts, Timeline, TimelineEvent
from .verifier import ActionVerificationManager, ScreenVerifier
from .video_analyzer import OllamaVisionModel, VideoAnalyzer
from .video_writer import VideoWriter
from .watcher import ScreenWatcher

__all__ = [
    "ScreenRecorder",
    "ScreenSource",
    "ImageGrabSource",
    "NullSource",
    "CallableSource",
    "encode_image",
    "decode_frame",
    "FrameSampler",
    "VideoWriter",
    "EventDetector",
    "StreamingEventDetector",
    "OllamaVisionModel",
    "VideoAnalyzer",
    "Timeline",
    "TimelineEvent",
    "TaskArtifacts",
    "ScreenVerifier",
    "ActionVerificationManager",
    "ScreenWatcher",
    "RecordingPolicy",
    "recording_policy",
]
