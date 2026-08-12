"""Pipeline Runner CLI de AI Shorts Factory.

Orquesta el pipeline completo (contenido → imagen → audio → manifest → render)
a través de :class:`pipeline.PipelineRunner` dentro de un directorio de
ejecución aislado (:class:`pipeline.RunContext`).

Flujo:

1. Carga el entorno central (``load_project_env``).
2. Resuelve el tema (argumento posicional o ``PIPELINE_TOPIC``).
3. Crea un :class:`RunContext` (o usa el ``--run-id`` indicado) y prepara los
   directorios aislados ``output/runs/<run_id>/{input,output}``.
4. Ejecuta el :class:`PipelineRunner`, que corre las cinco etapas en orden y se
   detiene ante el primer fallo (conservando los artefactos del run para
   diagnóstico).

Modo ``--offline``: selecciona los proveedores sintéticos (contenido, imagen y
audio) sin modificar el ``.env``; no se realiza ninguna llamada HTTP.

Uso:

    python scripts/run_pipeline.py "Un eclipse solar total"
    python scripts/run_pipeline.py "Un eclipse solar total" --offline
    python scripts/run_pipeline.py --offline --project-id mi-short
    python scripts/run_pipeline.py --run-id run-20260811-143505 --offline
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# --- Ajuste del path para poder importar los paquetes de src/ ----------------
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config import load_project_env  # noqa: E402
from pipeline import (  # noqa: E402
    PipelineRunner,
    PipelineValidationError,
    RunContext,
)

logger = logging.getLogger("run_pipeline")

#: Nombre de la variable de entorno que provee el tema (alternativa al argv).
PIPELINE_TOPIC_ENV = "PIPELINE_TOPIC"


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser de argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description=(
            "Ejecuta el pipeline completo de un Short (contenido, imagen, "
            "audio, manifest y render) en un run aislado."
        ),
    )
    parser.add_argument(
        "topic",
        nargs="?",
        default=None,
        help="Tema del Short (alternativa a la variable PIPELINE_TOPIC).",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Identificador de la ejecución (run-YYYYMMDD-HHMMSS). "
        "Si se omite, se genera automáticamente.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Usa los proveedores sintéticos (sin API, sin red).",
    )
    parser.add_argument(
        "--project-id",
        default=None,
        help="Identificador del contenido (fuerza identity.id del manifest).",
    )
    return parser


def resolve_topic(args: argparse.Namespace) -> str | None:
    """Devuelve el tema del pipeline (argv o env), o ``None`` si falta."""
    topic = args.topic or os.environ.get(PIPELINE_TOPIC_ENV, "")
    return (topic or "").strip() or None


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada del script. Devuelve 0 en éxito, 1 en error."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = build_parser().parse_args(argv)
    load_project_env()

    topic = resolve_topic(args)
    if not topic:
        logger.error(
            "No se indicó un tema. Pásalo como argumento o define la "
            "variable de entorno '%s'.", PIPELINE_TOPIC_ENV,
        )
        return 1

    if args.run_id:
        try:
            context = RunContext(run_id=args.run_id)
        except PipelineValidationError as exc:
            logger.error("run_id inválido: %s", exc)
            return 1
    else:
        context = RunContext.create()
    logger.info("RunContext creado: run_id=%s", context.run_id)
    logger.info("Directorio de ejecución: %s", context.run_dir)

    runner = PipelineRunner(
        context,
        topic=topic,
        offline=args.offline,
        project_id=args.project_id,
    )
    result = runner.run()

    for stage in result.stages:
        level = logging.INFO if stage.success else logging.ERROR
        logger.log(
            level,
            "Etapa '%s': %s%s",
            stage.name,
            "OK" if stage.success else "FALLO",
            f" ({stage.error})" if stage.error else "",
        )

    if result.success:
        logger.info("Pipeline completado: run_id=%s", result.run_id)
        logger.info("Manifest: %s", result.project_path)
        logger.info("Video: %s", result.video_path)
        return 0

    logger.error(
        "Pipeline falló en la etapa '%s': %s",
        result.stages[-1].name if result.stages else "(sin etapas)",
        result.error or "desconocido",
    )
    logger.info(
        "Artefactos conservados para diagnóstico en: %s", context.run_dir
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
