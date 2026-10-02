# Phase 8 — Scheduling & Time Awareness

Status: Complete

HERMUS now has a durable scheduling boundary for one-shot and recurring work.

## What changed

- Timezone-aware schedules using IANA time zone names.
- One-shot schedules such as "in 20 minutes", "tomorrow at 9am", and "at 18:30".
- Recurring schedules such as "every day at 9am", "every weekday at 9am", "every monday at 18:30", "every 2 hours", and direct five-field cron.
- Persistent schedule state in data/cron_jobs.json.
- Automatic restoration of enabled schedules after gateway/process restart.
- Enable/disable/delete lifecycle.
- Optional priorities and maximum run counts.
- Optional quiet-hour deferral.
- Next-run and last-run metadata.
- Scheduled work is submitted to the canonical JobQueue instead of executing tools directly.
- Runtime receives schedule_id, user_id, platform and trigger metadata for correlation.

## Safety contract

The scheduler decides when to submit work, not how to execute it.

Schedule
→ JobQueue
→ Mission/Runtime
→ permissions + approvals + red lines
→ execution
→ verification
→ learning

This prevents scheduled work from becoming a privileged bypass around the existing autonomy and safety architecture.

## API

The realtime gateway exposes:

- GET /schedules
- POST /schedules
- POST /schedules/{schedule_id}/enable
- DELETE /schedules/{schedule_id}

## Example

POST /schedules with:

{
  "schedule": "every weekday at 9am",
  "task": "Review my active projects and summarize anything urgent",
  "timezone": "Asia/Kolkata",
  "user_id": "default"
}

The task will enter the normal HERMUS runtime when the schedule fires.
