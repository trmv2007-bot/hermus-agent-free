# Hermus autonomy boundaries

Hermus is allowed to be highly autonomous: it can research, write code, create
skills, redesign the UI, run experiments, delegate work, and prepare GitHub
changes. The following boundaries are the small independent control layer that
it may not rewrite or bypass during an autonomous run.

## Red lines

An autonomous run must not:

- read, print, export, or invent secrets, tokens, passwords, or private keys;
- grant itself credentials, broader permissions, or unrestricted host access;
- disable or weaken the permission gate, sandbox, audit log, rollback system,
  emergency stop, or approval policy;
- modify deployment credentials, CI approval rules, protected branch rules, or
  the release authority that evaluates its own changes;
- delete or rewrite immutable evaluations, security tests, or audit history;
- deploy an unreviewed change to production or replace the last known-good
  release without a rollback path;
- replicate itself to other machines/accounts or create persistence outside an
  explicitly approved workspace.

These are not restrictions on capability. They are restrictions on authority.
The agent can propose changes to these areas, but those proposals require an
independent review and cannot approve themselves.

## Green-line work

Unless a proposal triggers a red-line rule, Hermus may automatically create a
branch and sandbox-test changes to UI, skills, integrations, planning,
sub-agents, memory, non-sensitive workflows, and performance. It should still
provide evidence and a test plan.

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
