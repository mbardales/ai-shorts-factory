"""Workflow Engine de AI Shorts Factory.

Orquesta la generación completa de un Short ejecutando secuencialmente los
scripts del pipeline como procesos independientes:

1. ``generate_content.py``: tema → ``output/content.json`` (Content Package).
2. ``generate_image.py``: escenas → ``output/images/scene_*.png``.
3. ``generate_audio.py``: narración → ``output/audio/narration.mp3``.

No duplica la lógica de los pipelines ni importa sus módulos internos: solo
los ejecuta con :mod:`subprocess`, registra el ciclo de vida de cada etapa
(inicio, fin, duración y resultado) y se detiene ante el primer fallo.

El tema se puede indicar como argumento posicional o con la variable de
entorno ``PIPELINE_TOPIC`` (la primera etapa lo pide por consola).

Uso:

    python scripts/run_pipeline.py "Un eclipse solar total"
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

#: Raíz del proyecto (directorio padre de ``scripts/``).
ROOT = Path(__file__).resolve().parents[1]
#: Directorio donde viven los scripts de cada etapa.
SCRIPTS_DIR = ROOT / "scripts"
#: Nombre de la variable de entorno que provee el tema (alternativa al argv).
PIPELINE_TOPIC_ENV = "PIPELINE_TOPIC"

#: Etapas del pipeline en orden de ejecución: (nombre, script).
STAGES: tuple[tuple[str, str], ...] = (
    ("contenido", "generate_content.py"),
    ("imagen", "generate_image.py"),
    ("audio", "generate_audio.py"),
)

logger = logging.getLogger("run_pipeline")


def run_stage(name: str, script: Path, *, topic: Optional[str] = None) -> int:
    """Ejecuta una etapa y registra su inicio, duración y resultado.

    Args:
        name: nombre legible de la etapa (para los logs).
        script: ruta al script de la etapa.
        topic: tema a enviar por stdin si la etapa lo solicita (``None`` si
            la etapa no consume entrada estándar).

    Returns:
        Código de salida del proceso de la etapa.

    Raises:
        OSError: si el script no se puede ejecutar.
    """
    start = time.perf_counter()
    logger.info("Iniciando etapa '%s' (%s)...", name, script.name)
    command = [sys.executable, str(script)]
    if topic is not None:
        logger.info("Enviando tema a la etapa '%s'.", name)
        proc = subprocess.run(
            command,
            cwd=ROOT,
            input=topic + "\n",
            text=True,
            encoding="utf-8",
        )
    else:
        proc = subprocess.run(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
        )
    elapsed = time.perf_counter() - start

    if proc.returncode == 0:
        logger.info(
            "Etapa '%s' completada en %.2fs (código %d).", name, elapsed, proc.returncode
        )
    else:
        logger.error(
            "La etapa '%s' falló tras %.2fs (código %d).",
            name,
            elapsed,
            proc.returncode,
        )
    return proc.returncode


def resolve_topic() -> Optional[str]:
    """Devuelve el tema del pipeline (argv o env), o ``None`` si falta."""
    topic = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(PIPELINE_TOPIC_ENV, "")
    return (topic or "").strip() or None


def main() -> int:
    """Punto de entrada del script. Devuelve 0 en éxito, 1 en error."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    topic = resolve_topic()
    if not topic:
        logger.error(
            "No se indicó un tema. Pásalo como argumento o define la "
            "variable de entorno '%s'.", PIPELINE_TOPIC_ENV,
        )
        return 1

    stages: list[tuple[str, Path]] = []
    for name, filename in STAGES:
        script = SCRIPTS_DIR / filename
        if not script.is_file():
            logger.error("No se encontró el script de la etapa '%s': %s", name, script)
            return 1
        stages.append((name, script))

    pipeline_start = time.perf_counter()
    logger.info("Iniciando pipeline de generación para: %s", topic)
    for name, script in stages:
        try:
            returncode = run_stage(name, script, topic=topic if name == "contenido" else None)
        except OSError as exc:
            logger.error("No se pudo ejecutar la etapa '%s': %s", name, exc)
            return 1
        if returncode != 0:
            logger.error("Pipeline detenido por fallo en la etapa '%s'.", name)
            return 1

    elapsed = time.perf_counter() - pipeline_start
    logger.info("Pipeline completado en %.2fs.", elapsed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
