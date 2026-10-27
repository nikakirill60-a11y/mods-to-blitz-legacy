@echo off
rem scgtool - console tool for WoT Blitz .sc2/.scg/.dvpl.
rem Drag a .sc2 / .scg / .dvpl file onto this icon to see a summary (info).
rem Without arguments it opens the 3D viewer. Commands: info tree mats obj json unpack
chcp 65001 >nul
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0scgtool.py" %*
) else (
    python "%~dp0scgtool.py" %*
)
pause
