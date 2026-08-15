"""Pipeline Runner CLI de AI Shorts Factory.

Orquesta el pipeline completo (contenido → imagen → audio → manifest → render →
quality) a través de :class:`pipeline.PipelineRunner` dentro de un directorio de
ejecución aislado (:class:`pipeline.RunContext`).

Flujo:

1. Carga el entorno central (``load_project_env``).
2. Resuelve el tema (argumento posicional o ``PIPELINE_TOPIC``).
3. Crea un :class:`RunContext` (o usa el ``--run-id`` indicado) y prepara los
   directorios aislados ``output/runs/<run_id>/{input,output}``.
4. Ejecuta el :class:`PipelineRunner`, que corre las seis etapas en orden. La
   etapa ``quality`` verifica el run con el Quality Gate solo si el render
   terminó bien; si el gate falla, el pipeline se marca como fallido (los
   artefactos se conservan).

Códigos de salida:

- 0: el pipeline y el Quality Gate pasaron.
- 1: falló alguna etapa (incluido el Quality Gate).

Modo ``--offline``: selecciona los proveedores sintéticos (contenido, imagen y
audio) sin modificar el ``.env``; no se realiza ninguna llamada HTTP.

Uso:

    python scripts/run_pipeline.py "Un eclipse solar total"
    python scripts/run_pipeline.py "Un eclipse solar total" --offline
    python scripts/run_pipeline.py --offline --project-id mi-short
    python scripts/run_pipeline.py --run-id run-20260811-143505 --offline
    python scripts/run_pipeline.py --list-runs
    python scripts/run_pipeline.py --inspect-run run-20260811-143505

Cada run persiste su estado en ``output/runs/<run_id>/run.json`` (escritura
atómica): ``RUNNING`` al preparar el run y ``SUCCESS``/``FAILED``/
``QUALITY_FAILED`` al finalizar (un crash deja el run en ``RUNNING``, detectable
por antigüedad). ``--list-runs`` y ``--inspect-run`` son solo lectura.
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
    PipelineError,
    PipelineRunner,
    PipelineValidationError,
    RunContext,
    load_run_record,
    unknown_record,
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
    parser.add_argument(
        "--list-runs",
        action="store_true",
        help="Lista los runs existentes (estado persistido en run.json).",
    )
    parser.add_argument(
        "--inspect-run",
        metavar="RUN_ID",
        default=None,
        help="Inspecciona un run concreto (run-YYYYMMDD-HHMMSS).",
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

    if args.list_runs:
        return _cmd_list_runs()

    if args.inspect_run:
        return _cmd_inspect_run(args.inspect_run)

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
    try:
        result = runner.run()
    except PipelineError as exc:
        logger.error("Error del pipeline: %s", exc)
        return 1

    for stage in result.stages:
        level = logging.INFO if stage.success else logging.ERROR
        logger.log(
            level,
            "Etapa '%s': %s%s",
            stage.name,
            "OK" if stage.success else "FALLO",
            f" ({stage.error})" if stage.error else "",
        )

    _log_quality_result(result)

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


def _cmd_list_runs() -> int:
    """Lista los runs existentes (estado persistido en ``run.json``)."""
    from pipeline import RUNS_ROOT, list_runs

    records = list_runs(RUNS_ROOT)
    if not records:
        logger.info("No hay runs en %s", RUNS_ROOT)
        return 0
    logger.info("Runs en %s (%d):", RUNS_ROOT, len(records))
    for record in records:
        logger.info(
            "  %s  status=%s  creado=%s  finalizado=%s%s",
            record.run_id,
            record.status.value,
            record.created_at.isoformat() if record.created_at else "-",
            record.finished_at.isoformat() if record.finished_at else "-",
            _quality_tag(record),
        )
    return 0


def _cmd_inspect_run(run_id: str) -> int:
    """Inspecciona un run concreto sin ejecutar ninguna etapa."""
    from pipeline import RUNS_ROOT
    from pipeline.lifecycle import checked_run_dir

    try:
        run_dir = checked_run_dir(RUNS_ROOT / run_id)
    except PipelineValidationError as exc:
        logger.error("run_id inválido: %s", exc)
        return 1
    if not run_dir.is_dir():
        logger.error("No existe el run: %s", run_id)
        return 1

    record = load_run_record(run_dir)
    if record is None:
        record = unknown_record(run_dir)
        logger.warning("run.json ausente o ilegible; estado UNKNOWN.")

    logger.info("run_id: %s", record.run_id)
    logger.info("status: %s", record.status.value)
    logger.info(
        "creado: %s | iniciado: %s | finalizado: %s",
        record.created_at.isoformat() if record.created_at else "-",
        record.started_at.isoformat() if record.started_at else "-",
        record.finished_at.isoformat() if record.finished_at else "-",
    )
    logger.info(
        "quality_passed: %s",
        record.quality_passed if record.quality_passed is not None else "-",
    )
    if record.error:
        logger.info("error: %s", record.error)

    output = run_dir / "output"
    images = len(list((output / "images").glob("scene_*.png"))) if (output / "images").is_dir() else 0
    videos = list((output / "video").glob("*.mp4")) if (output / "video").is_dir() else []
    audio_files = list((output / "audio").iterdir()) if (output / "audio").is_dir() else []
    logger.info(
        "artefactos: content=%s project=%s images=%d audio=%d video=%d",
        (output / "content.json").is_file(),
        (output / "project.json").is_file(),
        images,
        len(audio_files),
        len(videos),
    )
    return 0


def _quality_tag(record) -> str:
    """Sufijo legible del veredicto del Quality Gate para el listado."""
    if record.quality_passed is None:
        return ""
    return "  quality=PASS" if record.quality_passed else "  quality=FAIL"


def _log_quality_result(result) -> None:
    """Registra el veredicto del Quality Gate en el log del CLI."""
    quality = getattr(result, "quality_result", None)
    if quality is None:
        return
    if quality.passed:
        logger.info(
            "Quality Gate: PASS (errores=%d, avisos=%d)",
            quality.errors,
            quality.warnings,
        )
    else:
        logger.error(
            "Quality Gate: FAIL (errores=%d, avisos=%d)",
            quality.errors,
            quality.warnings,
        )
    for check in quality.failed_checks:
        logger.error("  [gate] %s: %s", check.name, check.message)
    for check in quality.warning_checks:
        logger.warning("  [gate] %s: %s", check.name, check.message)


if __name__ == "__main__":
    sys.exit(main())
