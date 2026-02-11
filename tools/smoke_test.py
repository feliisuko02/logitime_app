#!/usr/bin/env python3
"""Prueba rápida de la API principal de Logitime.

Uso:
  python tools/smoke_test.py
"""

from __future__ import annotations

import json
import sys
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

    with app.test_client() as c:
        # 1) Login superadmin seed
        r = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        assert_ok(r.status_code == 200, f"Login falló: {r.status_code} {r.get_data(as_text=True)}")
        data = r.get_json() or {}
        user = data.get("user") or {}
        assert_ok(user.get("rol") == "superadmin", f"Rol esperado superadmin, recibido: {user.get('rol')}")

        # 2) Contexto admin
        r = c.get("/api/admin/context")
        assert_ok(r.status_code == 200, f"/api/admin/context falló: {r.status_code}")
        ctx = r.get_json() or {}
        assert_ok(isinstance(ctx.get("almacenes"), list), "Contexto sin almacenes")
        assert_ok(isinstance(ctx.get("proveedores"), list), "Contexto sin proveedores")

        # 3) Crear almacén
        r = c.post("/api/admin/almacenes", json={"nombre": "Almacen QA"})
        assert_ok(r.status_code in (200, 409), f"Crear almacén devolvió {r.status_code}")

        # 4) Renombrar almacén
        almacen_qa_id = None
        if r.status_code == 200:
            almacen_qa_id = (r.get_json() or {}).get("id")
        if not almacen_qa_id:
            r_ctx = c.get("/api/admin/context")
            almacenes_ctx = (r_ctx.get_json() or {}).get("almacenes") or []
            found = next((a for a in almacenes_ctx if a.get("nombre") == "Almacen QA"), None)
            if found:
                almacen_qa_id = found.get("id")
        assert_ok(bool(almacen_qa_id), "No se encontró ID de Almacen QA")
        r = c.put(f"/api/admin/almacenes/{almacen_qa_id}", json={"nombre": "Almacen QA Renombrado"})
        assert_ok(r.status_code == 200, f"Renombrar almacén devolvió {r.status_code}")

        # 5) Crear proveedor
        r = c.post("/api/admin/proveedores", json={"nombre": "Proveedor QA"})
        assert_ok(r.status_code in (200, 409), f"Crear proveedor devolvió {r.status_code}")

        # 6) Obtener contexto actualizado
        r = c.get("/api/admin/context")
        assert_ok(r.status_code == 200, "No se pudo recargar contexto")
        ctx = r.get_json() or {}
        almacenes = ctx.get("almacenes") or []
        proveedores = ctx.get("proveedores") or []
        assert_ok(any(a.get("nombre") == "Almacen QA Renombrado" for a in almacenes), "No aparece Almacen QA Renombrado")
        assert_ok(any(p.get("nombre") == "Proveedor QA" for p in proveedores), "No aparece Proveedor QA")

        # 7) Crear usuario restringido por proveedor y permisos
        almacen_qa = next((a for a in almacenes if a.get("nombre") == "Almacen QA Renombrado"), None)
        assert_ok(almacen_qa is not None, "No existe almacén QA para prueba")
        payload = {
            "username": "usuario_qa",
            "password": "qa1234",
            "nombre": "Usuario QA",
            "rol": "user",
            "almacen_id": almacen_qa.get("id"),
            "proveedores": ["Proveedor QA"],
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
        assert_ok(r.status_code in (200, 409), f"Crear usuario QA devolvió {r.status_code}")

        # 8) Validar estructura en listado de usuarios
        r = c.get("/api/admin/usuarios")
        assert_ok(r.status_code == 200, "No se pudo listar usuarios")
        users = r.get_json() or []
        uqa = next((u for u in users if u.get("username") == "usuario_qa"), None)
        assert_ok(uqa is not None, "usuario_qa no existe en listado")
        assert_ok("permisos" in uqa and isinstance(uqa["permisos"], dict), "usuario_qa sin permisos")
        assert_ok("proveedores" in uqa and isinstance(uqa["proveedores"], list), "usuario_qa sin proveedores")

    print(json.dumps({"ok": True, "checks": 8}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise
