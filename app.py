"""
Logitime — Servidor Flask v3
Novedades: Login con sesiones, admin endpoints, settings editables
"""

import os, sys, io, time, traceback, secrets, threading, uuid
from datetime import timedelta
import numpy as np
import pandas as pd
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, send_file, abort, session, g
from flask.json.provider import DefaultJSONProvider
from database import (
    init_db, guardar_analisis, listar_analisis, obtener_analisis,
    eliminar_analisis, buscar_operarios, buscar_clientes,
    historico_operario, historico_cliente, dashboard_data, db_stats,
    comparar_analisis, obtener_historico_ops, exportar_analisis,
    ultimo_analisis, compactar_db,
    autenticar, crear_usuario, listar_usuarios, obtener_usuario,
    actualizar_usuario, eliminar_usuario, listar_almacenes, crear_almacen, actualizar_almacen, listar_proveedores, crear_proveedor,
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
app.config["SESSION_COOKIE_SECURE"] = os.getenv("LOGITIME_COOKIE_SECURE", "0") == "1"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=int(os.getenv("LOGITIME_SESSION_HOURS", "12")))

LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGITIME_LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_WINDOW_SEC = int(os.getenv("LOGITIME_LOGIN_WINDOW_SEC", "300"))
LOGIN_MAX_TRACKED_KEYS = int(os.getenv("LOGITIME_LOGIN_MAX_TRACKED_KEYS", "5000"))
BULK_STATUS_MAX_IDS = int(os.getenv("LOGITIME_BULK_STATUS_MAX_IDS", "200"))
_login_attempts = {}
_login_lock = threading.Lock()

AUDIT_LOG_PATH = os.path.join(STATIC_DIR, "data", "audit.log")


# ── Errores siempre JSON ──
@app.errorhandler(Exception)
def handle_exception(e):
    tb = traceback.format_exc()
    print(f"[ERROR] {e}\n{tb}")
    verbose = app.debug or os.getenv("LOGITIME_VERBOSE_ERRORS", "0") == "1"
    payload = {
        "error": str(e) if verbose else "Error interno del servidor",
        "code": "INTERNAL_ERROR",
    }
    rid = getattr(g, "request_id", None)
    if rid:
        payload["request_id"] = rid
    return jsonify(payload), 500

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

def _uid():
    return session.get("user_id")

def _error(msg, status=400, code=None):
    payload = {"error": msg}
    if code:
        payload["code"] = code
    rid = getattr(g, "request_id", None)
    if rid:
        payload["request_id"] = rid
    return jsonify(payload), status

def _get_json():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}

def _log_audit(action, target="", extra=""):
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG_PATH), exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        actor = session.get("username", "anon")
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{ts}\t{actor}\t{action}\t{target}\t{extra}\n")
    except Exception:
        pass

def _request_identity_key(username=""):
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    return f"{username.lower()}|{ip}"

def _is_login_limited(username):
    now = time.time()
    key = _request_identity_key(username)
    with _login_lock:
        if len(_login_attempts) > LOGIN_MAX_TRACKED_KEYS:
            # Purga simple para evitar crecimiento no acotado.
            stale_before = now - LOGIN_WINDOW_SEC
            to_del = [k for k, vals in _login_attempts.items() if not vals or max(vals) < stale_before]
            for k in to_del:
                _login_attempts.pop(k, None)
        attempts = [t for t in _login_attempts.get(key, []) if now - t <= LOGIN_WINDOW_SEC]
        _login_attempts[key] = attempts
        return len(attempts) >= LOGIN_MAX_ATTEMPTS

def _record_login_failure(username):
    now = time.time()
    key = _request_identity_key(username)
    with _login_lock:
        attempts = [t for t in _login_attempts.get(key, []) if now - t <= LOGIN_WINDOW_SEC]
        attempts.append(now)
        _login_attempts[key] = attempts

def _clear_login_failures(username):
    key = _request_identity_key(username)
    with _login_lock:
        _login_attempts.pop(key, None)

def permiso_required(nombre):
    def outer(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if "user_id" not in session:
                return _error("No autenticado", 401, "AUTH_REQUIRED")
            if not _can(nombre):
                return _error(f"No tienes permiso para '{nombre}'", 403)
            return f(*args, **kwargs)
        return decorated
    return outer

def _can_global_scope():
    return _can("manage_warehouses")

def _almacen_id():
    return session.get("almacen_id")

def _permisos():
    return session.get("permisos") or {}

def _can(permiso):
    return bool(_permisos().get(permiso, False))

def _can_any(*permisos):
    return any(_can(p) for p in permisos)

def _can_manage_target_user(target):
    return _can("manage_warehouses") or target.get("almacen_id") == _almacen_id()

def _value_error_response(exc):
    msg = str(exc)
    low = msg.lower()
    if "no encontrado" in low or "invalido" in low:
        return _error(msg, 400)
    if "ya existe" in low:
        return _error(msg, 409)
    return _error(msg, 400)


@app.before_request
def _request_start_timer():
    g._t0 = time.perf_counter()
    g.request_id = uuid.uuid4().hex[:12]


@app.after_request
def _log_slow_request(resp):
    try:
        dt = (time.perf_counter() - getattr(g, "_t0", time.perf_counter())) * 1000
        if request.path.startswith("/api"):
            print(f"[REQ] {getattr(g,'request_id','-')} {request.method} {request.path} -> {resp.status_code} ({dt:.1f} ms)")
    except Exception:
        pass
    if getattr(g, "request_id", None):
        resp.headers["X-Request-Id"] = g.request_id
    return resp


# ── Paginas estaticas ──

@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


# ── Auth endpoints ──

@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = _get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or not password:
        return _error("Usuario y password requeridos")
    if _is_login_limited(username):
        return _error("Demasiados intentos. Espera unos minutos.", 429, "RATE_LIMIT")
    user = autenticar(username, password)
    if not user:
        _record_login_failure(username)
        return _error("Credenciales incorrectas", 401)
    _clear_login_failures(username)
    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["nombre"] = user["nombre"]
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
                 "nombre": session["nombre"],
                 "almacen_id": session.get("almacen_id"),
                 "permisos": session.get("permisos", {}),
                 "proveedores": session.get("proveedores", [])}
    })

@app.route("/api/auth/cambiar-password", methods=["POST"])
@login_required
def api_cambiar_password():
    data = _get_json()
    new_pw = data.get("password", "")
    if len(new_pw) < 4:
        return _error("La password debe tener al menos 4 caracteres")
    actualizar_usuario(_uid(), password=new_pw)
    return jsonify({"ok": True})


# ── Admin: Usuarios ──

@app.route("/api/admin/usuarios")
@permiso_required("manage_users")
def api_admin_listar_usuarios():
    users = listar_usuarios(None if _can("manage_warehouses") else _almacen_id())
    q = (request.args.get("q") or "").strip().lower()
    st = (request.args.get("status") or "all").lower()
    if q:
        users = [u for u in users if q in str(u.get("username", "")).lower() or q in str(u.get("nombre", "")).lower()]
    if st in ("active", "inactive"):
        want = st == "active"
        users = [u for u in users if bool(u.get("activo")) == want]
    offset = request.args.get("offset", type=int)
    limit = request.args.get("limit", type=int)
    if offset is not None and offset > 0:
        users = users[offset:]
    if limit is not None and limit > 0:
        users = users[:limit]
    return jsonify(users)

@app.route("/api/admin/usuarios", methods=["POST"])
@permiso_required("manage_users")
def api_admin_crear_usuario():
    data = _get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    nombre = data.get("nombre", "").strip()
    almacen_id = data.get("almacen_id")
    permisos = data.get("permisos") or {}
    proveedores = data.get("proveedores") or []
    if not username or not password:
        return _error("Username y password requeridos")
    if len(password) < 4:
        return _error("Password minimo 4 caracteres")
    if not _can("manage_warehouses"):
        almacen_id = _almacen_id()
    try:
        uid = crear_usuario(username, password, nombre, almacen_id=almacen_id, permisos=permisos, proveedores=proveedores)
        _log_audit("USER_CREATE", str(uid), f"username={username}")
        return jsonify({"ok": True, "id": uid})
    except ValueError as e:
        return _value_error_response(e)

@app.route("/api/admin/usuarios/<int:uid>", methods=["PUT"])
@permiso_required("manage_users")
def api_admin_editar_usuario(uid):
    data = _get_json()
    target = obtener_usuario(uid)
    if not target:
        return _error("Usuario no encontrado", 404)
    if not _can_manage_target_user(target):
        return _error("Solo puedes editar usuarios de tu almacen", 403)
    campos = {}
    if "nombre" in data: campos["nombre"] = data["nombre"]
    if "almacen_id" in data and _can("manage_warehouses"):
        campos["almacen_id"] = data["almacen_id"]
    if "activo" in data:
        if not data["activo"] and target.get("username") == "admin":
            return _error("La cuenta admin no se puede desactivar", 403)
        campos["activo"] = 1 if data["activo"] else 0
    if "permisos" in data: campos["permisos"] = data["permisos"]
    if "proveedores" in data: campos["proveedores"] = data["proveedores"]
    if "password" in data and data["password"]:
        if len(data["password"]) < 4:
            return _error("Password minimo 4 caracteres")
        campos["password"] = data["password"]
    try:
        actualizar_usuario(uid, **campos)
        _log_audit("USER_UPDATE", str(uid), f"campos={','.join(sorted(campos.keys()))}")
    except ValueError as e:
        return _value_error_response(e)
    return jsonify({"ok": True})

@app.route("/api/admin/usuarios/<int:uid>", methods=["DELETE"])
@permiso_required("manage_users")
def api_admin_eliminar_usuario(uid):
    if uid == _uid():
        return _error("No puedes desactivarte a ti mismo")
    target = obtener_usuario(uid)
    if not target:
        return _error("Usuario no encontrado", 404)
    if not _can_manage_target_user(target):
        return _error("Solo puedes desactivar usuarios de tu almacen", 403)
    if target.get("username") == "admin":
        return _error("La cuenta admin no se puede desactivar", 403)
    eliminar_usuario(uid)
    _log_audit("USER_DEACTIVATE", str(uid), f"username={target.get('username','')}")
    return jsonify({"ok": True})


@app.route("/api/admin/usuarios/bulk-status", methods=["POST"])
@permiso_required("manage_users")
def api_admin_bulk_status():
    data = _get_json()
    ids = data.get("ids") or []
    activo = bool(data.get("activo", True))
    if not isinstance(ids, list) or not ids:
        return _error("Debes enviar una lista de ids")
    if len(ids) > BULK_STATUS_MAX_IDS:
        return _error(f"Maximo {BULK_STATUS_MAX_IDS} ids por lote", 400, "BULK_LIMIT")
    updated = 0
    skipped = 0
    for raw in ids:
        try:
            uid = int(raw)
        except Exception:
            skipped += 1
            continue
        target = obtener_usuario(uid)
        if not target:
            skipped += 1
            continue
        if target.get("username") == "admin" and not activo:
            skipped += 1
            continue
        if not _can_manage_target_user(target):
            skipped += 1
            continue
        actualizar_usuario(uid, activo=1 if activo else 0)
        updated += 1
    _log_audit("USER_BULK_STATUS", "*", f"updated={updated},skipped={skipped},activo={activo}")
    return jsonify({"ok": True, "updated": updated, "skipped": skipped})


@app.route("/api/admin/audit/recent")
@permiso_required("manage_settings")
def api_admin_audit_recent():
    limit = min(max(request.args.get("limit", 100, type=int), 1), 500)
    if not os.path.exists(AUDIT_LOG_PATH):
        return jsonify([])
    try:
        with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
        out = []
        for ln in lines:
            parts = ln.rstrip("\n").split("\t")
            out.append({
                "ts": parts[0] if len(parts) > 0 else "",
                "actor": parts[1] if len(parts) > 1 else "",
                "action": parts[2] if len(parts) > 2 else "",
                "target": parts[3] if len(parts) > 3 else "",
                "extra": parts[4] if len(parts) > 4 else "",
            })
        return jsonify(out)
    except Exception as e:
        return _error(f"No se pudo leer auditoria: {e}", 500)


@app.route("/api/admin/audit/recent.csv")
@permiso_required("manage_settings")
def api_admin_audit_recent_csv():
    limit = min(max(request.args.get("limit", 500, type=int), 1), 2000)
    if not os.path.exists(AUDIT_LOG_PATH):
        return send_file(io.BytesIO(b"ts,actor,action,target,extra\n"), mimetype="text/csv", as_attachment=True,
                         download_name="audit_recent.csv")
    try:
        with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
        rows = []
        for ln in lines:
            parts = ln.rstrip("\n").split("\t")
            rows.append({
                "ts": parts[0] if len(parts) > 0 else "",
                "actor": parts[1] if len(parts) > 1 else "",
                "action": parts[2] if len(parts) > 2 else "",
                "target": parts[3] if len(parts) > 3 else "",
                "extra": parts[4] if len(parts) > 4 else "",
            })
        df = pd.DataFrame(rows or [{"ts": "", "actor": "", "action": "", "target": "", "extra": ""}])
        out = io.StringIO()
        df.to_csv(out, index=False)
        return send_file(io.BytesIO(out.getvalue().encode("utf-8")), mimetype="text/csv", as_attachment=True,
                         download_name="audit_recent.csv")
    except Exception as e:
        return _error(f"No se pudo exportar auditoria: {e}", 500)


@app.route("/api/capabilities")
@login_required
def api_capabilities():
    perms = session.get("permisos", {})
    return jsonify({
        "can_manage_users": bool(perms.get("manage_users")),
        "can_manage_settings": bool(perms.get("manage_settings")),
        "can_manage_warehouses": bool(perms.get("manage_warehouses")),
        "can_export": bool(perms.get("manage_users") or perms.get("manage_warehouses")),
    })


@app.route("/api/admin/context")
@login_required
def api_admin_context():
    if not _can_any("manage_users", "manage_settings", "manage_warehouses"):
        return _error("No tienes permisos de administracion", 403)
    return jsonify({
        "almacenes": listar_almacenes(),
        "proveedores": listar_proveedores(),
        "can_manage_users": _can("manage_users"),
        "can_manage_settings": _can("manage_settings"),
        "can_manage_warehouses": _can("manage_warehouses"),
    })

@app.route("/api/admin/almacenes", methods=["POST"])
@permiso_required("manage_warehouses")
def api_admin_crear_almacen():
    data = _get_json()
    nombre = data.get("nombre", "").strip()
    if not nombre:
        return _error("Nombre requerido")
    try:
        aid = crear_almacen(nombre)
        _log_audit("WAREHOUSE_CREATE", str(aid), nombre)
        return jsonify({"ok": True, "id": aid})
    except ValueError as e:
        return jsonify({"error": str(e)}), 409

@app.route("/api/admin/almacenes/<int:aid>", methods=["PUT"])
@permiso_required("manage_warehouses")
def api_admin_editar_almacen(aid):
    data = _get_json()
    cambios = {}
    if "nombre" in data:
        cambios["nombre"] = data.get("nombre", "")
    if "activo" in data:
        cambios["activo"] = bool(data.get("activo"))
    if not cambios:
        return _error("Sin cambios")
    try:
        actualizar_almacen(aid, **cambios)
        _log_audit("WAREHOUSE_UPDATE", str(aid), f"campos={','.join(sorted(cambios.keys()))}")
        return jsonify({"ok": True})
    except ValueError as e:
        msg = str(e)
        if "no encontrado" in msg.lower():
            return jsonify({"error": msg}), 404
        if "usuarios activos" in msg.lower():
            return jsonify({"error": msg}), 409
        return jsonify({"error": msg}), 400

@app.route("/api/admin/proveedores", methods=["POST"])
@permiso_required("manage_users")
def api_admin_crear_proveedor():
    data = _get_json()
    nombre = data.get("nombre", "").strip()
    if not nombre:
        return _error("Nombre requerido")
    try:
        pid = crear_proveedor(nombre)
        _log_audit("PROVIDER_CREATE", str(pid), nombre)
        return jsonify({"ok": True, "id": pid})
    except ValueError as e:
        return jsonify({"error": str(e)}), 409

# ── Admin: Settings ──

@app.route("/api/admin/settings")
@permiso_required("manage_settings")
def api_admin_get_settings():
    return jsonify(obtener_all_settings())

@app.route("/api/admin/settings", methods=["POST"])
@permiso_required("manage_settings")
def api_admin_save_settings():
    data = _get_json()
    guardar_all_settings(data)
    _log_audit("SETTINGS_UPDATE", "*", "save_settings")
    return jsonify({"ok": True, "settings": obtener_all_settings()})


@app.route("/api/admin/export/users.csv")
@permiso_required("manage_users")
def api_admin_export_users_csv():
    users = listar_usuarios(None if _can("manage_warehouses") else _almacen_id())
    almacenes = {a["id"]: a["nombre"] for a in listar_almacenes()}
    rows = []
    for u in users:
        rows.append({
            "id": u.get("id"),
            "username": u.get("username"),
            "nombre": u.get("nombre"),
            "almacen": almacenes.get(u.get("almacen_id"), ""),
            "activo": int(bool(u.get("activo"))),
            "proveedores": ", ".join(u.get("proveedores") or []),
        })
    df = pd.DataFrame(rows)
    out = io.StringIO()
    df.to_csv(out, index=False)
    return send_file(io.BytesIO(out.getvalue().encode("utf-8")), mimetype="text/csv", as_attachment=True,
                     download_name="usuarios.csv")


@app.route("/api/admin/export/almacenes.csv")
@permiso_required("manage_warehouses")
def api_admin_export_warehouses_csv():
    df = pd.DataFrame(listar_almacenes())
    out = io.StringIO()
    df.to_csv(out, index=False)
    return send_file(io.BytesIO(out.getvalue().encode("utf-8")), mimetype="text/csv", as_attachment=True,
                     download_name="almacenes.csv")


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

        hist_ops = obtener_historico_ops(_uid(), _can_global_scope())
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
    return jsonify(listar_analisis(request.args.get("limit", 50, type=int), _uid(), _can_global_scope()))

@app.route("/api/analisis/<int:aid>")
@login_required
def api_detalle(aid):
    d = obtener_analisis(aid, _uid(), _can_global_scope())
    if not d: abort(404)
    return jsonify(d)

@app.route("/api/analisis/<int:aid>", methods=["DELETE"])
@login_required
def api_borrar(aid):
    eliminar_analisis(aid, _uid(), _can_global_scope())
    return jsonify({"ok": True})

@app.route("/api/ultimo")
@login_required
@permiso_required("historial")
def api_ultimo():
    u = ultimo_analisis(_uid(), _can_global_scope())
    return jsonify(u if u else {})

@app.route("/api/comparar/<int:id_a>/<int:id_b>")
@login_required
@permiso_required("comparar")
def api_comparar(id_a, id_b):
    data = comparar_analisis(id_a, id_b, _uid(), _can_global_scope())
    if not data: abort(404)
    return jsonify(data)

@app.route("/api/exportar/<int:aid>")
@login_required
def api_exportar(aid):
    data = exportar_analisis(aid, _uid(), _can_global_scope())
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
    return jsonify(dashboard_data(_uid(), _can_global_scope()))

@app.route("/api/operarios")
@login_required
@permiso_required("operarios")
def api_ops():
    return jsonify(buscar_operarios(request.args.get("q",""), _uid(), _can_global_scope()))

@app.route("/api/operarios/<codigo>/historico")
@login_required
@permiso_required("operarios")
def api_hist_op(codigo):
    return jsonify(historico_operario(codigo, 30, _uid(), _can_global_scope()))

@app.route("/api/clientes")
@login_required
@permiso_required("clientes")
def api_cls():
    data = buscar_clientes(request.args.get("q",""), _uid(), _can_global_scope())
    permitidos = session.get("proveedores") or []
    if permitidos and not _can_global_scope():
        permitidos_set = {p.strip().lower() for p in permitidos if p}
        data = [c for c in data if str(c.get("nombre", "")).strip().lower() in permitidos_set]
    return jsonify(data)

@app.route("/api/clientes/<path:nombre>/historico")
@login_required
@permiso_required("clientes")
def api_hist_cl(nombre):
    permitidos = session.get("proveedores") or []
    if permitidos and not _can_global_scope():
        permitidos_set = {p.strip().lower() for p in permitidos if p}
        if nombre.strip().lower() not in permitidos_set:
            return jsonify([])
    return jsonify(historico_cliente(nombre, 30, _uid(), _can_global_scope()))

@app.route("/api/db")
@login_required
def api_db():
    return jsonify(db_stats())


@app.route("/api/health")
def api_health():
    try:
        stats = db_stats()
        return jsonify({"ok": True, "db": "ok", "size_mb": stats.get("size_mb")})
    except Exception as e:
        return jsonify({"ok": False, "db": "error", "error": str(e)}), 500

@app.route("/api/db/compactar", methods=["POST"])
@permiso_required("manage_settings")
def api_compactar():
    return jsonify(compactar_db())
