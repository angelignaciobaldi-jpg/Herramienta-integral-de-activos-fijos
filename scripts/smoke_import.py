"""Smoke test de imports: importa app + todos los módulos de core/ y ui/.

Sirve para detectar imports rotos o módulos faltantes (p. ej. un .py referenciado
pero NO versionado) ANTES de compilar y publicar. Lo corre el CI: si algo no
importa, el build FALLA aquí y el Release roto no llega a los usuarios.

Ejecuta:  python scripts/smoke_import.py
"""

from __future__ import annotations

import importlib
import os
import pkgutil
import sys

# Raíz del proyecto en el path (para poder importar core/ y ui/).
_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _RAIZ)


def _modulos_de(paquete: str) -> list[str]:
    ruta = os.path.join(_RAIZ, paquete)
    return [f"{paquete}.{m.name}" for m in pkgutil.iter_modules([ruta])]


def main() -> int:
    objetivos = ["app"] + _modulos_de("core") + _modulos_de("ui")
    fallidos: list[tuple[str, str]] = []
    for nombre in objetivos:
        try:
            importlib.import_module(nombre)
            print(f"OK  {nombre}")
        except Exception as exc:  # noqa: BLE001 — se reporta el módulo que falla
            fallidos.append((nombre, f"{type(exc).__name__}: {exc}"))
            print(f"XX  {nombre}  ->  {type(exc).__name__}: {exc}")
    if fallidos:
        print("\nImports fallidos:")
        for nombre, err in fallidos:
            print(f"  - {nombre}: {err}")
        return 1
    print(f"\nTodos los módulos importan correctamente ({len(objetivos)}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
