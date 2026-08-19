@echo off
REM CaseBuddy one-shot setup. RUN THIS AS ADMINISTRATOR.
REM   1. Installs the Python dependencies (Pillow).
REM   2. Registers CaseBuddy to start at logon (Scheduled Task).
REM   3. Starts it now.
REM
REM LibreHardwareMonitor is NOT installed by this script. Download it from
REM https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases,
REM run it as Administrator, and enable Options -> Remote Web Server.

setlocal
set "HERE=%~dp0"

net session >nul 2>&1
if errorlevel 1 (
  echo This script needs Administrator rights.
  echo Right-click setup.bat and choose "Run as administrator".
  pause
  exit /b 1
)

REM --- find Python -----------------------------------------------------------
set "PY="
for %%I in (python.exe) do set "PY=%%~$PATH:I"
if not defined PY (
  echo Python was not found on PATH. Install Python 3.10+ from python.org
  echo with "Add python.exe to PATH" ticked, then run this again.
  pause
  exit /b 1
)
for %%I in ("%PY%") do set "PYW=%%~dpIpythonw.exe"
if not exist "%PYW%" set "PYW=%PY%"

echo.
echo Installing dependencies...
"%PY%" -m pip install --quiet -r "%HERE%requirements.txt"
if errorlevel 1 (
  echo pip install failed. Check your internet connection and try again.
  pause
  exit /b 1
)

echo Registering the logon task...
schtasks /Create /F /TN "CaseBuddy" /SC ONLOGON /RL LIMITED ^
  /TR "\"%PYW%\" \"%HERE%casebuddy.py\"" >nul
if errorlevel 1 (
  echo Could not create the scheduled task.
  pause
  exit /b 1
)

echo Starting CaseBuddy...
start "" /D "%HERE%" "%PYW%" "%HERE%casebuddy.py"

echo.
echo Done. CaseBuddy is running and will start automatically at logon.
echo To undo the autostart later:  schtasks /Delete /F /TN "CaseBuddy"
pause
endlocal
