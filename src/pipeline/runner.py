"""PipelineRunner: orquestador end-to-end del pipeline de AI Shorts Factory.

Implementa el orquestador real de ME27: ejecuta las seis etapas del pipeline
(contenido → imagen → audio → manifest → render → quality) dentro de un
directorio de ejecución aislado (:class:`pipeline.context.RunContext` /
``RunDirectory``), reutilizando las funciones internas de los scripts en lugar
de invocarlos por ``subprocess``.

Principios:

- **Aislamiento**: todas las etapas trabajan sobre ``context.output_dir``; no se
  toca ``output/`` global (legacy).
- **Detención ante fallo**: si una etapa falla (incluido el Quality Gate), no se
  ejecutan las siguientes y el pipeline se marca como fallido.
- **Resultado estructurado**: :class:`pipeline.models.PipelineResult` con el
  resultado por etapa (:class:`pipeline.models.StageResult`) y el veredicto del
  Quality Gate (:class:`quality.models.QualityGateResult`).
- **Artefactos conservados**: un run fallido no se borra; queda en
  ``output/runs/<run_id>/`` para diagnóstico.
- **Sin ``subprocess``**: las etapas importan y llaman directamente las
  funciones extraídas de ``scripts/*.py`` (cada script conserva su CLI).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from .context import RunContext
from .models import PipelineResult, StageResult, _utc_now
from .exceptions import PipelineValidationError

logger = logging.getLogger(__name__)

#: Raíz del proyecto (dos niveles por encima de este módulo).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
#: Directorio donde viven los scripts de cada etapa.
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

#: Nombres de las etapas en orden de ejecución.
STAGE_CONTENT = "content"
STAGE_IMAGE = "image"
STAGE_AUDIO = "audio"
STAGE_MANIFEST = "manifest"
STAGE_RENDER = "render"
STAGE_QUALITY = "quality"

STAGE_ORDER: tuple[str, ...] = (
    STAGE_CONTENT,
    STAGE_IMAGE,
    STAGE_AUDIO,
    STAGE_MANIFEST,
    STAGE_RENDER,
    STAGE_QUALITY,
)

#: Variable de entorno que selecciona el proveedor de contenido.
CONTENT_PROVIDER_ENV = "GEMINI_CONTENT_PROVIDER"


class PipelineRunner:
    """Orquesta las etapas del pipeline dentro de un :class:`RunContext`.

    Args:
        context: contexto de la ejecución (establece ``run_id`` y los
            directorios aislados).
        topic: tema del Short (obligatorio para la etapa de contenido).
        offline: si ``True``, selecciona los proveedores sintéticos (imagen,
            audio y contenido) sin modificar el ``.env``.
        project_id: identificador opcional del contenido; si se indica, fuerza
            ``identity.id`` en el ContentPackage.
    """

    def __init__(
        self,
        context: RunContext,
        *,
        topic: Optional[str] = None,
        offline: bool = False,
        project_id: Optional[str] = None,
    ) -> None:
        if not isinstance(context, RunContext):
            raise TypeError("'context' debe ser un RunContext.")
        self._context = context
        self._topic = (topic or "").strip() or None
        self._offline = bool(offline)
        self._project_id = project_id
        self._quality_result: Optional[object] = None

    @property
    def context(self) -> RunContext:
        """Contexto de la ejecución."""
        return self._context

    def run(self) -> PipelineResult:
        """Ejecuta las seis etapas del pipeline en orden.

        Crea los directorios del run (implícito), aplica el entorno offline si
        corresponde y ejecuta las etapas hasta el primer fallo. La etapa
        ``quality`` corre solo si el render terminó bien y determina el
        ``success`` final (un run que no pasa el gate es un fallo).

        Returns:
            :class:`PipelineResult` con el resultado de cada etapa ejecutada
            y el veredicto del Quality Gate.
        """
        context = self._context
        logger.info("Preparando ejecución run_id=%s...", context.run_id)
        context.prepare()

        if self._offline:
            self._apply_offline_environment()
            logger.info(
                "Modo offline: proveedores sintéticos activados "
                "(imagen, audio y contenido)."
            )

        stages: list[StageResult] = []
        failed: Optional[StageResult] = None
        for name in STAGE_ORDER:
            stage = self._execute_stage(name)
            stages.append(stage)
            if not stage.success:
                failed = stage
                logger.error(
                    "Pipeline detenido: la etapa '%s' falló (%s).",
                    name,
                    stage.error or "código de salida distinto de 0",
                )
                break

        success = failed is None
        output_dir = context.output_dir
        project_path = output_dir / "project.json"

        # El video_path se conserva si el render terminó bien, aunque el
        # Quality Gate falle después (los artefactos se conservan para
        # diagnóstico; el run sigue siendo un fallo).
        render_succeeded = any(
            stage.name == STAGE_RENDER and stage.success for stage in stages
        )
        video_path = (
            self._find_video(output_dir / "video") if render_succeeded else None
        )

        if success:
            logger.info(
                "Pipeline completado: run_id=%s video=%s",
                context.run_id,
                video_path,
            )
        else:
            logger.info(
                "Run fallido conservado para diagnóstico en: %s",
                context.run_dir,
            )

        return PipelineResult(
            run_id=context.run_id,
            success=success,
            stages=tuple(stages),
            project_path=project_path if project_path.is_file() else None,
            video_path=video_path,
            quality_result=self._quality_result,
            error=failed.error if failed else None,
        )

    # ------------------------------------------------------------------
    # Ejecución de etapas
    # ------------------------------------------------------------------

    def _execute_stage(self, name: str) -> StageResult:
        """Ejecuta una etapa y registra timestamps, éxito o error."""
        started_at = _utc_now()
        error: Optional[str] = None
        success = False
        try:
            exit_code = self._call_stage(name)
            success = exit_code == 0
            if not success:
                if name == STAGE_QUALITY and self._quality_result is not None:
                    error = self._quality_failure_message(self._quality_result)
                else:
                    error = f"La etapa '{name}' terminó con código {exit_code}."
        except PipelineValidationError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - capturar y registrar el fallo
            logger.exception("Fallo no controlado en la etapa '%s'.", name)
            error = f"Error no controlado en la etapa '{name}': {exc}"
        finished_at = _utc_now()
        logger.info(
            "Etapa '%s': %s.",
            name,
            "OK" if success else f"FALLO ({error})",
        )
        return StageResult(
            name=name,
            success=success,
            started_at=started_at,
            finished_at=finished_at,
            error=error,
        )

    def _call_stage(self, name: str) -> int:
        """Despacha la etapa a su función interna correspondiente."""
        if name == STAGE_CONTENT:
            return self._run_content()
        if name == STAGE_IMAGE:
            return self._run_image()
        if name == STAGE_AUDIO:
            return self._run_audio()
        if name == STAGE_MANIFEST:
            return self._run_manifest()
        if name == STAGE_RENDER:
            return self._run_render()
        if name == STAGE_QUALITY:
            return self._run_quality()
        raise PipelineValidationError(f"Etapa desconocida: {name!r}")

    def _run_content(self) -> int:
        """Etapa de contenido: escribe ``output_dir/content.json``."""
        if not self._topic:
            raise PipelineValidationError(
                "La etapa de contenido requiere un tema (argumento o "
                "PIPELINE_TOPIC)."
            )
        from generate_content import generate_content_to_path

        return generate_content_to_path(
            self._topic,
            self._context.output_dir / "content.json",
            project_id=self._project_id,
        )

    def _run_image(self) -> int:
        """Etapa de imagen: genera imágenes en ``output_dir/images/``."""
        from generate_image import generate_images_to_path

        return generate_images_to_path(
            self._context.output_dir / "content.json",
            self._context.output_dir / "images",
        )

    def _run_audio(self) -> int:
        """Etapa de audio: genera la narración en ``output_dir/audio/``."""
        from generate_audio import generate_audio_to_path

        return generate_audio_to_path(
            self._context.output_dir / "content.json",
            self._context.output_dir / "audio",
        )

    def _run_manifest(self) -> int:
        """Etapa de manifest: genera ``output_dir/project.json``."""
        from generate_manifest import generate_manifest_to_path

        return generate_manifest_to_path(
            self._context.output_dir,
            run_id=self._context.run_id,
        )

    def _run_render(self) -> int:
        """Etapa de render: genera el video en ``output_dir/video/``."""
        from render_video import render_video_to_path

        return render_video_to_path(self._context.output_dir / "project.json")

    def _run_quality(self) -> int:
        """Etapa de quality: verifica el run con el Quality Gate (read-only).

        La inspección es estrictamente de solo lectura: no borra ni regenera
        artefactos. Devuelve 0 si el run pasa el gate (sin errores) o 1 en
        caso contrario.
        """
        from quality import run_quality_gate

        result = run_quality_gate(self._context.output_dir)
        self._quality_result = result
        return 0 if result.passed else 1

    @staticmethod
    def _quality_failure_message(result: object) -> str:
        """Compone el mensaje de error de la etapa quality a partir de los
        checks fallidos del gate."""
        failed = [c for c in result.checks if c.severity == "error" and not c.passed]
        if failed:
            detail = "; ".join(check.message for check in failed)
            return f"Quality Gate FAIL ({len(failed)} check(s) de error): {detail}"
        return "Quality Gate FAIL."

    # ------------------------------------------------------------------
    # Ayudantes
    # ------------------------------------------------------------------

    def _apply_offline_environment(self) -> None:
        """Selecciona proveedores sintéticos en ``os.environ`` (sin .env)."""
        os.environ[CONTENT_PROVIDER_ENV] = "synthetic"
        os.environ["GEMINI_IMAGE_PROVIDER"] = "synthetic"
        os.environ["GEMINI_AUDIO_PROVIDER"] = "synthetic"

    @staticmethod
    def _find_video(video_dir: Path) -> Optional[Path]:
        """Devuelve el primer archivo de video encontrado (o ``None``)."""
        if not video_dir.is_dir():
            return None
        for entry in sorted(video_dir.iterdir()):
            if entry.is_file() and entry.suffix.lower() in {".mp4", ".mov", ".webm"}:
                return entry
        return None
