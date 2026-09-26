# Changelog

All notable changes to Hermus Agent Free are recorded here.
Format follows Keep a Changelog; versions follow Semantic Versioning.

## [0.2.0] - 2026-09-26

The spine remodel. First release that is installable and product-shaped rather
than "clone the repo and read bootstrap.py".

### Added
- Installable package: `pyproject.toml` gains a build system, real dependency
  metadata, explicit package discovery and a `hermes` console script.
  `pip install -e .` now produces a working `hermes` command.
- `hermes_cli.run()`: the console-script entrypoint. Returns a real exit code
  and treats Ctrl-C as a normal exit (130) instead of a traceback.
- `tests/test_packaging.py`: drift guards. The build fails when pyproject
  dependencies diverge from `requirements.txt`, when a top-level package is
  missing from the packaging list, when the console-script target stops being
  callable, or when VERSION/CHANGELOG fall behind pyproject.
- Test lanes declared in pyproject: `fast` (developer loop), `slow` (end-to-end
  and multi-process), `perf` (latency budgets).

### Changed
- pytest configuration moved from `pytest.ini` into
  `[tool.pytest.ini_options]`. Both files existing at once meant `pytest.ini`
  silently won and lane/marker config was lost.
- Version 0.1.0 to 0.2.0.

### Fixed
- `core/atomic_io.py`: file locking now works on Windows (`msvcrt`); the POSIX
  `fcntl` path is unchanged.
- `core/mcp_client.py`: `select()` on a subprocess pipe raised
  `WinError 10038` on Windows. Reads now go through a reader thread on
  `os.name == "nt"`.
- `core/profiles.py`: `ProfileManager` leaked a SQLite handle per profile, so
  deleting a profile failed with "database is locked". Memory facades are now
  cached per profile and closed on delete.
- `core/autonomy_preflight.py`: the approval store is threaded into approval
  request creation instead of being assumed.
- `core/computer/tools.py`: reports `dry_run` alongside the legacy
  `allow_dry_run` so honesty checks see the real capability.
- Launchers: `hermes.py` did not exist after the CLI was split into
  `hermus_cli/`, so `bin/hermes` pointed at nothing. Added `hermes.py`,
  `bin/hermes.cmd`, `bin/hermes-gateway.cmd`, and made the bash wrappers
  MSYS-safe (`cygpath -w`) so the Windows venv interpreter receives a native
  path.
- Test isolation: `tests/test_context_tiers.py` patched memory methods on the
  instance, so pytest restored them as instance attributes that permanently
  shadowed later class-level patches. That silently broke the delegation
  end-to-end test and the critical-config test. Patches now target the class.
- Test isolation: `tests/test_delegation.py` and `tests/test_gateway_realtime.py`
  mutated the config singleton at import time and leaked it; both now restore
  the original values, and `test_validation_reports_all_four_checks` sets the
  step budget it is asserting on.

### Known issues
- Ollama models whose chat template `/api/chat` does not understand fail with a
  500 while `/v1/chat/completions` works. The provider layer needs a
  conformance-tested adapter instead of one hardcoded endpoint.
- This project tree intermittently returns "path not found" to Windows
  processes for individual files, including the venv interpreter. Not
  antivirus (`C:/Users` is already excluded from Defender). Verify-after-write
  is the current workaround; a clean copy of the tree is the durable fix.
