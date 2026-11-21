"""Packaged data for policies/ — shipped with the app so a wheel install resolves it.

Hermus reads this tree relative to `config.base_dir` (the repository root in a
checkout, the install root when pip-installed). Marking it as a package is what
lets setuptools carry its non-Python files into the wheel.
"""
