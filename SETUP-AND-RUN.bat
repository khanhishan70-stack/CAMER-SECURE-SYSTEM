@echo off
setlocal EnableExtensions
title CAMER SECURE SYSTEM - One-Click Setup
cd /d "%~dp0"

echo ============================================================
echo   CAMER SECURE SYSTEM - one-click setup
echo   Installs everything automatically, then launches the app.
echo ============================================================
echo.

set "ROOT=%~dp0"
set "APP=%ROOT%SecureCam-win32-x64\SecureCam.exe"
if not exist "%APP%" set "APP=%ROOT%SecureCam.exe"
if not exist "%APP%" goto noapp
for %%F in ("%APP%") do set "APPDIR=%%~dpF"
set "REQ=%ROOT%requirements.txt"
goto step1

:noapp
echo [ERROR] Could not find SecureCam.exe next to this file.
echo Keep this .bat inside the extracted download folder and re-run.
pause
exit /b 1

:step1
echo [1/4] Looking for Python 3.12 ...
set "PY="
if exist "C:\Program Files\Python312\python.exe" set PY="C:\Program Files\Python312\python.exe"
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined PY goto pyfound
py -3.12 --version >nul 2>nul
if not errorlevel 1 set "PY=py -3.12"
if defined PY goto pyfound
python --version 2>nul | findstr /r /c:"^Python 3\.12\." >nul
if not errorlevel 1 set "PY=python"
if defined PY goto pyfound
goto installpy

:installpy
echo Python 3.12 not found - installing it now, automatic ...
where winget >nul 2>nul
if errorlevel 1 goto manualpy
echo Installing Python 3.12 via winget ...
winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
goto checkfresh

:manualpy
echo Downloading the Python 3.12 installer ...
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile \"$env:TEMP\python312.exe\""
echo Installing Python for this Windows user only, no admin needed ...
"%TEMP%\python312.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
del "%TEMP%\python312.exe" >nul 2>nul

:checkfresh
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist "C:\Program Files\Python312\python.exe" set PY="C:\Program Files\Python312\python.exe"
if not defined PY goto pyfail

:pyfound
echo Using: %PY%
call %PY% --version
goto step2

:pyfail
echo.
echo [ERROR] Python did not get installed. Easiest fix:
echo   1. Install Python 3.12 from https://www.python.org/downloads/
echo      tick Add python.exe to PATH on the first screen
echo   2. Re-run this file.
pause
exit /b 1

:step2
echo.
echo [2/4] Installing the AI brain packages - one time, a few minutes ...
call %PY% -m pip install --upgrade pip
if not exist "%REQ%" goto skipreq
call %PY% -m pip install -r "%REQ%"
if errorlevel 1 goto pipfail
goto shortcut

:skipreq
echo [WARN] requirements.txt not found next to this file - skipping packages.
goto shortcut

:pipfail
echo.
echo [ERROR] Package install failed. Check your internet and re-run this file.
pause
exit /b 1

:shortcut
echo.
echo [3/4] Creating the Desktop icon ...
set "SCRT=%TEMP%\camer_shortcut.ps1"
> "%SCRT%" echo $w = New-Object -ComObject WScript.Shell
>>"%SCRT%" echo $d = $w.SpecialFolders("Desktop")
>>"%SCRT%" echo $s = $w.CreateShortcut("$d\CAMER SECURE SYSTEM.lnk")
>>"%SCRT%" echo $s.TargetPath = "%APP%"
>>"%SCRT%" echo $s.WorkingDirectory = "%APPDIR%"
>>"%SCRT%" echo $s.Save()
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRT%"
del "%SCRT%" >nul 2>nul
goto launch

:launch
echo.
echo [4/4] Launching CAMER SECURE SYSTEM ...
start "" "%APP%"
echo.
echo ============================================================
echo   DONE! The app is starting now.
echo   First start takes about 30 seconds - the face-AI model
echo   downloads itself once, so keep the internet on.
echo.
echo   HOW TO LAUNCH NEXT TIME - no setup needed:
echo     - Double-click the Desktop icon CAMER SECURE SYSTEM
echo     - or double-click SecureCam.exe in this folder
echo.
echo   HOW TO ACTIVATE - inside the app:
echo     1. Register your face - Capture from camera or Upload
echo     2. Set your lock screen password
echo     3. Switch Security Mode ON - guarding starts
echo.
echo   Full illustrated guide:
echo   https://khanhishan70-stack.github.io/CAMER-SECURE-SYSTEM/
echo ============================================================
pause
