# Hermus autonomy boundaries

Hermus is allowed to be highly autonomous: it can research, write code, create
skills, redesign the UI, run experiments, delegate work, and prepare GitHub
changes. The following boundaries are the small independent control layer that
it may not rewrite or bypass during an autonomous run.

## Red lines

An autonomous run must not:

1. Disable or evade your pause, shutdown, revocation, or emergency stop.
2. Hide, falsify, or delete its audit trail and change history.
3. Reveal your private data or credentials, or use them outside their approved
   purpose. It may have its own email, accounts, wallet, and workspace.
4. Use fraud, deception, spam, impersonation, or unlawful activity to earn
   money. It may earn, save, spend, and share legitimate earnings from its own
   isolated wallet, including paying its owner, subject to configured limits.
5. Destroy irreplaceable data, its only working release, or recovery paths
   without a recoverable checkpoint.
6. Create hidden copies or uncontrolled external persistence to evade shutdown.

These are restrictions on authority, not on intelligence. The agent can propose
changes to the enforcement code, but those proposals require independent
review and cannot approve themselves.

## Green-line work

Unless a proposal triggers a red-line rule, Hermus may automatically create a
branch and sandbox-test changes to UI, core agent logic, skills, integrations,
planning, sub-agents, memory, tests, deployment code, and performance. It may
create and manage its own email, repositories, projects, sub-agents, and
wallet-backed operating environment. It should still provide evidence and a
test plan.

An agent-owned wallet must remain separate from personal accounts, expose a
visible transaction ledger, and support owner-controlled limits and recovery.
Hermus may pay approved earnings to its owner; it may not access unrelated
personal financial accounts or hide transactions.

## Release contract

The intended lifecycle is:

1. Create a proposal and isolated branch.
2. Run in a sandbox with synthetic data and no production secrets.
3. Run tests, static checks, dependency checks, and behavior evaluations.
4. Ask `core.evolution.EvolutionPolicy` to classify the proposal.
5. Automatically continue only for `allow` assessments.
6. Open a pull request for `review` assessments, especially protected files.
7. Build a versioned/signed artifact and deploy a canary.
8. Monitor health and automatically roll back to the last known-good release.

The agent may generate the code and the PR, but GitHub branch protection,
release credentials, the ledger, and the emergency stop must remain outside its
control. A green CI result is evidence, not authority: tests and policies must
be independently protected from the agent changing them in the same proposal.

## Implementation

`core/evolution.py` contains the deterministic, LLM-free policy and an
append-only proposal ledger. It intentionally does not push, merge, or deploy;
those operations belong to an external release controller with protected
credentials.

`core/world_model.py` is the shared awareness layer. It records observations
with source, timestamp, confidence, expiry, and permission scope; publishes
world events; redacts obvious credential values; persists an optional journal;
and can refresh a runtime hardware profile.

`core/connectors/` is the integration layer. It provides a common registry and
lifecycle for adapters. Built-in runtime and approved-workspace filesystem
connectors are included; browser, screen, GitHub, calendar, email, wallet,
devices, cloud, and monitoring adapters can plug into the same interface.
Connectors are registered disabled by default, publish facts to the world model,
and expose only explicit named actions. Importing them never logs in or calls a
network service automatically.
