@echo off
rem HERMUS on Windows: the same launcher bin/hermus is on the other platforms.
rem Run it from the install folder:  .\hermus  (or  .\hermus undo )
setlocal
set "ROOT=%~dp0"
if exist "%ROOT%.venv\Scripts\python.exe" (
  "%ROOT%.venv\Scripts\python.exe" "%ROOT%hermus.py" %*
) else (
  py -3 "%ROOT%hermus.py" %*
)
exit /b %ERRORLEVEL%
