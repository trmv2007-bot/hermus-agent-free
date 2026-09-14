"""CLI dispatch coverage — the split is only safe if the commands actually run.

Phase 2 split a 2776-line ``hermus.py`` into ``hermus_cli/`` (one module per
command group) and pinned the migration with *source-text* checks (help output
byte-identical, one module per command). Text pins prove nothing moved; they do
not prove the dispatch table still works. Measured coverage of ``hermus_cli``
was **0%** — nothing in the suite ever imported or executed it.

These tests execute the CLI in-process:

* every command module exposes ``register`` + ``run``;
* ``build_parser()`` really declares every command in the dispatch table;
* ``<command> --help`` parses for all ~45 commands (exercises every
  ``register()``, which is where a broken subparser would surface);
* ``main()`` routes a real argv to the module's ``run`` (verified with a spy,
  so no command has to actually perform work);
* a curated set of read-only commands run for real and print sane output.

Nothing here touches the network, and no test invokes a command that writes.
"""

from __future__ import annotations

import pytest

from hermus_cli import COMMANDS, build_parser, main

#: Read-only commands that are safe to execute for real: no network, no writes,
#: no long probes. Each entry is the argv after the program name.
READ_ONLY_INVOCATIONS = [
    ["tools"],
    ["powers", "list"],
    ["powers", "registry"],
    ["profile", "list"],
    ["multikey", "providers"],
    ["multikey", "list"],
    ["forge", "list"],
    ["forge", "stats"],
    ["forge", "quarantine"],
    ["forge", "log"],
    ["mem2", "index"],
    ["presence", "status"],
]


# ---------------------------------------------------------------------------
# Structure of the dispatch table
# ---------------------------------------------------------------------------
def test_every_command_module_exposes_register_and_run():
    for name, module in COMMANDS.items():
        assert callable(getattr(module, "register", None)), f"{name} has no register()"
        assert callable(getattr(module, "run", None)), f"{name} has no run()"


def test_dispatch_table_is_not_empty_and_has_no_duplicate_targets():
    assert len(COMMANDS) >= 40, f"expected the full command set, got {len(COMMANDS)}"
    targets = [m.__name__ for m in COMMANDS.values()]
    assert len(targets) == len(set(targets)), "two commands point at the same module"


def test_build_parser_declares_every_command():
    parser = build_parser()
    actions = [a for a in parser._actions if getattr(a, "choices", None) and a.dest == "command"]
    assert actions, "no subparsers were registered"
    declared = set(actions[0].choices)
    assert declared == set(COMMANDS), f"parser/table mismatch: {declared ^ set(COMMANDS)}"


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_command_help_parses(name):
    """Every registered subparser is internally consistent.

    A mis-wired argument (missing dest, duplicate option, bad add_subparsers
    call) raises here rather than at a user's terminal.
    """
    parser = build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args([name, "--help"])
    assert excinfo.value.code == 0


def test_unknown_command_is_rejected_not_ignored():
    parser = build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["definitely-not-a-command"])
    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# main() routing
# ---------------------------------------------------------------------------
#: Minimal argv for commands that require positional arguments. `run` is
#: replaced by a spy, so these never do any real work.
MINIMAL_ARGV = {
    "delegate": ["delegate", "test-goal"],
    "research": ["research", "test-query"],
    "run": ["run", "test-task"],
    "skill": ["skill", "list"],
    "subagent": ["subagent", "spawn", "test-task"],
}


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_main_routes_to_the_command_module(monkeypatch, name):
    """main() must dispatch argv to COMMANDS[name].run — the thing the split
    could most easily have broken (a renamed module, a stale import).

    Every command is really dispatched (none skips on an argparse error):
    the five that take required positionals get them from MINIMAL_ARGV.
    """
    calls: list[object] = []
    monkeypatch.setattr(COMMANDS[name], "run", lambda args, ctx: calls.append(args))
    monkeypatch.setattr("sys.argv", ["hermus", *MINIMAL_ARGV.get(name, [name])])
    main()
    assert len(calls) == 1, f"{name} was not dispatched to its run()"


def test_every_command_with_required_args_is_covered():
    """Guard the guard: if a new command starts requiring arguments, add it to
    MINIMAL_ARGV instead of letting its dispatch test silently no-op."""
    import io
    from contextlib import redirect_stderr

    parser = build_parser()
    needing_args = set()
    for name in COMMANDS:
        try:
            with redirect_stderr(io.StringIO()):
                parser.parse_args([name])
        except SystemExit:
            needing_args.add(name)
    assert needing_args == set(MINIMAL_ARGV), f"commands requiring args changed: {needing_args ^ set(MINIMAL_ARGV)}"


def test_main_with_no_command_starts_the_default_repl(monkeypatch):
    """Bare `hermus` is the documented chat entry point, not an accident."""
    calls: list[object] = []
    monkeypatch.setattr("hermus_cli.repl.run_default", lambda args, ctx: calls.append(args))
    monkeypatch.setattr("sys.argv", ["hermus"])
    main()
    assert len(calls) == 1, "bare `hermus` must delegate to the repl default"


# ---------------------------------------------------------------------------
# Real execution of read-only commands
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("argv", READ_ONLY_INVOCATIONS)
def test_read_only_command_runs_and_prints(monkeypatch, capsys, argv):
    monkeypatch.setattr("sys.argv", ["hermus", *argv])
    main()  # must not raise
    out = capsys.readouterr().out
    assert out.strip(), f"{' '.join(argv)} printed nothing"


def test_tools_command_reports_the_registry(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["hermus", "tools"])
    main()
    out = capsys.readouterr().out
    assert "Registered tools:" in out


def test_powers_list_reports_from_the_ledger(monkeypatch, capsys, tmp_path, monkeypatch_env_ledger):
    """`powers list` reads the real ledger the conftest redirects to tmp."""
    monkeypatch.setattr("sys.argv", ["hermus", "powers", "list"])
    main()
    out = capsys.readouterr().out
    assert "discovered possible powers" in out or " - " in out


@pytest.fixture()
def monkeypatch_env_ledger(monkeypatch):
    """Keep `powers` writes out of the repo (capability ledger is elsewhere)."""
    _ = monkeypatch  # conftest already redirects the ledger; nothing to do
    yield
