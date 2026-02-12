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
set "INVALID_DIST=0"
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
call :detect_invalid_pandas
if %INVALID_DIST% gtr 0 (
  echo   [!] Se detectaron residuos previos de ~andas*.
  call :repair_invalid_pandas
)

echo.
echo   Instalando dependencias...
echo.

call :run_pip "flask, pandas, numpy, openpyxl, werkzeug" "%PY% -m pip install --disable-pip-version-check --no-input flask pandas numpy openpyxl werkzeug --upgrade"
call :run_pip "pywebview" "%PY% -m pip install --disable-pip-version-check --no-input pywebview --upgrade"
if errorlevel 1 (
  echo   [!] Primer intento pywebview fallo. Reintentando con --user...
  call :run_pip "pywebview (--user)" "%PY% -m pip install --disable-pip-version-check --no-input pywebview --upgrade --user"
)
if errorlevel 1 (
  echo   [!] Segundo intento pywebview fallo. Reintentando con pip directo...
  call :run_pip "pywebview (pip directo)" "pip install pywebview --upgrade"
)
if errorlevel 1 (
  echo   [X] No se pudo instalar pywebview tras 3 intentos.
  set /a FAILS+=1
)

if %INVALID_DIST% gtr 0 (
  echo.
  echo   [!] Se detecto "invalid distribution" durante la instalacion.
  call :repair_invalid_pandas
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
call :check_py "Diagnostics endpoint" "from app import app; c=app.test_client(); c.post('/api/auth/login', json={'username':'admin','password':'admin123'}); r=c.get('/api/diagnostics/environment'); import sys; sys.exit(0 if r.status_code==200 else 1)"

echo.
echo   ========================================
echo   RESUMEN DIAGNOSTICO
echo   ========================================
if %FAILS% gtr 0 (
  color 0C
  echo   [X] Fallos detectados: %FAILS%
  echo   [!] Revisa los mensajes [X] y corrige antes de iniciar Logitime.
  if exist "%TMP_LOG%" echo   [i] Ultimo log pip: %TMP_LOG%
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
  echo       - Si aparece ^"Ignoring invalid distribution ~andas^", se intentara reparar automaticamente.
  exit /b 1
)

type "%TMP_LOG%" | findstr /i "Ignoring invalid distribution" >nul
if not errorlevel 1 (
  echo   [!] Advertencia detectada en pip (invalid distribution).
  set /a WARNS+=1
  set "INVALID_DIST=1"
)

echo   [OK] %_label%
exit /b 0

:detect_invalid_pandas
set "INVALID_DIST=0"
for /f %%I in ('%PY% -c "import os,site,glob; p=[]; [p.extend(glob.glob(os.path.join(x,'~andas*'))) for x in site.getsitepackages() if os.path.isdir(x)]; up=site.getusersitepackages(); p.extend(glob.glob(os.path.join(up,'~andas*')) if os.path.isdir(up) else []); print(len([x for x in p if os.path.exists(x)]))"') do set "INVALID_DIST=%%I"
if not defined INVALID_DIST set "INVALID_DIST=0"
if %INVALID_DIST% gtr 0 (
  echo   [!] Detectadas %INVALID_DIST% rutas invalidas de pandas (~andas*).
) else (
  echo   [OK] No se detectaron residuos ~andas previos.
)
exit /b 0

:repair_invalid_pandas
echo   [i] Intentando reparar paquetes residuales de pandas (~andas)...
%PY% -c "import os,site,glob,shutil; paths=[]; [paths.extend(glob.glob(os.path.join(p,'~andas*'))) for p in site.getsitepackages() if os.path.isdir(p)]; up=site.getusersitepackages(); paths.extend(glob.glob(os.path.join(up,'~andas*')) if os.path.isdir(up) else []); alive=[x for x in paths if os.path.exists(x)]; print('RUTAS_CONFLICTO:', len(alive)); [print(' -',x) for x in alive]; [shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p) for p in alive]" > "%TMP_LOG%" 2>&1
if errorlevel 1 (
  echo   [!] No se pudo limpiar automaticamente. Continua con advertencia.
  type "%TMP_LOG%"
  set /a WARNS+=1
  exit /b 0
)

type "%TMP_LOG%"
%PY% -m pip install --disable-pip-version-check --no-input --upgrade --force-reinstall pandas > "%TMP_LOG%" 2>&1
if errorlevel 1 (
  echo   [!] Reinstalacion de pandas fallo. Revisa el log:
  echo       %TMP_LOG%
  set /a WARNS+=1
  exit /b 0
)

call :detect_invalid_pandas
if %INVALID_DIST% gtr 0 (
  echo   [!] Persisten rutas ~andas*. Borrado manual recomendado en el path mostrado arriba.
  set /a WARNS+=1
) else (
  echo   [OK] Reparacion de pandas completada.
)
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
