"""
Logitime — Servidor Flask v3
Novedades: Login con sesiones, admin endpoints, settings editables
"""

import os, sys, io, time, traceback, secrets
import numpy as np
import pandas as pd
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, send_file, abort, session
from flask.json.provider import DefaultJSONProvider
from database import (
    init_db, guardar_analisis, listar_analisis, obtener_analisis,
    eliminar_analisis, buscar_operarios, buscar_clientes,
    historico_operario, historico_cliente, dashboard_data, db_stats,
    comparar_analisis, obtener_historico_ops, exportar_analisis,
    ultimo_analisis, compactar_db,
    autenticar, crear_usuario, listar_usuarios, obtener_usuario,
    actualizar_usuario, eliminar_usuario,
    obtener_all_settings, guardar_all_settings,
    obtener_umbrales, obtener_margen_mantenimiento, obtener_score_weights,
)
from engine import analizar_excel, leer_excel


class NumpyJSONProvider(DefaultJSONProvider):
    def default(self, o):
        if isinstance(o, (np.integer,)): return int(o)
        if isinstance(o, (np.floating,)): return float(o)
        if isinstance(o, np.ndarray): return o.tolist()
        if isinstance(o, (np.bool_,)): return bool(o)
        return super().default(o)


if getattr(sys, 'frozen', False):
    STATIC_DIR = sys._MEIPASS
else:
    STATIC_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder=None)
app.json_provider_class = NumpyJSONProvider
app.json = NumpyJSONProvider(app)
app.secret_key = secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# ── Errores siempre JSON ──
@app.errorhandler(Exception)
def handle_exception(e):
    tb = traceback.format_exc()
    print(f"[ERROR] {e}\n{tb}")
    return jsonify({"error": str(e)}), 500

@app.errorhandler(404)
def handle_404(e):
    return jsonify({"error": "Ruta no encontrada"}), 404


# ── Auth helpers ──

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "No autenticado", "code": "AUTH_REQUIRED"}), 401
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "No autenticado", "code": "AUTH_REQUIRED"}), 401
        if session.get("rol") != "admin":
            return jsonify({"error": "Acceso denegado: se requiere rol admin"}), 403
        return f(*args, **kwargs)
    return decorated

def _uid():
    return session.get("user_id")

def _is_admin():
    return session.get("rol") == "admin"


# ── Paginas estaticas ──

@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


# ── Auth endpoints ──

@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or not password:
        return jsonify({"error": "Usuario y password requeridos"}), 400
    user = autenticar(username, password)
    if not user:
        return jsonify({"error": "Credenciales incorrectas"}), 401
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["nombre"] = user["nombre"]
    session["rol"] = user["rol"]
    return jsonify({"ok": True, "user": user})

@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})

@app.route("/api/auth/me")
def api_me():
    if "user_id" not in session:
        return jsonify({"authenticated": False}), 401
    return jsonify({
        "authenticated": True,
        "user": {"id": session["user_id"], "username": session["username"],
                 "nombre": session["nombre"], "rol": session["rol"]}
    })

@app.route("/api/auth/cambiar-password", methods=["POST"])
@login_required
def api_cambiar_password():
    data = request.get_json(silent=True) or {}
    new_pw = data.get("password", "")
    if len(new_pw) < 4:
        return jsonify({"error": "La password debe tener al menos 4 caracteres"}), 400
    actualizar_usuario(_uid(), password=new_pw)
    return jsonify({"ok": True})


# ── Admin: Usuarios ──

@app.route("/api/admin/usuarios")
@admin_required
def api_admin_listar_usuarios():
    return jsonify(listar_usuarios())

@app.route("/api/admin/usuarios", methods=["POST"])
@admin_required
def api_admin_crear_usuario():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    nombre = data.get("nombre", "").strip()
    rol = data.get("rol", "user")
    if not username or not password:
        return jsonify({"error": "Username y password requeridos"}), 400
    if len(password) < 4:
        return jsonify({"error": "Password minimo 4 caracteres"}), 400
    if rol not in ("admin", "user"):
        return jsonify({"error": "Rol debe ser 'admin' o 'user'"}), 400
    try:
        uid = crear_usuario(username, password, nombre, rol)
        return jsonify({"ok": True, "id": uid})
    except ValueError as e:
        return jsonify({"error": str(e)}), 409

@app.route("/api/admin/usuarios/<int:uid>", methods=["PUT"])
@admin_required
def api_admin_editar_usuario(uid):
    data = request.get_json(silent=True) or {}
    campos = {}
    if "nombre" in data: campos["nombre"] = data["nombre"]
    if "rol" in data and data["rol"] in ("admin", "user"): campos["rol"] = data["rol"]
    if "activo" in data: campos["activo"] = 1 if data["activo"] else 0
    if "password" in data and data["password"]:
        if len(data["password"]) < 4:
            return jsonify({"error": "Password minimo 4 caracteres"}), 400
        campos["password"] = data["password"]
    actualizar_usuario(uid, **campos)
    return jsonify({"ok": True})

@app.route("/api/admin/usuarios/<int:uid>", methods=["DELETE"])
@admin_required
def api_admin_eliminar_usuario(uid):
    if uid == _uid():
        return jsonify({"error": "No puedes desactivarte a ti mismo"}), 400
    eliminar_usuario(uid)
    return jsonify({"ok": True})


# ── Admin: Settings ──

@app.route("/api/admin/settings")
@admin_required
def api_admin_get_settings():
    return jsonify(obtener_all_settings())

@app.route("/api/admin/settings", methods=["POST"])
@admin_required
def api_admin_save_settings():
    data = request.get_json(silent=True) or {}
    guardar_all_settings(data)
    return jsonify({"ok": True, "settings": obtener_all_settings()})


# ── Analizar ──

@app.route("/api/analizar", methods=["POST"])
@login_required
def api_analizar():
    if "movimientos" not in request.files:
        return jsonify({"error": "Falta el archivo de movimientos"}), 400
    f = request.files["movimientos"]
    try:
        t0 = time.time()
        df_mov = leer_excel(io.BytesIO(f.read()))
        t_read = time.time() - t0

        df_mant = None
        if "mantenimiento" in request.files and request.files["mantenimiento"].filename:
            try:
                df_mant = pd.read_excel(io.BytesIO(request.files["mantenimiento"].read()), engine="openpyxl")
            except Exception:
                pass

        # Cargar settings actuales
        umbrales_cfg = obtener_umbrales()
        margen_mant = obtener_margen_mantenimiento()
        score_w = obtener_score_weights()

        hist_ops = obtener_historico_ops(_uid(), _is_admin())
        t0 = time.time()
        resultado = analizar_excel(
            df_mov, df_mant, archivo=f.filename, historico_ops=hist_ops,
            umbrales=umbrales_cfg, margen_mant=margen_mant, score_weights=score_w)
        t_analysis = time.time() - t0

        if resultado.get("error") and resultado["total_movimientos"] == 0:
            return jsonify(resultado), 400

        t0 = time.time()
        aid = guardar_analisis(resultado, user_id=_uid())
        t_db = time.time() - t0

        resp = resultado.copy()
        resp["id"] = aid
        resp["timing"] = {
            "lectura_excel": round(t_read, 3),
            "analisis": round(t_analysis, 3),
            "guardado_db": round(t_db, 3),
        }
        return jsonify(resp)

    except Exception as e:
        tb = traceback.format_exc()
        print(f"[ERROR analizar] {e}\n{tb}")
        return jsonify({"error": str(e)}), 500


# ── Consultas (filtradas por usuario) ──

@app.route("/api/analisis")
@login_required
def api_listar():
    return jsonify(listar_analisis(request.args.get("limit", 50, type=int), _uid(), _is_admin()))

@app.route("/api/analisis/<int:aid>")
@login_required
def api_detalle(aid):
    d = obtener_analisis(aid, _uid(), _is_admin())
    if not d: abort(404)
    return jsonify(d)

@app.route("/api/analisis/<int:aid>", methods=["DELETE"])
@login_required
def api_borrar(aid):
    eliminar_analisis(aid, _uid(), _is_admin())
    return jsonify({"ok": True})

@app.route("/api/ultimo")
@login_required
def api_ultimo():
    u = ultimo_analisis(_uid(), _is_admin())
    return jsonify(u if u else {})

@app.route("/api/comparar/<int:id_a>/<int:id_b>")
@login_required
def api_comparar(id_a, id_b):
    data = comparar_analisis(id_a, id_b, _uid(), _is_admin())
    if not data: abort(404)
    return jsonify(data)

@app.route("/api/exportar/<int:aid>")
@login_required
def api_exportar(aid):
    data = exportar_analisis(aid, _uid(), _is_admin())
    if not data: abort(404)
    output = io.BytesIO()
    info = data["info"]
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame([{"Fecha": info["fecha"], "Archivo": info.get("archivo",""),
            "Movimientos": info["total_movimientos"], "Anomalias": info["total_anomalias"],
            "Tasa %": info["tasa_anomalias_pct"]}]).to_excel(writer, sheet_name="Resumen", index=False)
        if data["operarios"]:
            cols = ["codigo","nombre","total_movimientos","total_anomalias",
                "tiempo_promedio_entre","duracion_promedio","total_unidades","productividad",
                "score_riesgo","tendencia"]
            df_ops = pd.DataFrame(data["operarios"])
            df_ops[[c for c in cols if c in df_ops.columns]].to_excel(writer, sheet_name="Operarios", index=False)
        if data["clientes"]:
            cols = ["nombre","total_movimientos","total_unidades","duracion_total","duracion_promedio","productividad"]
            df_cls = pd.DataFrame(data["clientes"])
            df_cls[[c for c in cols if c in df_cls.columns]].to_excel(writer, sheet_name="Clientes", index=False)
        if data["anomalias"]:
            cols = ["operario","operario_nombre","exceso","tiempo_entre","umbral","umbral_tipo",
                "tipo_operacion","fecha_fin_n","fecha_fin_n1","cliente","articulo"]
            df_an = pd.DataFrame(data["anomalias"])
            df_an[[c for c in cols if c in df_an.columns]].to_excel(writer, sheet_name="Anomalias", index=False)
    output.seek(0)
    fname = f"logitime_{info['fecha'][:10].replace('-','')}.xlsx"
    return send_file(output, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name=fname)

@app.route("/api/dashboard")
@login_required
def api_dashboard():
    return jsonify(dashboard_data(_uid(), _is_admin()))

@app.route("/api/operarios")
@login_required
def api_ops():
    return jsonify(buscar_operarios(request.args.get("q",""), _uid(), _is_admin()))

@app.route("/api/operarios/<codigo>/historico")
@login_required
def api_hist_op(codigo):
    return jsonify(historico_operario(codigo, 30, _uid(), _is_admin()))

@app.route("/api/clientes")
@login_required
def api_cls():
    return jsonify(buscar_clientes(request.args.get("q",""), _uid(), _is_admin()))

@app.route("/api/clientes/<path:nombre>/historico")
@login_required
def api_hist_cl(nombre):
    return jsonify(historico_cliente(nombre, 30, _uid(), _is_admin()))

@app.route("/api/db")
@login_required
def api_db():
    return jsonify(db_stats())

@app.route("/api/db/compactar", methods=["POST"])
@admin_required
def api_compactar():
    return jsonify(compactar_db())
