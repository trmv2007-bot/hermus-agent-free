"""engine — Local AI engine: NPU/GPU detection, NoLlama install/serve, model downloads."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    engine_parser = subparsers.add_parser(
        "engine", help="Local AI engine: NPU/GPU detection, NoLlama install/serve, model downloads"
    )
    engine_sub = engine_parser.add_subparsers(dest="engine_action")
    engine_sub.add_parser("status", help="Detected hardware, routing plan and engine health")
    engine_sub.add_parser("install", help="Install the NoLlama server (no model weights)")
    engine_start = engine_sub.add_parser("start", help="Start the local engine")
    engine_start.add_argument("--device", default="", help="NPU | GPU | CPU (default: auto-detect)")
    engine_start.add_argument("--model-dir", default="", help="Model directory to serve")
    engine_sub.add_parser("stop", help="Stop the local engine")
    engine_sub.add_parser("models", help="List catalog + models already on disk")
    engine_dl = engine_sub.add_parser("download", help="Download a model (default: minicpm)")
    engine_dl.add_argument("model", nargs="?", default="minicpm", help="Catalog id (see 'engine models')")
    engine_dl.add_argument("--wait", action="store_true", help="Wait for the download to finish")


def run(args, ctx: CLIContext) -> None:
    import json as _json

    from core.accelerators import state as engine_state
    from core.nollama import TERMINAL_STATES, nollama_manager

    action = getattr(args, "engine_action", None)
    if action == "status":
        info = engine_state()
        plan = info["plan"]
        hw = plan.get("hardware") or {}
        print(
            f"mode      : {plan.get('mode')}   status: {info.get('status')}"
            + (f"   action: {info['action']}" if info.get("action") else "")
        )
        print(f"NPU       : {', '.join(d['name'] for d in hw.get('npu', [])) or 'none detected'}")
        print(f"GPU       : {', '.join(d['name'] for d in hw.get('gpus', [])) or 'none detected'}")
        for role, assignment in (plan.get("roles") or {}).items():
            print(f"  {role:<11s} {assignment['engine']:<8s} {assignment['device']:<4s} {assignment['model']}")
        for note in plan.get("notes") or []:
            print(f"note      : {note}")
        if info.get("recommended_model"):
            rec = info["recommended_model"]
            print(f"missing   : {rec['name']} (~{rec['est_size_gb']} GB) — hermus engine download {rec['id']}")
        raise SystemExit(0)
    if action == "install":
        result = nollama_manager.install()
        print(_json.dumps(result, indent=2, default=str))
        raise SystemExit(0 if result.get("success") else 1)
    if action == "start":
        result = nollama_manager.start(
            device=getattr(args, "device", "") or "",
            model_dir=getattr(args, "model_dir", "") or None,
        )
        print(_json.dumps(result, indent=2, default=str))
        raise SystemExit(0 if result.get("success") else 1)
    if action == "stop":
        print(_json.dumps(nollama_manager.stop(), indent=2, default=str))
        raise SystemExit(0)
    if action == "models":
        for row in nollama_manager.list_catalog():
            flag = "installed" if row["installed"] else f"~{row['est_size_gb']} GB"
            print(f"  {row['id']:<16s} {flag:<12s} {row['name']}  [{','.join(row['devices'])}]")
        raise SystemExit(0)
    if action == "download":
        started = nollama_manager.download_model(getattr(args, "model", "minicpm") or "minicpm")
        if not started.get("success"):
            print(_json.dumps(started, indent=2, default=str))
            raise SystemExit(1)
        job = started["job"]
        if not getattr(args, "wait", False):
            print(_json.dumps(job, indent=2, default=str))
            raise SystemExit(0)
        import time as _time

        while True:
            job = nollama_manager.download_status(job["id"]) or job
            print(f"\r  {job['model_id']:<16s} {job['state']:<12s} {job['percent']:>5.1f}%", end="", flush=True)
            if job["state"] in TERMINAL_STATES:
                break
            _time.sleep(2)
        print()
        if job.get("error"):
            print(f"error: {job['error']}")
        raise SystemExit(0 if job["state"] == "ready" else 1)
    ctx.parser.parse_args(["engine", "--help"])
    raise SystemExit(2)
