@echo off
title Logitime
cd /d "%~dp0"

where python >nul 2>&1 && (set PY=python& goto :found)
where python3 >nul 2>&1 && (set PY=python3& goto :found)
where py >nul 2>&1 && (set PY=py& goto :found)
echo Python no encontrado. & pause & exit /b 1

:found
%PY% -m pip install flask pandas numpy openpyxl werkzeug pywebview --quiet --upgrade 2>nul
if %errorlevel% neq 0 %PY% -m pip install flask pandas numpy openpyxl werkzeug pywebview --quiet --user --upgrade 2>nul
%PY% main.py
if %errorlevel% neq 0 pause
