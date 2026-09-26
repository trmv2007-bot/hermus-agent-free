@echo off
rem Windows wrapper for bin/hermes - uses the project venv, never ambient Python.
setlocal
set "ROOT=%~dp0.."
if exist "%ROOT%\.venv\Scripts\python.exe" (
  "%ROOT%\.venv\Scripts\python.exe" "%ROOT%\hermes.py" %*
) else (
  python "%ROOT%\hermes.py" %*
)
