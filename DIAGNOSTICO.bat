@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Logitime - Diagnostico
color 0E

echo.
echo   ========================================
echo     DIAGNOSTICO LOGITIME
echo   ========================================
echo.

cd /d "%~dp0"
set "FAILS=0"
set "WARNS=0"
set "TMP_LOG=%TEMP%\logitime_diag_pip_%RANDOM%.log"

where python >nul 2>&1 && (set "PY=python" & goto :found)
where python3 >nul 2>&1 && (set "PY=python3" & goto :found)
where py >nul 2>&1 && (set "PY=py" & goto :found)
echo   [X] Python no encontrado en PATH.
echo       Instala Python 3.11+ y marca "Add python.exe to PATH".
pause
exit /b 1

:found
echo   [OK] Python: %PY%
%PY% --version || (
  echo   [X] No se pudo ejecutar %PY% --version
  pause
  exit /b 1
)
echo.

echo   Instalando dependencias...
echo.

call :run_pip "flask, pandas, numpy, openpyxl, werkzeug" "%PY% -m pip install flask pandas numpy openpyxl werkzeug --upgrade"
call :run_pip "pywebview" "%PY% -m pip install pywebview --upgrade"
if errorlevel 1 (
  echo   [!] Primer intento pywebview fallo. Reintentando con --user...
  call :run_pip "pywebview (--user)" "%PY% -m pip install pywebview --upgrade --user"
)
if errorlevel 1 (
  echo   [!] Segundo intento pywebview fallo. Reintentando con pip directo...
  call :run_pip "pywebview (pip directo)" "pip install pywebview --upgrade"
)
if errorlevel 1 (
  echo   [X] No se pudo instalar pywebview tras 3 intentos.
  set /a FAILS+=1
)

echo.
echo   ========================================
echo   Comprobando dependencias:
echo   ========================================
call :check_py "Flask" "import flask; print(flask.__version__)"
call :check_py "Pandas" "import pandas; print(pandas.__version__)"
call :check_py "Numpy" "import numpy; print(numpy.__version__)"
call :check_py "Openpyxl" "import openpyxl; print(openpyxl.__version__)"
call :check_py "Pywebview" "import webview; print('create_window='+str(hasattr(webview,'create_window')))"
echo.

echo   Verificando sintaxis de codigo:
%PY% -m py_compile app.py database.py main.py engine.py tools\smoke_test.py 2>&1
if errorlevel 1 (
    echo.
    echo   [X] Hay errores de sintaxis.
    echo       Sugerencia: ejecuta ^"%PY% -m py_compile app.py^" para ver el primer fallo puntual.
    set /a FAILS+=1
) else (
    echo   [OK] Sintaxis Python
)
echo.

echo   Probando componentes:
call :check_py "Base de datos" "from database import init_db; init_db(); print('ok')"
call :check_py "Motor de analisis" "from engine import analizar_excel; print('ok')"
call :check_py "Flask app" "from app import app; print('ok')"
call :check_py "Diagnostics endpoint" "from app import app; c=app.test_client(); c.post('/api/auth/login', json={'username':'admin','password':'admin123'}); r=c.get('/api/diagnostics/environment'); print(r.status_code); import sys; sys.exit(0 if r.status_code==200 else 1)"
echo.

echo   ========================================
echo   RESUMEN DIAGNOSTICO
echo   ========================================
if %FAILS% gtr 0 (
    color 0C
    echo   [X] Fallos detectados: %FAILS%
    echo   [!] Revisa los mensajes [X] y corrige antes de iniciar Logitime.
    if exist "%TMP_LOG%" (
        echo   [i] Ultimo log pip: %TMP_LOG%
    )
    echo.
    pause
    exit /b 1
)

color 0A
echo   [OK] Sin fallos bloqueantes.
if %WARNS% gtr 0 echo   [!] Advertencias detectadas: %WARNS%
echo.
echo   ========================================
echo   Iniciando Logitime...
echo   ========================================
echo.

%PY% main.py

echo.
echo   Logitime se cerro.
pause
exit /b 0

:run_pip
set "_label=%~1"
set "_cmd=%~2"
echo   --- %_label% ---
cmd /c "%_cmd%" > "%TMP_LOG%" 2>&1
if errorlevel 1 (
  echo   [X] Error instalando %_label%
  type "%TMP_LOG%"
  echo   [i] Sugerencias rapidas:
  echo       - Actualiza pip: %PY% -m pip install --upgrade pip
  echo       - Si aparece ^"Ignoring invalid distribution ~andas^", reinstala pandas:
  echo         %PY% -m pip uninstall -y pandas ^&^& %PY% -m pip install pandas
  exit /b 1
)

type "%TMP_LOG%" | findstr /i "Ignoring invalid distribution" >nul
if not errorlevel 1 (
  echo   [!] Advertencia detectada en pip (invalid distribution).
  set /a WARNS+=1
)

echo   [OK] %_label%
exit /b 0

:check_py
set "_label=%~1"
set "_code=%~2"
%PY% -c "%_code%" >nul 2>&1
if errorlevel 1 (
  echo   [X] %_label%
  set /a FAILS+=1
) else (
  echo   [OK] %_label%
)
exit /b 0
