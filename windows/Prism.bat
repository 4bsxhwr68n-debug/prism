@echo off
rem Prism (Windows) - Retarget any 3MF. Blend any colour.
rem Double-click to open the app window. Or drag .3mf files onto this file to
rem go straight to the console journey.
setlocal
set PYTHONUTF8=1
set DIR=%~dp0
set SCRIPT=%DIR%engine\optimise3mf.py
set GUI=%DIR%engine\gui.py

set PY=
set PYW=
py -3 -c "print()" >nul 2>nul && (set PY=py -3& set PYW=pyw -3)
if not defined PY python -c "print()" >nul 2>nul && (set PY=python& set PYW=pythonw)
if not defined PY (
    echo Python 3 is required but was not found.
    echo Install it from https://www.python.org/downloads/ or the Microsoft
    echo Store ^(no extra packages needed^), then run this again.
    echo.
    pause
    exit /b 1
)

if "%~1"=="" (
    rem No files: open the app window in the default browser.
    start "" %PYW% "%GUI%"
    exit /b 0
)

%PY% "%SCRIPT%" --interactive %*
echo.
pause
