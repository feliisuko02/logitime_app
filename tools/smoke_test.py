#!/usr/bin/env python3
"""Prueba rápida de la API principal de Logitime.

Uso:
  python tools/smoke_test.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app
from database import init_db


def assert_ok(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> int:
    init_db()
    nonce = int(time.time())
    almacen_base = f"Almacen QA {nonce}"
    almacen_renombrado = f"Almacen QA Renombrado {nonce}"
    proveedor_qa = f"Proveedor QA {nonce}"
    usuario_qa = f"usuario_qa_{nonce}"

    with app.test_client() as c:
        # 1) Login superadmin seed
        r = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        assert_ok(r.status_code == 200, f"Login falló: {r.status_code} {r.get_data(as_text=True)}")
        data = r.get_json() or {}
        user = data.get("user") or {}
        permisos = user.get("permisos") or {}
        assert_ok(permisos.get("manage_users") is True, "Admin seed sin manage_users")
        assert_ok(permisos.get("manage_settings") is True, "Admin seed sin manage_settings")
        assert_ok(permisos.get("manage_warehouses") is True, "Admin seed sin manage_warehouses")

        # 2) Contexto admin
        r = c.get("/api/admin/context")
        assert_ok(r.status_code == 200, f"/api/admin/context falló: {r.status_code}")
        ctx = r.get_json() or {}
        assert_ok(isinstance(ctx.get("almacenes"), list), "Contexto sin almacenes")
        assert_ok(isinstance(ctx.get("proveedores"), list), "Contexto sin proveedores")
        assert_ok(ctx.get("can_manage_users") is True, "Contexto admin sin can_manage_users")
        assert_ok(ctx.get("can_manage_settings") is True, "Contexto admin sin can_manage_settings")
        assert_ok(ctx.get("can_manage_warehouses") is True, "Contexto admin sin can_manage_warehouses")

        # 3) Crear almacén
        r = c.post("/api/admin/almacenes", json={"nombre": almacen_base})
        assert_ok(r.status_code == 200, f"Crear almacén devolvió {r.status_code}")

        # 4) Renombrar almacén
        almacen_qa_id = None
        if r.status_code == 200:
            almacen_qa_id = (r.get_json() or {}).get("id")
        if not almacen_qa_id:
            r_ctx = c.get("/api/admin/context")
            almacenes_ctx = (r_ctx.get_json() or {}).get("almacenes") or []
            found = next((a for a in almacenes_ctx if a.get("nombre") == almacen_base), None)
            if found:
                almacen_qa_id = found.get("id")
        assert_ok(bool(almacen_qa_id), "No se encontró ID de almacén QA")
        r = c.put(f"/api/admin/almacenes/{almacen_qa_id}", json={"nombre": almacen_renombrado})
        assert_ok(r.status_code == 200, f"Renombrar almacén devolvió {r.status_code}")

        # 5) Crear proveedor
        r = c.post("/api/admin/proveedores", json={"nombre": proveedor_qa})
        assert_ok(r.status_code == 200, f"Crear proveedor devolvió {r.status_code}")

        # 6) Obtener contexto actualizado
        r = c.get("/api/admin/context")
        assert_ok(r.status_code == 200, "No se pudo recargar contexto")
        ctx = r.get_json() or {}
        almacenes = ctx.get("almacenes") or []
        proveedores = ctx.get("proveedores") or []
        assert_ok(any(a.get("nombre") == almacen_renombrado for a in almacenes), "No aparece almacén renombrado")
        assert_ok(any(p.get("nombre") == proveedor_qa for p in proveedores), "No aparece proveedor QA")

        # 7) Crear usuario restringido por proveedor y permisos
        almacen_qa = next((a for a in almacenes if a.get("nombre") == almacen_renombrado), None)
        assert_ok(almacen_qa is not None, "No existe almacén QA para prueba")
        payload = {
            "username": usuario_qa,
            "password": "qa1234",
            "nombre": "Usuario QA",
            "almacen_id": almacen_qa.get("id"),
            "proveedores": [proveedor_qa],
            "permisos": {
                "analizar": True,
                "dashboard": True,
                "operarios": True,
                "clientes": True,
                "historial": True,
                "comparar": False,
                "manage_users": False,
                "manage_settings": False,
            },
        }
        r = c.post("/api/admin/usuarios", json=payload)
        assert_ok(r.status_code == 200, f"Crear usuario QA devolvió {r.status_code}")

        # 8) Validar estructura en listado de usuarios
        r = c.get("/api/admin/usuarios")
        assert_ok(r.status_code == 200, "No se pudo listar usuarios")
        users = r.get_json() or []
        uqa = next((u for u in users if u.get("username") == usuario_qa), None)
        assert_ok(uqa is not None, "usuario_qa no existe en listado")
        assert_ok("permisos" in uqa and isinstance(uqa["permisos"], dict), "usuario_qa sin permisos")
        assert_ok("proveedores" in uqa and isinstance(uqa["proveedores"], list), "usuario_qa sin proveedores")

        # 9) Proteger cuenta admin: no desactivable
        admin_user = next((u for u in users if u.get("username") == "admin"), None)
        assert_ok(admin_user is not None, "No existe admin en listado")
        r = c.delete(f"/api/admin/usuarios/{admin_user['id']}")
        assert_ok(r.status_code in (400, 403), f"La cuenta admin deberia estar protegida, devolvio {r.status_code}")

        # 10) Usuario sin permisos de admin no puede abrir /api/admin/context
        c.post("/api/auth/logout")
        r = c.post("/api/auth/login", json={"username": usuario_qa, "password": "qa1234"})
        assert_ok(r.status_code == 200, "No se pudo iniciar sesion con usuario_qa")
        r = c.get("/api/admin/context")
        assert_ok(r.status_code == 403, "usuario_qa no deberia acceder a contexto admin")

        # 11) Health endpoint debe responder ok
        r = c.get("/api/health")
        assert_ok(r.status_code == 200, f"Health endpoint fallo: {r.status_code}")
        h = r.get_json() or {}
        assert_ok(h.get("ok") is True, "Health endpoint no devolvio ok=true")

        # 12) Crear usuario con almacen invalido debe fallar
        c.post("/api/auth/logout")
        c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        bad_payload = {
            "username": f"bad_alm_{nonce}",
            "password": "qa1234",
            "nombre": "Bad Alm",
            "almacen_id": 999999,
            "proveedores": [],
            "permisos": {"analizar": True},
        }
        r = c.post("/api/admin/usuarios", json=bad_payload)
        assert_ok(r.status_code == 400, f"Esperado 400 por almacen invalido, devolvio {r.status_code}")

    print(json.dumps({"ok": True, "checks": 12}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise
