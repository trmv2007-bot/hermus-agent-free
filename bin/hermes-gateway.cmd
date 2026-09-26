@echo off
rem Windows wrapper for bin/hermes-gateway - starts the live gateway.
setlocal
set "ROOT=%~dp0.."
if "%~1"=="" (set "PORT=8000") else (set "PORT=%~1")
if exist "%ROOT%\.venv\Scripts\python.exe" (
  "%ROOT%\.venv\Scripts\python.exe" "%ROOT%\hermes.py" gateway start --port %PORT%
) else (
  python "%ROOT%\hermes.py" gateway start --port %PORT%
)
