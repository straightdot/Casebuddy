@echo off
REM Start casebuddy with no console window.
REM pythonw.exe is the windowed Python launcher: same interpreter, no cmd box.

setlocal
set "HERE=%~dp0"
set "PYW=E:\Python\pythonw.exe"

if not exist "%PYW%" (
  REM Fall back to whatever Python is on PATH.
  for %%I in (pythonw.exe) do set "PYW=%%~$PATH:I"
)
if not exist "%PYW%" (
  echo Could not find pythonw.exe. Edit run.bat and set PYW to your Python install.
  pause
  exit /b 1
)

start "" "%PYW%" "%HERE%casebuddy.py" %*
endlocal
