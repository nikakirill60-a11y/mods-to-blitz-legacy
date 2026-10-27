@echo off
rem SCG/SC2 Viewer - WoT Blitz .sc2/.scg/.dvpl models.
rem Double-click: empty viewer window. Drop a model file or a folder onto this icon to open it.
rem Also works as "Open with..." for .sc2 / .scg / .dvpl files.
rem Needs Python 3 (python.org). Without Python the HTML viewer opens in the browser instead.
where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 "%~dp0SCGViewer.pyw" %*
    exit /b
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0SCGViewer.pyw" %*
    exit /b
)
start "" "%~dp0scgviewer.html"
