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
    actualizar_usuario, eliminar_usuario, listar_almacenes, crear_almacen, listar_proveedores, crear_proveedor,
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
        if session.get("rol") not in ("admin", "superadmin"):
            return jsonify({"error": "Acceso denegado: se requiere rol admin"}), 403
        return f(*args, **kwargs)
    return decorated

def _uid():
    return session.get("user_id")

def superadmin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "No autenticado", "code": "AUTH_REQUIRED"}), 401
        if session.get("rol") != "superadmin":
            return jsonify({"error": "Acceso denegado: se requiere rol superadmin"}), 403
        return f(*args, **kwargs)
    return decorated

def permiso_required(nombre):
    def outer(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if "user_id" not in session:
                return jsonify({"error": "No autenticado", "code": "AUTH_REQUIRED"}), 401
            if not _can(nombre):
                return jsonify({"error": f"No tienes permiso para '{nombre}'"}), 403
            return f(*args, **kwargs)
        return decorated
    return outer

def _rol():
    return session.get("rol", "user")

def _is_superadmin():
    return _rol() == "superadmin"

def _is_admin():
    return _rol() == "superadmin"

def _almacen_id():
    return session.get("almacen_id")

def _permisos():
    return session.get("permisos") or {}

def _can(permiso):
    return bool(_permisos().get(permiso, False)) or _is_admin()


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
    session["almacen_id"] = user.get("almacen_id")
    session["permisos"] = user.get("permisos", {})
    session["proveedores"] = user.get("proveedores", [])
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
                 "nombre": session["nombre"], "rol": session["rol"],
                 "almacen_id": session.get("almacen_id"),
                 "permisos": session.get("permisos", {}),
                 "proveedores": session.get("proveedores", [])}
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
    users = listar_usuarios(None if _is_superadmin() else _almacen_id())
    return jsonify(users)

@app.route("/api/admin/usuarios", methods=["POST"])
@admin_required
def api_admin_crear_usuario():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    nombre = data.get("nombre", "").strip()
    rol = data.get("rol", "user")
    almacen_id = data.get("almacen_id")
    permisos = data.get("permisos") or {}
    proveedores = data.get("proveedores") or []
    if not username or not password:
        return jsonify({"error": "Username y password requeridos"}), 400
    if len(password) < 4:
        return jsonify({"error": "Password minimo 4 caracteres"}), 400
    if rol not in ("superadmin", "admin", "user"):
        return jsonify({"error": "Rol invalido"}), 400
    if not _is_superadmin() and rol == "superadmin":
        return jsonify({"error": "Solo superadmin puede crear superadmin"}), 403
    if not _is_superadmin():
        almacen_id = _almacen_id()
    try:
        uid = crear_usuario(username, password, nombre, rol, almacen_id=almacen_id, permisos=permisos, proveedores=proveedores)
        return jsonify({"ok": True, "id": uid})
    except ValueError as e:
        return jsonify({"error": str(e)}), 409

@app.route("/api/admin/usuarios/<int:uid>", methods=["PUT"])
@admin_required
def api_admin_editar_usuario(uid):
    data = request.get_json(silent=True) or {}
    target = obtener_usuario(uid)
    if not target:
        return jsonify({"error": "Usuario no encontrado"}), 404
    if not _is_superadmin() and target.get("almacen_id") != _almacen_id():
        return jsonify({"error": "Solo puedes editar usuarios de tu almacen"}), 403
    campos = {}
    if "nombre" in data: campos["nombre"] = data["nombre"]
    if "rol" in data and data["rol"] in ("superadmin", "admin", "user"):
        if not _is_superadmin() and data["rol"] == "superadmin":
            return jsonify({"error": "Solo superadmin puede asignar este rol"}), 403
        campos["rol"] = data["rol"]
    if "almacen_id" in data and _is_superadmin():
        campos["almacen_id"] = data["almacen_id"]
    if "activo" in data: campos["activo"] = 1 if data["activo"] else 0
    if "permisos" in data: campos["permisos"] = data["permisos"]
    if "proveedores" in data: campos["proveedores"] = data["proveedores"]
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
    target = obtener_usuario(uid)
    if not target:
        return jsonify({"error": "Usuario no encontrado"}), 404
    if not _is_superadmin() and target.get("almacen_id") != _almacen_id():
        return jsonify({"error": "Solo puedes desactivar usuarios de tu almacen"}), 403
    eliminar_usuario(uid)
    return jsonify({"ok": True})


@app.route("/api/admin/context")
@admin_required
def api_admin_context():
    return jsonify({
        "almacenes": listar_almacenes(),
        "proveedores": listar_proveedores(),
        "is_superadmin": _is_superadmin(),
    })

@app.route("/api/admin/almacenes", methods=["POST"])
@superadmin_required
def api_admin_crear_almacen():
    data = request.get_json(silent=True) or {}
    nombre = data.get("nombre", "").strip()
    if not nombre:
        return jsonify({"error": "Nombre requerido"}), 400
    try:
        aid = crear_almacen(nombre)
        return jsonify({"ok": True, "id": aid})
    except ValueError as e:
        return jsonify({"error": str(e)}), 409

@app.route("/api/admin/proveedores", methods=["POST"])
@admin_required
def api_admin_crear_proveedor():
    data = request.get_json(silent=True) or {}
    nombre = data.get("nombre", "").strip()
    if not nombre:
        return jsonify({"error": "Nombre requerido"}), 400
    try:
        pid = crear_proveedor(nombre)
        return jsonify({"ok": True, "id": pid})
    except ValueError as e:
        return jsonify({"error": str(e)}), 409

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
@permiso_required("analizar")
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
@permiso_required("historial")
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
@permiso_required("historial")
def api_ultimo():
    u = ultimo_analisis(_uid(), _is_admin())
    return jsonify(u if u else {})

@app.route("/api/comparar/<int:id_a>/<int:id_b>")
@login_required
@permiso_required("comparar")
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
@permiso_required("dashboard")
def api_dashboard():
    return jsonify(dashboard_data(_uid(), _is_admin()))

@app.route("/api/operarios")
@login_required
@permiso_required("operarios")
def api_ops():
    return jsonify(buscar_operarios(request.args.get("q",""), _uid(), _is_admin()))

@app.route("/api/operarios/<codigo>/historico")
@login_required
@permiso_required("operarios")
def api_hist_op(codigo):
    return jsonify(historico_operario(codigo, 30, _uid(), _is_admin()))

@app.route("/api/clientes")
@login_required
@permiso_required("clientes")
def api_cls():
    data = buscar_clientes(request.args.get("q",""), _uid(), _is_admin())
    permitidos = session.get("proveedores") or []
    if permitidos and not _is_admin():
        permitidos_set = {p.strip().lower() for p in permitidos if p}
        data = [c for c in data if str(c.get("nombre", "")).strip().lower() in permitidos_set]
    return jsonify(data)

@app.route("/api/clientes/<path:nombre>/historico")
@login_required
@permiso_required("clientes")
def api_hist_cl(nombre):
    permitidos = session.get("proveedores") or []
    if permitidos and not _is_admin():
        permitidos_set = {p.strip().lower() for p in permitidos if p}
        if nombre.strip().lower() not in permitidos_set:
            return jsonify([])
    return jsonify(historico_cliente(nombre, 30, _uid(), _is_admin()))

@app.route("/api/db")
@login_required
def api_db():
    return jsonify(db_stats())

@app.route("/api/db/compactar", methods=["POST"])
@admin_required
def api_compactar():
    return jsonify(compactar_db())
