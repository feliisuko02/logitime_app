@echo off
setlocal enabledelayedexpansion
title Compilar Instalador Logitime
color 0A
echo.
echo   ========================================
echo     COMPILAR INSTALADOR LOGITIME
echo     Resultado: Output\Setup_Logitime.exe
echo   ========================================
echo.

cd /d "%~dp0"

:: ========================================
:: PASO 1: Buscar Python
:: ========================================
echo   [1/5] Buscando Python...

set PY=
where python >nul 2>&1 && (set PY=python& goto :py_ok)
where python3 >nul 2>&1 && (set PY=python3& goto :py_ok)
where py >nul 2>&1 && (set PY=py& goto :py_ok)

echo.
echo   [!] Python no encontrado.
echo   Descargalo de: https://www.python.org/downloads/
echo   IMPORTANTE: Marca "Add Python to PATH" al instalar.
pause
exit /b 1

:py_ok
for /f "tokens=*" %%v in ('%PY% --version 2^>^&1') do set PYVER=%%v
echo         %PYVER%

:: ========================================
:: PASO 2: Instalar dependencias
:: ========================================
echo   [2/5] Instalando dependencias Python...
%PY% -m pip install --upgrade pip --quiet 2>nul
%PY% -m pip install flask pandas numpy openpyxl pywebview pyinstaller werkzeug --quiet --upgrade 2>nul
if %errorlevel% neq 0 (
    echo         Reintentando con --user...
    %PY% -m pip install flask pandas numpy openpyxl pywebview pyinstaller werkzeug --quiet --user --upgrade 2>nul
)
echo         OK

:: ========================================
:: PASO 3: Compilar .exe con PyInstaller
:: ========================================
echo   [3/5] Compilando aplicacion con PyInstaller...
echo         Esto tarda 2-5 minutos la primera vez...
echo.

if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "Logitime.spec" del "Logitime.spec"

set "ICON_ARG="
set "DATA_HTML=--add-data index.html;."
set "DATA_ICON="

if exist "assets\icon.ico" (
    set "ICON_ARG=--icon assets\icon.ico"
) else (
    echo         [WARN] No se encontro assets\icon.ico ^(se compila sin icono .ico^)
)

if exist "assets\icon.png" (
    set "DATA_ICON=--add-data assets\icon.png;assets"
) else (
    echo         [WARN] No se encontro assets\icon.png ^(se omite recurso opcional^)
)

%PY% -m PyInstaller --onefile --noconsole --name Logitime !ICON_ARG! !DATA_HTML! !DATA_ICON! --collect-all webview --hidden-import webview --hidden-import bottle --hidden-import cryptography main.py

if not exist "dist\Logitime.exe" (
    echo.
    echo   [ERROR] PyInstaller fallo.
    echo   Revisa los errores arriba.
    echo.
    echo   Truco: si falla por webview, prueba a ejecutar
    echo   primero: %PY% main.py
    echo   para ver si funciona desde codigo fuente.
    pause
    exit /b 1
)

echo.
echo         Logitime.exe creado correctamente
echo.

:: ========================================
:: PASO 4: Buscar Inno Setup
:: ========================================
echo   [4/5] Buscando Inno Setup...

set ISCC=
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
    set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    goto :inno_ok
)
if exist "C:\Program Files\Inno Setup 6\ISCC.exe" (
    set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
    goto :inno_ok
)
where iscc >nul 2>&1 && (
    for /f "tokens=*" %%i in ('where iscc') do set "ISCC=%%i"
    goto :inno_ok
)

echo.
echo   [!] Inno Setup no encontrado.
echo   Para crear el instalador necesitas Inno Setup 6:
echo   https://jrsoftware.org/isdl.php
echo.
echo   El .exe ya esta en: dist\Logitime.exe
echo   Puedes usarlo directamente sin instalador.
pause
exit /b 0

:inno_ok
echo         Encontrado

:: ========================================
:: PASO 5: Crear instalador
:: ========================================
echo   [5/5] Creando instalador...

if not exist "installer" mkdir "installer"
if not exist "installer\logitime.iss" (
    echo   [ERROR] Falta installer\logitime.iss
    echo   Este archivo define como generar Setup_Logitime.exe
    pause
    exit /b 1
)

if not exist "Output" mkdir "Output"

"%ISCC%" "installer\logitime.iss"

if not exist "Output\Setup_Logitime.exe" (
    echo.
    echo   [ERROR] Inno Setup fallo.
    echo   ISCC: %ISCC%
    echo   Script: installer\logitime.iss
    pause
    exit /b 1
)

if exist "build" rmdir /s /q "build"
if exist "Logitime.spec" del "Logitime.spec"

echo.
echo   ========================================
echo.
echo     INSTALADOR CREADO:
echo     Output\Setup_Logitime.exe
echo.
echo     Envia ese archivo a cualquier PC Windows.
echo     Doble clic para instalar.
echo.
echo   ========================================
echo.

explorer "Output"
pause
