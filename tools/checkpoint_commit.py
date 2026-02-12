#!/usr/bin/env python3
"""Crea un commit de checkpoint si hay cambios pendientes.

Uso:
  python tools/checkpoint_commit.py "checkpoint: mensaje"
"""

from __future__ import annotations

import subprocess
import sys


def run(cmd: list[str]) -> str:
    p = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return p.stdout.strip()


def main() -> int:
    msg = sys.argv[1] if len(sys.argv) > 1 else "checkpoint: progreso"
    status = run(["git", "status", "--short"])
    if not status:
        print("Sin cambios; no se crea checkpoint.")
        return 0
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-m", msg], check=True)
    print(run(["git", "log", "--oneline", "-n", "1"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

