# Archive

These documents are kept for the record, not as a description of the product.

They are the design and phase notes from when this repository was being built out
as a JARVIS/FAIRY-style personal agent: the control-room narrative, the phase
plan (world awareness, long-horizon planning, specialist ecosystem, distributed
nodes, and so on) and the environment specs that went with the cockpit.

That work is still in the code — the mission engine, the sentinel, the presence
kernel, the approval surface — but the product's home screen is now the harness
(`/harness`: a task in, a transcript and a diff out), and the cockpit moved to
`/control` as the ops view. Documentation that describes the old centre of
gravity would be misleading as a starting point, so it lives here.

The documents that stay load-bearing — `RED_LINES.md`, `AUTONOMY_BOUNDARIES.md` and
`CAPABILITY_LEDGER.md` — were never archived: they describe what the agent may do, which did not
change when the home screen did.

What to read instead:

| Question | Document |
|---|---|
| What is this and how do I run a task? | [`../../README.md`](../../README.md) |
| How is the code organised, and what owns what? | [`../ARCHITECTURE.md`](../../ARCHITECTURE.md) |
| How do I measure the agent on a task suite? | [`../HARNESS.md`](../HARNESS.md) |
| What may it do without asking me? | [`../../RED_LINES.md`](../../RED_LINES.md) and [`../../AUTONOMY_BOUNDARIES.md`](../../AUTONOMY_BOUNDARIES.md) |
| How do I install, verify or repair it? | [`../../QUICKSTART.md`](../../QUICKSTART.md) |

Nothing here is deleted, and nothing here is claimed to be current.
