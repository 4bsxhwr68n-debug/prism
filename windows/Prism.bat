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
rem pythonw has no console, so anything it writes is discarded and a failure to
rem start looks exactly like nothing happening: no window, no error, nothing to
rem report. So check first with the console interpreter, which CAN report, and
rem only then launch detached. Importing gui runs no server, it just proves the
rem interpreter can load the app.
rem
rem Do not be tempted to capture the real launch with `start /B ... >log`:
rem cmd then stays attached for the life of the app, so the console window sits
rem open until Prism exits. CI caught exactly that.
set ERR=%TEMP%\prism-start.log
if exist "%ERR%" del "%ERR%" >nul 2>nul
%PY% -c "import sys; sys.path.insert(0, r'%DIR%engine'); import gui" 2>"%ERR%"
if errorlevel 1 goto failed
start "" %PYW% "%GUI%"
exit /b 0

:failed
echo Prism could not start. Python reported:
echo.
if exist "%ERR%" type "%ERR%"
echo.
echo Please report this at:
echo   https://github.com/4bsxhwr68n-debug/prism/issues
echo.
pause
exit /b 1

:convert
%PY% "%SCRIPT%" --interactive %*
echo.
pause
