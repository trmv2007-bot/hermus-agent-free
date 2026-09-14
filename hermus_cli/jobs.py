"""jobs — Inspect the gateway job queue (data/jobs/)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    jobs_parser = subparsers.add_parser("jobs", help="Inspect the gateway job queue (data/jobs/)")
    jobs_sub = jobs_parser.add_subparsers(dest="jobs_action")
    jobs_list = jobs_sub.add_parser("list", help="Recent jobs from the durable log")
    jobs_list.add_argument("--limit", type=int, default=20)
    jobs_list.add_argument("--status", default=None, help="queued|running|succeeded|failed|cancelled|interrupted")
    jobs_list.add_argument("--log", default=None, help="read another jobs.jsonl (e.g. another instance)")
    jobs_status = jobs_sub.add_parser("status", help="One job's status + result")
    jobs_status.add_argument("job_id")
    jobs_show = jobs_sub.add_parser("events", help="Replay a job's run events")
    jobs_show.add_argument("job_id")


def run(args, ctx: CLIContext) -> None:
    from gateway.queue import job_queue

    if args.jobs_action == "list":
        log = getattr(args, "log", None)
        if log:
            rows = job_queue.read_log(log, 500)
        else:
            rows = job_queue.recent_jobs(max(args.limit * 4, 40))
        state = getattr(args, "status", None)
        if state:
            rows = [r for r in rows if r.get("status") == state]
        rows = rows[: args.limit]
        if not rows:
            print(f"no jobs in {log or job_queue.persist_path} yet (is the gateway running?)")
        for row in rows:
            stamp = row.get("created") or row.get("ts") or row.get("finished") or ""
            print(
                f" {str(stamp)[:19]:19s} {str(row.get('job_id') or row.get('id', ''))[:16]:18s} "
                f"{str(row.get('kind', ''))[:18]:18s} {str(row.get('status', ''))[:10]:10s} "
                f"{str(row.get('duration_ms') or 0):>6}ms "
                f"{str(row.get('error') or row.get('result_brief') or '')[:56]}"
            )
    elif args.jobs_action == "status":
        print(__import__("json").dumps(job_queue.status(args.job_id), indent=2, default=str))
        res = job_queue.result(args.job_id)
        if res is not None:
            print("--- result ---")
            print(__import__("json").dumps(res, indent=2, default=str)[:3000])
    elif args.jobs_action == "events":
        for e in job_queue.events(args.job_id):
            print(
                f" #{e.get('id')} {e.get('ts', '')[:19]} {e.get('type'):22s} "
                f"{__import__('json').dumps(e.get('data'), default=str)[:110]}"
            )
    else:
        ctx.parser.parse_args(["jobs", "--help"])
