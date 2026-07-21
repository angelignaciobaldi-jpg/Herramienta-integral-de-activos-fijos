"""Versión de la aplicación (fuente única).

Mantén este número sincronizado con AppVersion de instalador.iss. El AutoUpdater
lo compara contra el tag_name de la última release de GitHub.

En el pipeline de CI (.github/workflows/compilar.yml), el tag del Release
SOBRESCRIBE este valor antes de compilar, así que en la práctica la versión
publicada la manda el tag. Este número es el punto de partida en desarrollo.
"""

from __future__ import annotations

__version__ = "0.1.0"
