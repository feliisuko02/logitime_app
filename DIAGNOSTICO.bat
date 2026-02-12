@echo off
title Logitime - Diagnostico
color 0E
echo.
echo   ========================================
echo     DIAGNOSTICO LOGITIME
echo   ========================================
echo.

cd /d "%~dp0"

where python >nul 2>&1 && (set PY=python& goto :found)
where python3 >nul 2>&1 && (set PY=python3& goto :found)
where py >nul 2>&1 && (set PY=py& goto :found)
echo   [X] Python no encontrado
pause & exit /b 1

:found
echo   [OK] Python: %PY%
%PY% --version
echo.

echo   Instalando dependencias...
echo.
echo   --- flask, pandas, numpy, openpyxl ---
%PY% -m pip install flask pandas numpy openpyxl werkzeug --quiet --upgrade
echo.
echo   --- pywebview ---
echo   (si falla aqui, mostrara el error)
%PY% -m pip install pywebview --upgrade
if %errorlevel% neq 0 (
    echo.
    echo   Reintentando con --user...
    %PY% -m pip install pywebview --upgrade --user
)
if %errorlevel% neq 0 (
    echo.
    echo   Reintentando con pip directo...
    pip install pywebview --upgrade
)
echo.

echo   ========================================
echo   Comprobando dependencias:
echo   ========================================
%PY% -c "import flask; print('   [OK] Flask', flask.__version__)" 2>nul || echo    [X] Flask
%PY% -c "import pandas; print('   [OK] Pandas', pandas.__version__)" 2>nul || echo    [X] Pandas
%PY% -c "import openpyxl; print('   [OK] Openpyxl', openpyxl.__version__)" 2>nul || echo    [X] Openpyxl
%PY% -c "import webview; print('   [OK] Pywebview'); print('       create_window:', hasattr(webview,'create_window'))" 2>nul || echo    [X] Pywebview
echo.

echo   Verificando sintaxis de codigo:
%PY% -m py_compile app.py database.py main.py engine.py tools\smoke_test.py 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   [X] Hay errores de sintaxis. Corrigelos antes de iniciar.
    pause
    exit /b 1
)
echo   [OK] Sintaxis Python
echo.

echo   Probando componentes:
%PY% -c "from database import init_db; init_db(); print('   [OK] Base de datos')" 2>&1
%PY% -c "from engine import analizar_excel; print('   [OK] Motor de analisis')" 2>&1
%PY% -c "from app import app; print('   [OK] Flask app')" 2>&1
%PY% -c "from app import app; c=app.test_client(); r=c.post('/api/auth/login', json={'username':'admin','password':'admin123'}); d=c.get('/api/diagnostics/environment'); print('   [OK] Diagnostics endpoint' if d.status_code==200 else f'   [X] Diagnostics {d.status_code}')" 2>&1
echo.

echo   ========================================
echo   Iniciando Logitime...
echo   ========================================
echo.

%PY% main.py

echo.
echo   Logitime se cerro.
pause
