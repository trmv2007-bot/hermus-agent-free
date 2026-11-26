@echo off
rem HERMUS on Windows: the same launcher bin/hermus is on the other platforms.
rem Run it from the install folder:  .\hermus  (or  .\hermus undo )
rem
rem Control flow uses labels rather than blocks: cmd.exe expands %ERRORLEVEL%
rem while parsing a whole if-block, so a status read inside one is the parent's
rem status, not the command's. Every exit happens after its block has ended.
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"

if /I "%~1"=="bootstrap" goto bootstrap
if not exist "%PY%" goto install
goto run

:bootstrap
rem Setup and repair go through the live bootstrap wrapper, so a download is
rem visible instead of looking like a hang. Same rule as bin/hermus.
shift
if exist "%PY%" (
  "%PY%" "%ROOT%bootstrap_live.py" %*
) else (
  py -3 "%ROOT%bootstrap_live.py" %*
)
goto status

:install
rem `hermus` on its own installs when the runtime is missing. Without this the
rem import of pydantic would fail on a clean Python and the room would never open.
echo Setting Hermus up. This takes a few minutes, and running it again is safe.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%setup.ps1"
if errorlevel 1 goto install_failed
goto run

:install_failed
echo Hermus could not be installed. The installer above says why.
exit /b 1

:run
if exist "%PY%" (
  "%PY%" "%ROOT%hermus.py" %*
) else (
  py -3 "%ROOT%hermus.py" %*
)

:status
exit /b %ERRORLEVEL%
