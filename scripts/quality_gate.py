"""Ejecuta el Quality Gate sobre el directorio de salida de un run.

Inspecciona (estrictamente en modo de solo lectura) el directorio de salida de
un run y determina si produjo un Short técnicamente válido: contenido JSON,
manifest, imágenes, audio, video, duraciones, sincronización A/V, provenance y
residuos. Reutiliza :func:`quality.run_quality_gate`; no escribe nada.

Códigos de salida:

- 0: el gate pasó (ningún check de severidad ``error`` fallido).
- 1: el gate falló (algún check de severidad ``error`` fallido) o hubo un error
  de configuración (directorio inexistente).

Uso:

    python scripts/quality_gate.py [directorio_de_salida]
    python scripts/quality_gate.py output/runs/run-20260812-093000/output
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# --- Ajuste del path para poder importar los paquetes de src/ ----------------
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from quality import (  # noqa: E402
    QualityGateError,
    run_quality_gate,
)

logger = logging.getLogger("quality_gate")

#: Directorio de salida por defecto (legacy: la salida global del pipeline).
DEFAULT_OUTPUT_DIR = ROOT / "output"


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser de argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description=(
            "Ejecuta el Quality Gate sobre el directorio de salida de un run "
            "y devuelve su veredicto."
        ),
    )
    parser.add_argument(
        "output_dir",
        nargs="?",
        default=DEFAULT_OUTPUT_DIR,
        help="Directorio de salida a inspeccionar (por defecto, output/).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada del script. Devuelve 0 si el gate pasa, 1 si falla."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = build_parser().parse_args(argv)

    output_dir = Path(args.output_dir).expanduser()
    try:
        result = run_quality_gate(output_dir)
    except QualityGateError as exc:
        logger.error("No se pudo ejecutar el Quality Gate: %s", exc)
        return 1

    for check in result.checks:
        if check.passed:
            logger.info("  OK  %s: %s", check.name, check.message)
        elif check.severity == "warning":
            logger.warning("  ~~  %s: %s", check.name, check.message)
        else:
            logger.error("  XX  %s: %s", check.name, check.message)

    if result.passed:
        logger.info(
            "Quality Gate: PASS (errores=%d, avisos=%d).",
            result.errors,
            result.warnings,
        )
        return 0
    logger.error(
        "Quality Gate: FAIL (errores=%d, avisos=%d).",
        result.errors,
        result.warnings,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())