@echo off
rem HERMUS on Windows: the same launcher bin/hermus is on the other platforms.
rem Run it from the install folder:  .\hermus  (or  .\hermus undo )
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"

rem Setup and repair go through the live bootstrap wrapper, so a download is
rem visible instead of looking like a hang. Same rule as bin/hermus.
if /I "%~1"=="bootstrap" (
  shift
  if exist "%PY%" (
    "%PY%" "%ROOT%bootstrap_live.py" %*
  ) else (
    py -3 "%ROOT%bootstrap_live.py" %*
  )
  exit /b %ERRORLEVEL%
)

rem `hermus` on its own installs when the runtime is missing. Without this the
rem import of pydantic would fail on a clean Python and the room would never open.
if not exist "%PY%" (
  echo Setting Hermus up. This takes a few minutes, and running it again is safe.
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%setup.ps1"
  if errorlevel 1 (
    echo Hermus could not be installed. The installer above says why.
    exit /b 1
  )
)

if exist "%PY%" (
  "%PY%" "%ROOT%hermus.py" %*
) else (
  py -3 "%ROOT%hermus.py" %*
)
exit /b %ERRORLEVEL%
