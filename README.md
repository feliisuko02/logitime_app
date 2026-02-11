# Logitime

Análisis de picking y anomalías de almacén.

## Crear el instalador (Setup_Logitime.exe)

### Requisitos (solo en tu PC, una vez):
1. **Python** → https://www.python.org/downloads/ (marca "Add Python to PATH")
2. **Inno Setup 6** → https://jrsoftware.org/isdl.php (instalar con opciones por defecto)

### Compilar:
1. Doble clic en **`COMPILAR_INSTALADOR.bat`**
2. Espera 2-5 minutos
3. Se genera **`Output\Setup_Logitime.exe`**

Ese archivo lo envías a cualquier PC Windows. Doble clic → instala como cualquier programa
(acceso directo en escritorio, barra de tareas, desinstalar desde Panel de Control).

## Desarrollo (sin compilar)

Doble clic en **`INICIAR.bat`** para ejecutar desde código fuente.

## Archivos

| Archivo | Descripción |
|---------|-------------|
| `main.py` | Punto de entrada (ventana nativa pywebview) |
| `app.py` | Servidor Flask (API REST) |
| `engine.py` | Motor de análisis vectorizado (pandas/numpy) |
| `database.py` | Base de datos SQLite con batch inserts |
| `index.html` | Interfaz web |
| `INICIAR.bat` | Ejecutar desde código fuente |
| `COMPILAR_INSTALADOR.bat` | Generar Setup_Logitime.exe |
| `installer/logitime.iss` | Script de Inno Setup |
| `assets/` | Icono y gráficos del instalador |


## Probar la nueva versión (multi-almacén y permisos)

### 1) Validación automática (rápida)
```bash
python tools/smoke_test.py
```
Si todo está bien, verás algo como:
```json
{"ok": true, "checks": 7}
```

### 2) Prueba manual en la interfaz
1. Ejecuta la app (`INICIAR.bat` en Windows o `python main.py`).
2. Inicia sesión con:
   - usuario: `admin`
   - contraseña: `admin123`
3. Ve a **Usuarios** y crea:
   - un almacén nuevo,
   - un proveedor nuevo,
   - un usuario con permisos limitados.
4. Inicia sesión con el usuario limitado y verifica:
   - que solo ve los apartados permitidos,
   - que no puede administrar usuarios/ajustes,
   - que en clientes solo aparezcan los proveedores asignados.

### 3) Comprobaciones de backend útiles
```bash
python -m py_compile app.py database.py main.py engine.py
```

