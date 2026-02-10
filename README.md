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
