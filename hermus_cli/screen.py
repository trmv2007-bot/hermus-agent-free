"""screen — Screen recording, visual timelines, verification and watching."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ._common import add_screen_start_args

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    screen_parser = subparsers.add_parser("screen", help="Screen recording, visual timelines, verification and watching")
    screen_sub = screen_parser.add_subparsers(dest="screen_action")

    # Backward-compatible short form: hermus screen start|stop|status|save
    screen_start = screen_sub.add_parser("start", help="Start the background recorder")
    add_screen_start_args(screen_start)
    screen_sub.add_parser("stop", help="Stop and finalize the background recorder")
    screen_sub.add_parser("status", help="Background recorder status")
    screen_save = screen_sub.add_parser("save", help="Save the last recording as a video or task bundle")
    screen_save.add_argument("target", help="Filename (.mp4/.webm) or task id")

    # Explicit form from the Computer Agent architecture:
    # hermus screen record start|stop|status|save
    screen_record = screen_sub.add_parser("record", help="Conventional MP4/WebM screen recording")
    record_sub = screen_record.add_subparsers(dest="record_action")
    record_start = record_sub.add_parser("start", help="Start recording in a detached local service")
    add_screen_start_args(record_start)
    record_sub.add_parser("stop", help="Stop and finalize recording")
    record_sub.add_parser("status", help="Show recording status")
    record_save = record_sub.add_parser("save", help="Save the latest video or complete task bundle")
    record_save.add_argument("target", help="Filename (.mp4/.webm) or task id")

    screen_analyze = screen_sub.add_parser("analyze", help="Generate a visual event timeline from a recording")
    screen_analyze.add_argument("video", nargs="?", default="", help="MP4/WebM path (default: latest recording)")
    screen_analyze.add_argument("--task", default="Screen recording")
    screen_analyze.add_argument("--sample-fps", type=float, default=2.0)
    screen_analyze.add_argument("--max-seconds", type=float, default=3600.0)
    screen_analyze.add_argument("--max-events", type=int, default=12)
    screen_analyze.add_argument("--model", default="llava:7b")
    screen_analyze.add_argument("--no-vision", action="store_true", help="Detect changes without running a vision model")

    screen_watch = screen_sub.add_parser("watch", help="Wait until a visual condition becomes true")
    screen_watch.add_argument("condition")
    screen_watch.add_argument("--timeout", type=float, default=60.0)
    screen_watch.add_argument("--fps", type=float, default=2.0)
    screen_watch.add_argument("--model", default="llava:7b")


def run(args, ctx: CLIContext) -> None:
    import json

    from core.computer.service import ScreenRecordingService

    service = ScreenRecordingService()
    # Normalize short and explicit record forms to one action.
    action = args.record_action if args.screen_action == "record" else args.screen_action
    if action == "start":
        result = service.start(
            fps=args.fps,
            max_seconds=args.buffer_seconds,
            container=args.format,
        )
        print(json.dumps(result, indent=2, default=str))
    elif action == "stop":
        print(json.dumps(service.stop(), indent=2, default=str))
    elif action == "status":
        print(json.dumps(service.status(), indent=2, default=str))
    elif action == "save":
        print(json.dumps(service.save(args.target), indent=2, default=str))
    elif action == "analyze":
        from core.computer import VideoAnalyzer

        state = service.status()
        video = args.video or state.get("output_path") or ""
        analyzer = VideoAnalyzer() if args.no_vision else VideoAnalyzer.with_ollama(args.model)
        result = analyzer.analyze_video(
            video,
            task=args.task,
            sample_fps=args.sample_fps,
            max_seconds=args.max_seconds,
            max_events=args.max_events,
        )
        if result.get("success") and video:
            source = Path(video).expanduser().resolve()
            timeline_path = source.parent / "timeline.json"
            events_path = source.parent / "events.json"
            timeline_path.write_text(json.dumps(result.get("timeline", {}), indent=2), encoding="utf-8")
            events_path.write_text(json.dumps(result.get("events", []), indent=2), encoding="utf-8")
            result["timeline_path"] = str(timeline_path)
            result["events_path"] = str(events_path)
        print(json.dumps(result, indent=2, default=str))
    elif action == "watch":
        from core.computer import ScreenRecorder, ScreenWatcher, VideoAnalyzer

        recorder = ScreenRecorder(fps=args.fps, max_seconds=max(10.0, args.timeout))
        analyzer = VideoAnalyzer.with_ollama(args.model)
        result = ScreenWatcher(recorder, analyzer=analyzer).watch(
            args.condition,
            timeout=args.timeout,
            start_if_needed=True,
        )
        print(json.dumps(result, indent=2, default=str))
    else:
        ctx.parser.parse_args(["screen", "--help"])
