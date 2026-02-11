"""
Logitime — Base de datos SQLite v3
Novedades:
  - Tabla usuarios con roles (admin/user)
  - Tabla settings para umbrales editables
  - user_id en analisis (cada usuario ve solo lo suyo, admin ve todo)
  - Hashing de passwords con werkzeug
"""

import sqlite3, os, sys, time, json, copy
from contextlib import contextmanager
from werkzeug.security import generate_password_hash, check_password_hash

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(BASE_DIR, "data", "logitime.db")
_hist_cache = {"data": None, "ts": 0, "ttl": 60, "key": ""}

DEFAULT_UMBRALES = {
    "picking":   {"normal": 8,  "anomalia": 20},
    "packing":   {"normal": 5,  "anomalia": 12.5},
    "carga":     {"normal": 15, "anomalia": 37.5},
    "recepcion": {"normal": 12, "anomalia": 30},
    "default":   {"normal": 10, "anomalia": 25},
}
DEFAULT_MARGEN_MANT = 30
DEFAULT_SCORE_WEIGHTS = {"rate_w": 50, "excess_w": 30, "trend_w": 20}


DEFAULT_PERMISOS = {
    "analizar": True,
    "dashboard": True,
    "operarios": True,
    "clientes": True,
    "historial": True,
    "comparar": True,
    "manage_users": False,
    "manage_settings": False,
}
DEFAULT_ALMACEN = "Almacen Principal"


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def _rows(rows):
    return [dict(r) for r in rows]

def _invalidate_cache():
    _hist_cache["data"] = None
    _hist_cache["ts"] = 0


# ===================== INIT =====================

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS almacenes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL UNIQUE COLLATE NOCASE,
                activo INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS proveedores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL UNIQUE COLLATE NOCASE,
                activo INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                nombre TEXT DEFAULT '',
                rol TEXT DEFAULT 'user' CHECK(rol IN ('superadmin','admin','user')),
                almacen_id INTEGER,
                permisos TEXT DEFAULT '{}',
                proveedores TEXT DEFAULT '[]',
                activo INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (almacen_id) REFERENCES almacenes(id)
            );
            CREATE TABLE IF NOT EXISTS settings (
                clave TEXT PRIMARY KEY,
                valor TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS analisis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                fecha TEXT NOT NULL,
                archivo TEXT,
                total_movimientos INTEGER DEFAULT 0,
                total_anomalias INTEGER DEFAULT 0,
                anomalias_justificadas INTEGER DEFAULT 0,
                tasa_anomalias_pct REAL DEFAULT 0,
                patrones_hora TEXT DEFAULT '{}',
                alertas TEXT DEFAULT '[]',
                created_at TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (user_id) REFERENCES usuarios(id)
            );
            CREATE TABLE IF NOT EXISTS operarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analisis_id INTEGER NOT NULL,
                codigo TEXT NOT NULL, nombre TEXT,
                total_movimientos INTEGER DEFAULT 0, total_anomalias INTEGER DEFAULT 0,
                tiempo_promedio_entre REAL DEFAULT 0, duracion_promedio REAL DEFAULT 0,
                total_unidades INTEGER DEFAULT 0, productividad REAL DEFAULT 0,
                score_riesgo REAL DEFAULT 0, tendencia TEXT DEFAULT 'estable',
                FOREIGN KEY (analisis_id) REFERENCES analisis(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS clientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analisis_id INTEGER NOT NULL,
                nombre TEXT NOT NULL,
                total_movimientos INTEGER DEFAULT 0, total_unidades INTEGER DEFAULT 0,
                duracion_total REAL DEFAULT 0, duracion_promedio REAL DEFAULT 0,
                productividad REAL DEFAULT 0,
                FOREIGN KEY (analisis_id) REFERENCES analisis(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS anomalias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analisis_id INTEGER NOT NULL,
                operario TEXT, operario_nombre TEXT,
                picking_n INTEGER, picking_n1 INTEGER,
                tiempo_entre REAL, umbral REAL, umbral_tipo TEXT DEFAULT 'fijo',
                exceso REAL, tipo_operacion TEXT,
                fecha_fin_n TEXT, fecha_fin_n1 TEXT,
                articulo TEXT, cliente TEXT,
                justificada INTEGER DEFAULT 0, motivo TEXT,
                FOREIGN KEY (analisis_id) REFERENCES analisis(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_op_analisis ON operarios(analisis_id);
            CREATE INDEX IF NOT EXISTS idx_op_codigo ON operarios(codigo);
            CREATE INDEX IF NOT EXISTS idx_cl_analisis ON clientes(analisis_id);
            CREATE INDEX IF NOT EXISTS idx_an_analisis ON anomalias(analisis_id);
            CREATE INDEX IF NOT EXISTS idx_analisis_fecha ON analisis(fecha);
            CREATE INDEX IF NOT EXISTS idx_analisis_user ON analisis(user_id);
            CREATE INDEX IF NOT EXISTS idx_users_almacen ON usuarios(almacen_id);
        """)
        _migrate(conn)
        _seed_almacen(conn)
        _seed_admin(conn)
        _seed_proveedores(conn)
        _seed_settings(conn)

def _migrate(conn):
    for table, col, typedef in [
        ("analisis", "patrones_hora", "TEXT DEFAULT '{}'"),
        ("analisis", "alertas", "TEXT DEFAULT '[]'"),
        ("analisis", "user_id", "INTEGER"),
        ("anomalias", "umbral_tipo", "TEXT DEFAULT 'fijo'"),
        ("usuarios", "almacen_id", "INTEGER"),
        ("usuarios", "permisos", "TEXT DEFAULT '{}'"),
        ("usuarios", "proveedores", "TEXT DEFAULT '[]'"),
    ]:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass
    conn.execute("DROP TABLE IF EXISTS movimientos")

def _seed_almacen(conn):
    conn.execute("INSERT OR IGNORE INTO almacenes (nombre) VALUES (?)", (DEFAULT_ALMACEN,))

def _seed_admin(conn):
    if conn.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 0:
        almacen_id = conn.execute("SELECT id FROM almacenes ORDER BY id LIMIT 1").fetchone()[0]
        conn.execute(
            "INSERT INTO usuarios (username,password_hash,nombre,rol,almacen_id,permisos) VALUES (?,?,?,?,?,?)",
            ("admin", generate_password_hash("admin123"), "Administrador", "superadmin", almacen_id,
             json.dumps({**DEFAULT_PERMISOS, "manage_users": True, "manage_settings": True})))

def _seed_proveedores(conn):
    for n in ["Proveedor General"]:
        conn.execute("INSERT OR IGNORE INTO proveedores (nombre) VALUES (?)", (n,))

def _seed_settings(conn):
    for k, v in [("umbrales", json.dumps(DEFAULT_UMBRALES)),
                 ("margen_mantenimiento", str(DEFAULT_MARGEN_MANT)),
                 ("score_weights", json.dumps(DEFAULT_SCORE_WEIGHTS))]:
        try:
            conn.execute("INSERT INTO settings (clave,valor) VALUES (?,?)", (k, v))
        except sqlite3.IntegrityError:
            pass


def _parse_json(raw, fallback):
    if not raw:
        return copy.deepcopy(fallback)
    try:
        value = json.loads(raw)
        return value
    except Exception:
        return copy.deepcopy(fallback)

def _default_permisos_by_role(rol):
    base = copy.deepcopy(DEFAULT_PERMISOS)
    if rol in ("admin", "superadmin"):
        base["manage_users"] = True
        base["manage_settings"] = True
    return base

def _normalize_permisos(permisos, rol):
    base = _default_permisos_by_role(rol)
    if isinstance(permisos, dict):
        for k in base.keys():
            if k in permisos:
                base[k] = bool(permisos[k])
    return base

def _sanitize_proveedores(proveedores):
    if not isinstance(proveedores, list):
        return []
    clean = []
    for p in proveedores:
        if isinstance(p, str) and p.strip():
            clean.append(p.strip())
    return sorted(set(clean))

def _row_user_public(row):
    d = dict(row)
    d["permisos"] = _normalize_permisos(_parse_json(d.get("permisos"), {}), d.get("rol", "user"))
    d["proveedores"] = _sanitize_proveedores(_parse_json(d.get("proveedores"), []))
    return d

def listar_almacenes(activos_solo=False):
    with get_db() as conn:
        q = "SELECT id,nombre,activo,created_at FROM almacenes"
        if activos_solo:
            q += " WHERE activo=1"
        q += " ORDER BY nombre"
        return _rows(conn.execute(q).fetchall())

def crear_almacen(nombre):
    with get_db() as conn:
        try:
            cur = conn.execute("INSERT INTO almacenes (nombre) VALUES (?)", (nombre.strip(),))
            return cur.lastrowid
        except sqlite3.IntegrityError:
            raise ValueError(f"El almacen '{nombre}' ya existe")

def listar_proveedores(activos_solo=False):
    with get_db() as conn:
        q = "SELECT id,nombre,activo,created_at FROM proveedores"
        if activos_solo:
            q += " WHERE activo=1"
        q += " ORDER BY nombre"
        return _rows(conn.execute(q).fetchall())

def crear_proveedor(nombre):
    with get_db() as conn:
        try:
            cur = conn.execute("INSERT INTO proveedores (nombre) VALUES (?)", (nombre.strip(),))
            return cur.lastrowid
        except sqlite3.IntegrityError:
            raise ValueError(f"El proveedor '{nombre}' ya existe")

# ===================== AUTH =====================

def autenticar(username, password):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM usuarios WHERE username=? AND activo=1", (username,)).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            u = _row_user_public(row)
            return {"id": u["id"], "username": u["username"], "nombre": u["nombre"], "rol": u["rol"],
                    "almacen_id": u.get("almacen_id"), "permisos": u.get("permisos", {}),
                    "proveedores": u.get("proveedores", [])}
    return None

def crear_usuario(username, password, nombre="", rol="user", almacen_id=None, permisos=None, proveedores=None):
    if rol not in ("superadmin", "admin", "user"):
        raise ValueError("Rol invalido")
    permisos_n = _normalize_permisos(permisos, rol)
    proveedores_n = _sanitize_proveedores(proveedores or [])
    with get_db() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO usuarios (username,password_hash,nombre,rol,almacen_id,permisos,proveedores) VALUES (?,?,?,?,?,?,?)",
                (username, generate_password_hash(password), nombre, rol, almacen_id,
                 json.dumps(permisos_n, ensure_ascii=False), json.dumps(proveedores_n, ensure_ascii=False)))
            return cur.lastrowid
        except sqlite3.IntegrityError:
            raise ValueError(f"El usuario '{username}' ya existe")

def listar_usuarios(almacen_id=None):
    with get_db() as conn:
        if almacen_id is None:
            rows = conn.execute(
                "SELECT id,username,nombre,rol,almacen_id,permisos,proveedores,activo,created_at FROM usuarios ORDER BY id").fetchall()
        else:
            rows = conn.execute(
                "SELECT id,username,nombre,rol,almacen_id,permisos,proveedores,activo,created_at FROM usuarios WHERE almacen_id=? ORDER BY id",
                (almacen_id,)).fetchall()
        return [_row_user_public(r) for r in rows]

def obtener_usuario(uid):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id,username,nombre,rol,almacen_id,permisos,proveedores,activo,created_at FROM usuarios WHERE id=?", (uid,)).fetchone()
        return _row_user_public(row) if row else None

def actualizar_usuario(uid, **campos):
    with get_db() as conn:
        existing = conn.execute("SELECT rol FROM usuarios WHERE id=?", (uid,)).fetchone()
        if not existing:
            return
        target_role = campos.get("rol", existing["rol"])
        if "password" in campos and campos["password"]:
            conn.execute("UPDATE usuarios SET password_hash=? WHERE id=?",
                         (generate_password_hash(campos.pop("password")), uid))
        allowed = {"nombre", "rol", "activo", "almacen_id"}
        updates = {k: v for k, v in campos.items() if k in allowed}
        if "permisos" in campos:
            updates["permisos"] = json.dumps(_normalize_permisos(campos["permisos"], target_role), ensure_ascii=False)
        if "proveedores" in campos:
            updates["proveedores"] = json.dumps(_sanitize_proveedores(campos["proveedores"]), ensure_ascii=False)
        if updates:
            sets = ", ".join(f"{k}=?" for k in updates)
            conn.execute(f"UPDATE usuarios SET {sets} WHERE id=?", (*updates.values(), uid))

def eliminar_usuario(uid):
    with get_db() as conn:
        conn.execute("UPDATE usuarios SET activo=0 WHERE id=?", (uid,))


# ===================== SETTINGS =====================

def obtener_setting(clave):
    with get_db() as conn:
        row = conn.execute("SELECT valor FROM settings WHERE clave=?", (clave,)).fetchone()
        return row["valor"] if row else None

def guardar_setting(clave, valor):
    with get_db() as conn:
        conn.execute("INSERT INTO settings (clave,valor) VALUES (?,?) ON CONFLICT(clave) DO UPDATE SET valor=?",
                     (clave, valor, valor))

def obtener_umbrales():
    raw = obtener_setting("umbrales")
    if raw:
        try: return json.loads(raw)
        except: pass
    return DEFAULT_UMBRALES.copy()

def obtener_margen_mantenimiento():
    raw = obtener_setting("margen_mantenimiento")
    try: return int(raw)
    except: return DEFAULT_MARGEN_MANT

def obtener_score_weights():
    raw = obtener_setting("score_weights")
    if raw:
        try: return json.loads(raw)
        except: pass
    return DEFAULT_SCORE_WEIGHTS.copy()

def obtener_all_settings():
    return {
        "umbrales": obtener_umbrales(),
        "margen_mantenimiento": obtener_margen_mantenimiento(),
        "score_weights": obtener_score_weights(),
    }

def guardar_all_settings(data):
    if "umbrales" in data:
        guardar_setting("umbrales", json.dumps(data["umbrales"]))
    if "margen_mantenimiento" in data:
        guardar_setting("margen_mantenimiento", str(int(data["margen_mantenimiento"])))
    if "score_weights" in data:
        guardar_setting("score_weights", json.dumps(data["score_weights"]))


# ===================== ANALISIS (con user_id) =====================

def guardar_analisis(resultado, user_id=None):
    with get_db() as conn:
        patrones_json = json.dumps(resultado.get("patrones_hora", {}), ensure_ascii=False)
        alertas_json = json.dumps(resultado.get("alertas", []), ensure_ascii=False)
        cur = conn.execute(
            """INSERT INTO analisis (user_id,fecha,archivo,total_movimientos,total_anomalias,
                anomalias_justificadas,tasa_anomalias_pct,patrones_hora,alertas) VALUES (?,?,?,?,?,?,?,?,?)""",
            (user_id, resultado["fecha"], resultado.get("archivo",""),
             resultado["total_movimientos"], resultado["total_anomalias"],
             resultado["anomalias_justificadas"], resultado.get("tasa_anomalias_pct",0),
             patrones_json, alertas_json))
        aid = cur.lastrowid
        ops = resultado.get("operarios", [])
        if ops:
            conn.executemany(
                """INSERT INTO operarios (analisis_id,codigo,nombre,total_movimientos,total_anomalias,
                    tiempo_promedio_entre,duracion_promedio,total_unidades,productividad,score_riesgo,tendencia)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                [(aid,o["codigo"],o["nombre"],o["total_movimientos"],o["total_anomalias"],
                  o["tiempo_promedio_entre"],o["duracion_promedio"],o["total_unidades"],
                  o["productividad"],o.get("score_riesgo",0),o.get("tendencia","estable")) for o in ops])
        cls = resultado.get("clientes", [])
        if cls:
            conn.executemany(
                """INSERT INTO clientes (analisis_id,nombre,total_movimientos,total_unidades,
                    duracion_total,duracion_promedio,productividad) VALUES (?,?,?,?,?,?,?)""",
                [(aid,c["nombre"],c["total_movimientos"],c["total_unidades"],
                  c["duracion_total"],c["duracion_promedio"],c["productividad"]) for c in cls])
        anoms = resultado.get("anomalias_detalle", [])
        if anoms:
            conn.executemany(
                """INSERT INTO anomalias (analisis_id,operario,operario_nombre,picking_n,picking_n1,
                    tiempo_entre,umbral,umbral_tipo,exceso,tipo_operacion,fecha_fin_n,fecha_fin_n1,
                    articulo,cliente,justificada,motivo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(aid,a["operario"],a["operario_nombre"],a["picking_n"],a["picking_n1"],
                  a["tiempo_entre"],a["umbral"],a.get("umbral_tipo","fijo"),a["exceso"],
                  a["tipo_operacion"],a["fecha_fin_n"],a["fecha_fin_n1"],
                  a.get("articulo"),a.get("cliente"),
                  1 if a.get("justificada") else 0,a.get("motivo")) for a in anoms])
        _invalidate_cache()
        return aid


def listar_analisis(limit=50, user_id=None, is_admin=False):
    with get_db() as conn:
        if is_admin:
            return _rows(conn.execute("SELECT * FROM analisis ORDER BY fecha DESC LIMIT ?", (limit,)).fetchall())
        return _rows(conn.execute("SELECT * FROM analisis WHERE user_id=? ORDER BY fecha DESC LIMIT ?",
                                  (user_id, limit)).fetchall())

def ultimo_analisis(user_id=None, is_admin=False):
    with get_db() as conn:
        if is_admin:
            row = conn.execute("SELECT * FROM analisis ORDER BY id DESC LIMIT 1").fetchone()
        else:
            row = conn.execute("SELECT * FROM analisis WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
        return dict(row) if row else None

def obtener_analisis(aid, user_id=None, is_admin=False):
    with get_db() as conn:
        if is_admin:
            row = conn.execute("SELECT * FROM analisis WHERE id=?", (aid,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM analisis WHERE id=? AND user_id=?", (aid, user_id)).fetchone()
        if not row: return None
        d = dict(row)
        try: d["patrones_hora"] = json.loads(d.get("patrones_hora") or "{}")
        except: d["patrones_hora"] = {}
        try: d["alertas"] = json.loads(d.get("alertas") or "[]")
        except: d["alertas"] = []
        d["operarios"] = _rows(conn.execute(
            "SELECT * FROM operarios WHERE analisis_id=? ORDER BY score_riesgo DESC", (aid,)).fetchall())
        d["clientes"] = _rows(conn.execute(
            "SELECT * FROM clientes WHERE analisis_id=? ORDER BY productividad DESC", (aid,)).fetchall())
        d["anomalias"] = _rows(conn.execute(
            "SELECT * FROM anomalias WHERE analisis_id=? AND justificada=0 ORDER BY exceso DESC", (aid,)).fetchall())
        return d

def eliminar_analisis(aid, user_id=None, is_admin=False):
    with get_db() as conn:
        if is_admin:
            conn.execute("DELETE FROM analisis WHERE id=?", (aid,))
        else:
            conn.execute("DELETE FROM analisis WHERE id=? AND user_id=?", (aid, user_id))
    _invalidate_cache()

def buscar_operarios(q="", user_id=None, is_admin=False):
    with get_db() as conn:
        if is_admin:
            return _rows(conn.execute(
                """SELECT codigo,nombre,ROUND(AVG(productividad),2) as prod_avg,
                   ROUND(AVG(total_anomalias),1) as anom_avg,ROUND(AVG(score_riesgo),1) as riesgo_avg,
                   SUM(total_unidades) as unidades_total,COUNT(*) as dias
                   FROM operarios WHERE codigo LIKE ? OR nombre LIKE ?
                   GROUP BY codigo ORDER BY prod_avg DESC""", (f"%{q}%",f"%{q}%")).fetchall())
        return _rows(conn.execute(
            """SELECT o.codigo,o.nombre,ROUND(AVG(o.productividad),2) as prod_avg,
               ROUND(AVG(o.total_anomalias),1) as anom_avg,ROUND(AVG(o.score_riesgo),1) as riesgo_avg,
               SUM(o.total_unidades) as unidades_total,COUNT(*) as dias
               FROM operarios o JOIN analisis a ON o.analisis_id=a.id
               WHERE a.user_id=? AND (o.codigo LIKE ? OR o.nombre LIKE ?)
               GROUP BY o.codigo ORDER BY prod_avg DESC""", (user_id,f"%{q}%",f"%{q}%")).fetchall())

def buscar_clientes(q="", user_id=None, is_admin=False):
    with get_db() as conn:
        if is_admin:
            return _rows(conn.execute(
                """SELECT nombre,ROUND(AVG(productividad),2) as prod_avg,
                   ROUND(AVG(total_movimientos),0) as mov_avg,SUM(total_unidades) as unidades_total,COUNT(*) as dias
                   FROM clientes WHERE nombre LIKE ? GROUP BY nombre ORDER BY prod_avg DESC""", (f"%{q}%",)).fetchall())
        return _rows(conn.execute(
            """SELECT c.nombre,ROUND(AVG(c.productividad),2) as prod_avg,
               ROUND(AVG(c.total_movimientos),0) as mov_avg,SUM(c.total_unidades) as unidades_total,COUNT(*) as dias
               FROM clientes c JOIN analisis a ON c.analisis_id=a.id
               WHERE a.user_id=? AND c.nombre LIKE ?
               GROUP BY c.nombre ORDER BY prod_avg DESC""", (user_id,f"%{q}%")).fetchall())

def historico_operario(codigo, limit=30, user_id=None, is_admin=False):
    with get_db() as conn:
        if is_admin:
            return _rows(conn.execute(
                "SELECT o.*,a.fecha FROM operarios o JOIN analisis a ON o.analisis_id=a.id WHERE o.codigo=? ORDER BY a.fecha DESC LIMIT ?",
                (codigo,limit)).fetchall())
        return _rows(conn.execute(
            "SELECT o.*,a.fecha FROM operarios o JOIN analisis a ON o.analisis_id=a.id WHERE o.codigo=? AND a.user_id=? ORDER BY a.fecha DESC LIMIT ?",
            (codigo,user_id,limit)).fetchall())

def historico_cliente(nombre, limit=30, user_id=None, is_admin=False):
    with get_db() as conn:
        if is_admin:
            return _rows(conn.execute(
                "SELECT c.*,a.fecha FROM clientes c JOIN analisis a ON c.analisis_id=a.id WHERE c.nombre=? ORDER BY a.fecha DESC LIMIT ?",
                (nombre,limit)).fetchall())
        return _rows(conn.execute(
            "SELECT c.*,a.fecha FROM clientes c JOIN analisis a ON c.analisis_id=a.id WHERE c.nombre=? AND a.user_id=? ORDER BY a.fecha DESC LIMIT ?",
            (nombre,user_id,limit)).fetchall())

def dashboard_data(user_id=None, is_admin=False):
    with get_db() as conn:
        if is_admin:
            wh, p = "", ()
        else:
            wh, p = "WHERE user_id=?", (user_id,)
        stats = conn.execute(f"""SELECT COUNT(*) as total_analisis,
            COALESCE(ROUND(AVG(total_movimientos),0),0) as avg_mov,
            COALESCE(ROUND(AVG(total_anomalias),1),0) as avg_anom,
            COALESCE(ROUND(AVG(tasa_anomalias_pct),2),0) as avg_tasa
            FROM analisis {wh}""", p).fetchone()
        tendencia = _rows(conn.execute(f"""SELECT fecha,total_movimientos,total_anomalias,tasa_anomalias_pct
            FROM analisis {wh} ORDER BY fecha DESC LIMIT 30""", p).fetchall())
        if is_admin:
            top_ops = _rows(conn.execute(
                """SELECT codigo,nombre,ROUND(AVG(productividad),2) as prod_avg,
                   ROUND(AVG(score_riesgo),1) as riesgo_avg,SUM(total_unidades) as unidades_total,COUNT(*) as dias
                   FROM operarios GROUP BY codigo ORDER BY prod_avg DESC LIMIT 10""").fetchall())
            top_cls = _rows(conn.execute(
                """SELECT nombre,ROUND(AVG(productividad),2) as prod_avg,
                   SUM(total_unidades) as unidades_total,COUNT(*) as dias
                   FROM clientes GROUP BY nombre ORDER BY unidades_total DESC LIMIT 10""").fetchall())
        else:
            top_ops = _rows(conn.execute(
                """SELECT o.codigo,o.nombre,ROUND(AVG(o.productividad),2) as prod_avg,
                   ROUND(AVG(o.score_riesgo),1) as riesgo_avg,SUM(o.total_unidades) as unidades_total,COUNT(*) as dias
                   FROM operarios o JOIN analisis a ON o.analisis_id=a.id
                   WHERE a.user_id=? GROUP BY o.codigo ORDER BY prod_avg DESC LIMIT 10""", (user_id,)).fetchall())
            top_cls = _rows(conn.execute(
                """SELECT c.nombre,ROUND(AVG(c.productividad),2) as prod_avg,
                   SUM(c.total_unidades) as unidades_total,COUNT(*) as dias
                   FROM clientes c JOIN analisis a ON c.analisis_id=a.id
                   WHERE a.user_id=? GROUP BY c.nombre ORDER BY unidades_total DESC LIMIT 10""", (user_id,)).fetchall())
        return {"global": dict(stats) if stats else {}, "tendencia": tendencia,
                "top_operarios": top_ops, "top_clientes": top_cls}

def comparar_analisis(id_a, id_b, user_id=None, is_admin=False):
    a = obtener_analisis(id_a, user_id, is_admin)
    b = obtener_analisis(id_b, user_id, is_admin)
    if not a or not b: return None
    return {"a": a, "b": b}

def obtener_historico_ops(user_id=None, is_admin=False):
    now = time.time()
    ck = f"{user_id}_{is_admin}"
    if _hist_cache.get("key")==ck and _hist_cache["data"] is not None and (now-_hist_cache["ts"])<_hist_cache["ttl"]:
        return _hist_cache["data"]
    hist = {}
    with get_db() as conn:
        if is_admin:
            rows = conn.execute(
                """SELECT codigo,AVG(total_anomalias) as avg_a,COUNT(*) as dias,
                   GROUP_CONCAT(tiempo_promedio_entre) as tiempos_csv
                   FROM operarios WHERE tiempo_promedio_entre>0 GROUP BY codigo HAVING dias>=5""").fetchall()
        else:
            rows = conn.execute(
                """SELECT o.codigo,AVG(o.total_anomalias) as avg_a,COUNT(*) as dias,
                   GROUP_CONCAT(o.tiempo_promedio_entre) as tiempos_csv
                   FROM operarios o JOIN analisis a ON o.analisis_id=a.id
                   WHERE a.user_id=? AND o.tiempo_promedio_entre>0
                   GROUP BY o.codigo HAVING dias>=5""", (user_id,)).fetchall()
        for r in rows:
            csv = r["tiempos_csv"]
            if not csv: continue
            vals = sorted([float(v) for v in csv.split(",") if v])
            if vals:
                idx = int(len(vals)*0.95)
                hist[r["codigo"]] = {"p95": vals[min(idx,len(vals)-1)], "avg_anomalias": r["avg_a"], "dias": r["dias"]}
    _hist_cache.update({"data": hist, "ts": now, "key": ck})
    return hist

def exportar_analisis(aid, user_id=None, is_admin=False):
    d = obtener_analisis(aid, user_id, is_admin)
    if not d: return None
    with get_db() as conn:
        ops = _rows(conn.execute("SELECT * FROM operarios WHERE analisis_id=? ORDER BY score_riesgo DESC", (aid,)).fetchall())
        cls = _rows(conn.execute("SELECT * FROM clientes WHERE analisis_id=? ORDER BY productividad DESC", (aid,)).fetchall())
        anoms = _rows(conn.execute("SELECT * FROM anomalias WHERE analisis_id=? AND justificada=0 ORDER BY exceso DESC", (aid,)).fetchall())
    return {"info": d, "operarios": ops, "clientes": cls, "anomalias": anoms}

def db_stats():
    size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    with get_db() as conn:
        counts = {}
        for t in ["analisis","operarios","clientes","anomalias","usuarios"]:
            counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    return {"path": DB_PATH, "size_mb": round(size/1048576,2), "registros": counts}

def compactar_db():
    size_before = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    conn = sqlite3.connect(DB_PATH)
    conn.execute("VACUUM")
    conn.close()
    size_after = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    return {"size_before_mb": round(size_before/1048576,2), "size_after_mb": round(size_after/1048576,2),
            "saved_mb": round((size_before-size_after)/1048576,2)}
