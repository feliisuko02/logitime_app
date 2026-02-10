"""
Logitime — Punto de entrada
Ventana nativa con pywebview. Si falla, abre el navegador.
"""

import sys
import os
import socket
import threading
import time
import logging
import traceback

# ── Rutas ──
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "logitime.log")

# ── Logging a archivo (crucial para --noconsole) ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("logitime")
logging.getLogger("werkzeug").setLevel(logging.ERROR)


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port):
    try:
        from app import app
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
    except Exception as e:
        log.error(f"Error arrancando Flask: {e}\n{traceback.format_exc()}")


def wait_for_server(port, timeout=15):
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.2)
    return False


def open_browser(url):
    """Fallback: abrir en navegador del sistema."""
    import webbrowser
    log.info(f"Abriendo en navegador: {url}")
    webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def main():
    log.info("=" * 50)
    log.info("Logitime iniciando...")
    log.info(f"Ejecutable: {sys.executable}")
    log.info(f"Base dir: {BASE_DIR}")
    log.info(f"Frozen: {getattr(sys, 'frozen', False)}")

    try:
        from database import init_db
        init_db()
        log.info("Base de datos inicializada")
    except Exception as e:
        log.error(f"Error init DB: {e}\n{traceback.format_exc()}")
        return

    port = find_free_port()
    url = f"http://127.0.0.1:{port}"
    log.info(f"Puerto: {port}")

    server_thread = threading.Thread(target=start_server, args=(port,), daemon=True)
    server_thread.start()

    if not wait_for_server(port):
        log.error("Flask no arranco a tiempo")
        return

    log.info("Flask arrancado correctamente")

    # ── Intentar pywebview ──
    try:
        import webview
        if not hasattr(webview, 'create_window'):
            raise ImportError("webview module found but not pywebview")
        log.info("pywebview cargado correctamente")

        window = webview.create_window(
            title="Logitime",
            url=url,
            width=1440,
            height=920,
            min_size=(1000, 600),
            text_select=True,
        )
        log.info("Ventana creada, iniciando GUI...")
        webview.start()
        log.info("Ventana cerrada")

    except ImportError:
        log.warning("pywebview no disponible, abriendo en navegador")
        open_browser(url)

    except Exception as e:
        log.warning(f"pywebview fallo ({e}), abriendo en navegador")
        open_browser(url)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error(f"Error fatal: {e}\n{traceback.format_exc()}")
