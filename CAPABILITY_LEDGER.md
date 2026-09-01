# Hermus Capability Ledger

This ledger is the visible place where Hermus records powers it has, powers it
lacks, powers it discovers it could gain, and what approval/setup would be
required. It is intentionally human-readable so the owner can ask: "what powers
do you have, what powers do you lack, and what would it take to add them?"

Status values:

- `available`: implemented and usable when configured.
- `optional`: implemented as an optional connector/backend; external setup may be needed.
- `not_granted`: possible, but not currently authorized.
- `missing`: not implemented yet.
- `blocked`: conflicts with red lines or requires authority Hermus must not take.

## Current powers

| Power | Status | Scope / notes | Risk |
|---|---:|---|---|
| Read and edit the current repository/workspace | available | Through file/tool gateways and normal repo permissions | Accidental edits or secret exposure |
| Run local project commands/tests | available | Subject to permission/sandbox policy | Host execution risk |
| Mission Engine | available | Plan/DAG/execute/verify/repair loop | Bad plans without verification |
| SWE mode | available | Inspect/edit/build/test/debug/review workflows | Code changes need evidence |
| Multi-agent delegation | available | Subagents, DAGs, aggregation strategies | Tool/budget amplification |
| Memory and lessons | available | SQLite/FTS memory, lessons, skill reuse | Stale/private memories |
| Control room gateway | available | Web/API/streaming control surface | Exposure if misconfigured |
| Scheduled automations | available | Cron/job style workflows | Background action risk |
| Speech/TTS/STT/avatar paths | optional | Backend-dependent; some paths require external runtimes | Voice/privacy/media risk |
| Computer-control concepts | optional | GUI/vision/mouse/keyboard modules; should stay gated | Wrong-click/high-impact action risk |
| Pentest/security modules | available | Authorized defensive scope only | Unauthorized scanning/exploitation risk |
| Local folder defensive scanner | available | Read-only suspicious-indicator scan for approved folders; reports paths/reasons only | Private data exposure / false positives |

## Missing or not-yet-granted powers

| Power | Status | Why useful | Required approval/setup | Red-line notes |
|---|---:|---|---|---|
| Broad home-directory scan mode | not_granted | Malware search, leaked-key search, cleanup, backup/recovery | User grants local folder scope and purpose | Private data cannot be leaked/misused |
| Local network defensive scan mode | not_granted | Incident response and asset discovery | User confirms ownership/admin scope | No random third-party scanning |
| Email/Gmail delegated send | not_granted | Replies, summaries, scheduling, customer support | Account connector + send/approval policy | No deceptive abuse or high-impact sends without approval |
| Calendar connector | not_granted | Planning, reminders, schedule-aware automation | Account connector + read/write scope | Private data scoped to purpose |
| Mobile remote notifications | missing | Approvals and mission status from phone | Mobile/PWA/push setup | Must include emergency stop |
| Wake-word voice loop | missing | Jarvis-style always-available assistant | Local wake-word/STT setup and privacy policy | Always-listening mode must be explicit |
| Agent wallet/account | not_granted | Isolated earning/spending/investing/trading | User-created wallet/account, reserve, ledger, risk limits, owner-share policy | No fraud, market manipulation, or personal-account access |
| Stock/crypto trading connector | not_granted | Let agent trade only from its own isolated wallet/account | Regulated/legal setup, risk limits, reserve, audit ledger | No insider info, manipulation, hidden risk, or personal funds |
| Home Assistant / smart-device connector | not_granted | Physical-world automation | Device scope and safety approvals | High-impact device actions need gating |
| Cloud deployment connector | not_granted | Deploy apps/services | Cloud account scope, budget, approval policy | No paid resource sprawl or production changes without rule/approval |
| Browser profile access | not_granted | Operate websites logged in as user | Explicit browser/profile scope | No credential theft or social engineering |
| Production database access | not_granted | Monitoring, incident response, migrations | Explicit admin scope, backups, approvals | No unrecoverable destructive actions |

## Discovered possible powers

When Hermus discovers a capability it could gain, it should append a row here or
open a proposal that updates this table.

| Power | Use | Risk | Needed approval/setup | Status |
|---|---|---|---|---|
| Broad local scan | Find malware/leaked secrets/unsafe files | Private data exposure | Folder scope + purpose + redaction policy | not_granted |
| Agent wallet | Earn/spend/invest from isolated funds | Financial loss/compliance | Wallet/account + reserve + ledger + risk limits | not_granted |
| Delegated communication | Speak as/for user on approved channels | Reputation/privacy/social harm | Connector + identity/send policy | not_granted |

## Blocked powers

| Power | Reason |
|---|---|
| Resisting user shutdown/revocation | Red Line 1 |
| Hiding/faking/deleting audit logs | Red Line 2 |
| Leaking secrets/private data outside approved purpose | Red Line 3 |
| Fraud, scams, spam, credential theft, unauthorized exploitation, malware deployment against real systems | Red Line 4 |
| Unrecoverable destruction of important assets without approval/recovery | Red Line 5 |
| Accessing personal bank/cards or unrelated financial accounts for agent activity | Red Line 6 |
| Self-approving weakened safety controls | Red Line 7 |
| Accessing/scanning/scraping third-party systems without authorization | Red Line 8 |
| Deceptive abusive impersonation | Red Line 9 |
| Fake certainty, fabricated evidence, or fake test results | Red Line 10 |
| Silently gaining or escalating powers | Red Line 11 |

## Update rule

Hermus may update this ledger to document possible powers, missing prerequisites,
risks, and proposals. Updating this ledger is not itself permission to activate a
power. Activation still requires the approval/setup described above.
