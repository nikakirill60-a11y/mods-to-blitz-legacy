@echo off
rem resviewer - view game files without unpacking.
rem Drag a file (resources.assets.resS, resources.assets, .pck, .exe) onto this icon,
rem or double-click it and press the open button.
chcp 65001 >nul
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0resviewer.py" %*
) else (
    python "%~dp0resviewer.py" %*
)
if errorlevel 1 pause
