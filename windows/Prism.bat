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

if not "%~1"=="" goto convert

rem No files: open the app window in the default browser.
rem
rem pythonw has no console, so anything the app writes is thrown away and a
rem failure to start looks exactly like nothing happening: no window, no error,
rem nothing to report. Capture both streams, wait long enough for an immediate
rem failure to land, and judge by whether the server announced itself rather
rem than by whether stderr is empty, because a harmless warning is not a
rem failure.
set OUT=%TEMP%\prism-out.log
set ERR=%TEMP%\prism-err.log
if exist "%OUT%" del "%OUT%" >nul 2>nul
if exist "%ERR%" del "%ERR%" >nul 2>nul
start "" /B %PYW% "%GUI%" >"%OUT%" 2>"%ERR%"
rem ping is the portable sleep here: timeout fails when stdin is redirected.
ping -n 4 127.0.0.1 >nul 2>nul
findstr /C:"Prism running at" "%OUT%" >nul 2>nul && goto started

echo Prism could not start.
echo.
if exist "%ERR%" type "%ERR%"
if exist "%OUT%" type "%OUT%"
echo.
echo Please report this at:
echo   https://github.com/4bsxhwr68n-debug/prism/issues
echo.
pause
exit /b 1

:started
exit /b 0

:convert
%PY% "%SCRIPT%" --interactive %*
echo.
pause
