"""Quality Gate read-only de AI Shorts Factory.

Inspecciona el directorio de salida de un run (la salida de un
:class:`pipeline.RunContext`, p. ej. ``output/runs/<run_id>/output/``) y
determina si el run produjo un Short técnicamente válido. La inspección es
**estrictamente de solo lectura**: no escribe ningún artefacto, no hace HTTP y
no ejecuta comandos con ``shell``.

Reutiliza los validadores de dominio existentes para no duplicarlos:

- ``content.content_package_from_json`` + ``content.find_errors`` para el
  ContentPackage.
- ``project.project_manifest_from_json`` + ``project.validate_project_manifest``
  para el ProjectManifest.

Añade checks de artefactos que hoy no existen en el repo (decode ligero de PNG
con ``struct``/``zlib`` de la stdlib, decode de WAV con ``wave`` e inspección
de MP3/MP4 con ``ffprobe`` mediante el patrón seguro del repo: ``argv`` +
``shell=False`` + timeout).

Modo de uso:

    from quality import run_quality_gate

    result = run_quality_gate(output_dir)
    if not result.passed:
        for check in result.failed_checks:
            print(check.message)
"""

from __future__ import annotations

import json
import logging
import shutil
import struct
import subprocess
import wave
import zlib
from pathlib import Path

from content import (
    ContentValidationError,
    content_package_from_json,
    content_package_to_dict,
    find_errors as find_content_errors,
)
from project import (
    AssetKind,
    ProjectManifest,
    ProjectNotFoundError,
    ProjectValidationError,
    project_manifest_from_json,
    validate_project_manifest,
)

from .exceptions import QualityGateError
from .models import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    QualityCheck,
    QualityGateResult,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes de contrato del pipeline (fuente de verdad del render).
# ---------------------------------------------------------------------------

#: Nombre canónico de los archivos JSON del run.
CONTENT_FILE = "content.json"
MANIFEST_FILE = "project.json"

#: Extensiones de activos reconocidas por el manifest (generate_manifest.py).
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
AUDIO_EXTENSIONS = {"mp3", "wav", "ogg", "m4a"}

#: Extensiones de video aceptadas por PipelineRunner._find_video.
VIDEO_EXTENSIONS = {"mp4", "mov", "webm"}

#: Nombre del sidecar de proveniencia de imágenes.
PROVIDERS_SIDECAR = "images/providers.json"

#: Firma PNG (8 bytes) usada para detección rápida de corte/corrupción.
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: Contrato sintético del audio (synthetic.py): PCM 16-bit mono @ 44.1 kHz.
SYNTHETIC_SAMPLE_RATE = 44100
SYNTHETIC_CHANNELS = 1
SYNTHETIC_SAMPLE_WIDTH = 2

#: Códec esperado en el stream de video: libx264 (commands.py) produce el
#: códec ``h264`` en ffprobe.
VIDEO_CODEC_NAME = "h264"
DEFAULT_AUDIO_CODEC = "aac"
DEFAULT_FPS = 25

#: Timeout de la inspección con ffprobe (sincronizado con render_video.py).
PROBE_TIMEOUT_SECONDS = 30

#: Tolerancias de desfase A/V (segundos).
AV_OK_DELTA_SECONDS = 0.25
AV_WARN_DELTA_SECONDS = 1.0
DURATION_FRACTION_FAIL = 0.10


def _sum_scene_timing(content: object) -> float:
    """Suma ``visuals.scenes[*].timing_seconds`` del ContentPackage (dict).

    Replica la lógica de ``project._estimate_duration``: ignora escenas sin
    timing numérico positivo. Devuelve ``0.0`` si no hay datos utilizables.
    """
    if not isinstance(content, dict):
        return 0.0
    visuals = content.get("visuals")
    scenes = visuals.get("scenes") if isinstance(visuals, dict) else None
    if not isinstance(scenes, (list, tuple)):
        return 0.0
    total = 0.0
    for scene in scenes:
        timing = scene.get("timing_seconds") if isinstance(scene, dict) else None
        if isinstance(timing, (int, float)) and not isinstance(timing, bool) and timing > 0:
            total += float(timing)
    return total


class QualityGate:
    """Ejecuta las comprobaciones read-only sobre un directorio de salida.

    Args:
        output_dir: directorio de salida del run (donde viven
            ``content.json``, ``project.json`` y las carpetas ``images/``,
            ``audio/``, ``video/``). Si no existe es un error de configuración
            (:class:`QualityGateError`).
    """

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        if not self.output_dir.is_dir():
            raise QualityGateError(
                f"El directorio de salida no existe: {self.output_dir}"
            )

    def run(self) -> QualityGateResult:
        """Ejecuta todas las comprobaciones y devuelve el veredicto.

        Returns:
            :class:`QualityGateResult` con ``passed=False`` si existe al menos
            un check de severidad ``error`` fallido.
        """
        checks: list[QualityCheck] = []

        content = self._check_content_json(checks)
        manifest = self._check_manifest_json(checks)
        self._check_images(checks, content, manifest)
        self._check_audio(checks, manifest)
        self._check_video(checks, manifest)
        self._check_duration_and_sync(checks, content, manifest)
        self._check_provenance(checks, manifest)
        self._check_residues(checks, manifest)

        errors = sum(1 for c in checks if c.severity == SEVERITY_ERROR and not c.passed)
        warnings = sum(1 for c in checks if c.severity == SEVERITY_WARNING)
        return QualityGateResult(
            passed=errors == 0,
            checks=tuple(checks),
            errors=errors,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _abs(self, relative: str | Path) -> Path:
        """Resuelve una ruta relativa del run de forma segura.

        Raises:
            ProjectValidationError: si la ruta escapa del directorio de salida
                (para exclusiones en checks, devuelve una ruta inexistente).
        """
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts or "\x00" in str(candidate):
            logger.warning("Ruta relativa insegura ignorada: %r", relative)
            return self.output_dir / "__unsafe__"
        return self.output_dir / candidate

    @staticmethod
    def _is_unsafe_relative(relative: str | Path) -> bool:
        """True si una ruta relativa es insegura (traverses, absoluta, NUL)."""
        candidate = Path(relative)
        return bool(
            not str(relative).strip()
            or candidate.is_absolute()
            or ".." in candidate.parts
            or "\x00" in str(candidate)
        )

    @staticmethod
    def _probe(path: Path) -> dict:
        """Inspecciona un archivo multimedia con ffprobe (patrón seguro).

        Usa ``subprocess.run`` con lista de argumentos, ``shell=False`` y
        timeout. Devuelve el JSON decodificado, o ``{}`` si no se puede
        inspeccionar (tool ausente, timeout, salida ilegible o archivo
        corrupto).
        """
        executable = shutil.which("ffprobe") or "ffprobe"
        try:
            completed = subprocess.run(
                [
                    executable,
                    "-v",
                    "error",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.debug("ffprobe no pudo inspeccionar %s: %s", path, exc)
            return {}
        if completed.returncode != 0:
            logger.debug(
                "ffprobe devolvió %d para %s: %s",
                completed.returncode,
                path,
                (completed.stderr or "").strip(),
            )
            return {}
        try:
            data = json.loads(completed.stdout or "{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _load_providers_sidecar(path: Path) -> dict:
        """Carga el sidecar ``images/providers.json``; nunca secreto.

        Devuelve ``{filename: {"provider":..., "model":...}}`` con solo valores
        de tipo cadena. Si falta o no es interpretable, devuelve ``{}``.
        """
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            logger.debug("Sidecar de providers ilegible (%s): %s", path, exc)
            return {}
        if not isinstance(data, dict):
            return {}
        result: dict = {}
        for filename, entry in data.items():
            if not isinstance(filename, str) or not isinstance(entry, dict):
                continue
            clean: dict[str, str] = {}
            provider = entry.get("provider")
            model = entry.get("model")
            if isinstance(provider, str):
                clean["provider"] = provider
            if isinstance(model, str):
                clean["model"] = model
            result[filename] = clean
        return result

    # ------------------------------------------------------------------
    # 1. content.json
    # ------------------------------------------------------------------

    def _check_content_json(self, checks: list[QualityCheck]) -> dict | None:
        """Valida ``content.json`` y devuelve su dict (o ``None``)."""
        path = self._abs(CONTENT_FILE)
        if not path.is_file():
            checks.append(QualityCheck(CONTENT_FILE, False, SEVERITY_ERROR,
                                       f"No se encuentra {CONTENT_FILE}."))
            return None
        checks.append(QualityCheck(CONTENT_FILE, True, SEVERITY_INFO,
                                   f"{CONTENT_FILE} presente."))

        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            checks.append(QualityCheck("content.json legible", False, SEVERITY_ERROR,
                                       f"No se pudo leer {CONTENT_FILE}: {exc}"))
            return None
        try:
            package = content_package_from_json(raw)
        except (ValueError, ContentValidationError) as exc:
            checks.append(QualityCheck("content.json JSON válido", False, SEVERITY_ERROR,
                                       f"{CONTENT_FILE} no es un ContentPackage válido: {exc}"))
            return None
        checks.append(QualityCheck("content.json JSON válido", True, SEVERITY_INFO,
                                   f"{CONTENT_FILE} reconstruido como ContentPackage."))

        content_errors = find_content_errors(package)
        if content_errors:
            checks.append(QualityCheck("content.json find_errors", False, SEVERITY_ERROR,
                                       "ContentPackage inválido: " + "; ".join(content_errors)))
        else:
            checks.append(QualityCheck("content.json find_errors", True, SEVERITY_INFO,
                                       "ContentPackage cumple find_errors."))

        content = content_package_to_dict(package)
        scenes = content.get("visuals", {}).get("scenes", []) if isinstance(content, dict) else []
        if not scenes:
            checks.append(QualityCheck("content.json escenas", False, SEVERITY_ERROR,
                                       "El ContentPackage no declara escenas."))
        else:
            checks.append(QualityCheck("content.json escenas", True, SEVERITY_INFO,
                                       f"El ContentPackage declara {len(scenes)} escena(s)."))
        return content

    # ------------------------------------------------------------------
    # 4. project.json
    # ------------------------------------------------------------------

    def _check_manifest_json(self, checks: list[QualityCheck]) -> ProjectManifest | None:
        """Valida ``project.json`` y devuelve el manifest (o ``None``)."""
        path = self._abs(MANIFEST_FILE)
        if not path.is_file():
            checks.append(QualityCheck(MANIFEST_FILE, False, SEVERITY_ERROR,
                                       f"No se encuentra {MANIFEST_FILE}."))
            return None
        checks.append(QualityCheck(MANIFEST_FILE, True, SEVERITY_INFO,
                                   f"{MANIFEST_FILE} presente."))
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            checks.append(QualityCheck("project.json legible", False, SEVERITY_ERROR,
                                       f"No se pudo leer {MANIFEST_FILE}: {exc}"))
            return None
        try:
            manifest = project_manifest_from_json(raw)
            validate_project_manifest(manifest)
        except (ValueError, ProjectValidationError, ProjectNotFoundError) as exc:
            errors = getattr(exc, "errors", [str(exc)])
            detail = "; ".join(errors) if isinstance(errors, list) else str(errors)
            checks.append(QualityCheck("project.json válido", False, SEVERITY_ERROR,
                                       f"{MANIFEST_FILE} inválido: {detail}"))
            return None
        checks.append(QualityCheck("project.json válido", True, SEVERITY_INFO,
                                   f"{MANIFEST_FILE} cumple validator del dominio."))

        # Activos referenciados existen físicamente.
        missing = [a.path for a in manifest.assets if not self._abs(a.path).is_file()]
        if missing:
            checks.append(QualityCheck("assets existen", False, SEVERITY_ERROR,
                                       "Activos declarados inexistentes: " + ", ".join(sorted(missing))))
        else:
            checks.append(QualityCheck("assets existen", True, SEVERITY_INFO,
                                       "Todos los activos declarados existen."))

        # output_file válido y dentro del run.
        if not manifest.output_file or self._is_unsafe_relative(manifest.output_file):
            checks.append(QualityCheck("output_file válido", False, SEVERITY_ERROR,
                                       f"output_file inválido: {manifest.output_file!r}"))
        else:
            checks.append(QualityCheck("output_file válido", True, SEVERITY_INFO,
                                       f"output_file: {manifest.output_file}"))
        return manifest

    # ------------------------------------------------------------------
    # 2. imágenes
    # ------------------------------------------------------------------

    def _check_images(self, checks: list[QualityCheck], content: dict | None,
                      manifest: ProjectManifest | None) -> None:
        """Verifica imágenes: correspondencia con escenas, índice y PNG."""
        if manifest is None:
            logger.debug("Sin manifest: se omite el chequeo de imágenes.")
            return
        scenes = content.get("visuals", {}).get("scenes", []) if content else []
        image_assets = [a for a in manifest.assets if a.kind is AssetKind.IMAGE]
        expected = len(scenes)

        if not image_assets:
            checks.append(QualityCheck("imágenes presentes", False, SEVERITY_ERROR,
                                       "El manifest no declara imágenes."))
            return

        # Regla 1 (CORE): una imagen por escena (conteo == escenas).
        if expected > 0 and len(image_assets) != expected:
            checks.append(QualityCheck("imagen por escena", False, SEVERITY_ERROR,
                                       f"{len(image_assets)} imagen(es) declarada(s) para {expected} "
                                       f"escena(s)."))
        else:
            checks.append(QualityCheck("imagen por escena", True, SEVERITY_INFO,
                                       f"{len(image_assets)} imagen(es) para {expected} escena(s)."))

        # Regla 2 (CORE): scene_index únicos y dentro de rango.
        indices = [a.scene_index for a in image_assets if a.scene_index is not None]
        dupes = sorted({i for i in indices if indices.count(i) > 1})
        out_of_range = [i for i in indices if i < 0 or (expected > 0 and i >= expected)]
        index_errors: list[str] = []
        if len(indices) != len(image_assets):
            index_errors.append("hay imágenes sin scene_index")
        if dupes:
            index_errors.append(f"scene_index duplicados: {dupes}")
        if out_of_range:
            index_errors.append(f"scene_index fuera de rango: {out_of_range}")
        if index_errors:
            checks.append(QualityCheck("scene_index válido", False, SEVERITY_ERROR,
                                       "; ".join(index_errors) + "."))
        else:
            checks.append(QualityCheck("scene_index válido", True, SEVERITY_INFO,
                                       "scene_index únicos y dentro de rango."))

        # Regla 3 (CORE): PNG válidos por firma/IHDR (stdlib).
        for asset in image_assets:
            path = self._abs(asset.path)
            if not path.is_file():
                checks.append(QualityCheck("imagen existe", False, SEVERITY_ERROR,
                                           f"Imagen faltante: {asset.path}"))
                continue
            png_errors = _validate_png(path)
            if png_errors:
                checks.append(QualityCheck("imagen decodifica", False, SEVERITY_ERROR,
                                           f"Imagen corrupta {asset.path}: {png_errors}"))
            else:
                checks.append(QualityCheck("imagen decodifica", True, SEVERITY_INFO,
                                           f"{asset.path} es un PNG válido."))

    # ------------------------------------------------------------------
    # 3. audio
    # ------------------------------------------------------------------

    def _check_audio(self, checks: list[QualityCheck], manifest: ProjectManifest | None) -> None:
        """Verifica la narración: presencia, unicidad, decodificación y
        duración."""
        if manifest is None:
            logger.debug("Sin manifest: se omite el chequeo de audio.")
            return
        audio_assets = [a for a in manifest.assets if a.kind is AssetKind.AUDIO]

        if not audio_assets:
            checks.append(QualityCheck("narración presente", False, SEVERITY_ERROR,
                                       "El manifest no declara audio (narración)."))
            return
        if len(audio_assets) > 1:
            checks.append(QualityCheck("narración única", False, SEVERITY_ERROR,
                                       f"El manifest declara {len(audio_assets)} pistas de audio; "
                                       f"se espera exactamente una."))
        else:
            checks.append(QualityCheck("narración única", True, SEVERITY_INFO,
                                       "Exactamente una pista de audio declarada."))

        for asset in audio_assets:
            path = self._abs(asset.path)
            if not path.is_file():
                checks.append(QualityCheck("audio existe", False, SEVERITY_ERROR,
                                           f"Audio faltante: {asset.path}"))
                continue
            duration = self._audio_duration(checks, asset, path)
            if duration is None:
                continue
            if duration <= 0:
                checks.append(QualityCheck("audio duración > 0", False, SEVERITY_ERROR,
                                           f"Audio {asset.path} con duración no positiva."))
            else:
                checks.append(QualityCheck("audio duración > 0", True, SEVERITY_INFO,
                                           f"Audio {asset.path}: {duration:.3f} s."))

    def _audio_duration(self, checks: list[QualityCheck], asset, path: Path) -> float | None:
        """Mide la duración del audio; valida decode WAV o MP3."""
        extension = (asset.extension or path.suffix.lstrip(".")).lower()
        if extension == "wav":
            try:
                with wave.open(str(path), "rb") as w:
                    rate = w.getframerate()
                    frames = w.getnframes()
            except (wave.Error, OSError, ValueError) as exc:
                checks.append(QualityCheck("audio decodifica", False, SEVERITY_ERROR,
                                           f"WAV inválido {asset.path}: {exc}"))
                return None
            if not rate or frames <= 0:
                checks.append(QualityCheck("audio decodifica", False, SEVERITY_ERROR,
                                           f"WAV {asset.path} sin muestras válidas."))
                return None
            checks.append(QualityCheck("audio decodifica", True, SEVERITY_INFO,
                                       f"{asset.path} es un WAV decodificable."))
            return frames / rate
        if extension == "mp3":
            data = self._probe(path)
            duration = _duration_from_probe(data)
            if duration is None:
                checks.append(QualityCheck("audio decodifica", False, SEVERITY_ERROR,
                                           f"MP3 {asset.path} no pudo inspeccionarse con ffprobe."))
                return None
            checks.append(QualityCheck("audio decodifica", True, SEVERITY_INFO,
                                       f"{asset.path} inspeccionado con ffprobe."))
            return duration
        checks.append(QualityCheck("audio formato", False, SEVERITY_ERROR,
                                   f"Formato de audio no soportado por el gate: {asset.path} "
                                   f"({extension or 'sin extensión'})."))
        return None

    # ------------------------------------------------------------------
    # 5. video
    # ------------------------------------------------------------------

    def _check_video(self, checks: list[QualityCheck], manifest: ProjectManifest | None) -> None:
        """Verifica el MP4: existencia, streams, códecs y duración."""
        if manifest is None:
            logger.debug("Sin manifest: se omite el chequeo de video.")
            return
        output_path = self._abs(manifest.output_file)
        if not manifest.output_file or self._is_unsafe_relative(manifest.output_file):
            return  # ya marcado en manifest

        if not output_path.is_file():
            checks.append(QualityCheck("video existe", False, SEVERITY_ERROR,
                                       f"Video faltante: {manifest.output_file}"))
            return
        checks.append(QualityCheck("video existe", True, SEVERITY_INFO,
                                   f"Video presente: {manifest.output_file}"))

        data = self._probe(output_path)
        streams = data.get("streams", []) if data else []
        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if not video_stream:
            checks.append(QualityCheck("video stream", False, SEVERITY_ERROR,
                                       "El MP4 no contiene stream de video."))
        else:
            codec = (video_stream.get("codec_name") or "").lower()
            if codec != VIDEO_CODEC_NAME:
                checks.append(QualityCheck("video códec h264", False, SEVERITY_ERROR,
                                           f"Códec video '{codec or 'desconocido'}' ≠ h264."))
            else:
                checks.append(QualityCheck("video códec h264", True, SEVERITY_INFO,
                                           "Stream de video codificado con h264 (libx264)."))

        audio_declared = any(a.kind is AssetKind.AUDIO for a in manifest.assets)
        if audio_declared:
            if not audio_stream:
                checks.append(QualityCheck("video audio stream", False, SEVERITY_ERROR,
                                           "Se declaró audio pero el MP4 no tiene stream de audio."))
            else:
                codec = (audio_stream.get("codec_name") or "").lower()
                if codec != DEFAULT_AUDIO_CODEC:
                    checks.append(QualityCheck("video audio códec aac", False, SEVERITY_ERROR,
                                               f"Códec audio '{codec or 'desconocido'}' ≠ aac."))
                else:
                    checks.append(QualityCheck("video audio códec aac", True, SEVERITY_INFO,
                                               "Stream de audio codificado con aac."))
                self._check_audio_stream_params(checks, audio_stream)

        duration = _duration_from_probe(data)
        if duration is None:
            checks.append(QualityCheck("video duración", False, SEVERITY_ERROR,
                                       "No se pudo determinar la duración del video."))
        elif duration <= 0:
            checks.append(QualityCheck("video duración", False, SEVERITY_ERROR,
                                       "Duración del video no positiva."))
        else:
            checks.append(QualityCheck("video duración", True, SEVERITY_INFO,
                                       f"Video de {duration:.3f} s."))

    def _check_audio_stream_params(self, checks: list[QualityCheck], audio_stream: dict) -> None:
        """Valida 44100/mono del stream de audio (contrato sintético)."""
        sample_rate = audio_stream.get("sample_rate")
        channels = audio_stream.get("channels")
        deviations: list[str] = []
        if sample_rate is not None and str(sample_rate) != str(SYNTHETIC_SAMPLE_RATE):
            deviations.append(f"sample_rate={sample_rate}")
        if channels is not None and int(channels) != SYNTHETIC_CHANNELS:
            deviations.append(f"channels={channels}")
        if deviations:
            checks.append(QualityCheck("audio stream 44100/mono", False, SEVERITY_WARNING,
                                       "Stream de audio fuera del contrato sintético ("
                                       + ", ".join(deviations) + ")."))
        else:
            checks.append(QualityCheck("audio stream 44100/mono", True, SEVERITY_INFO,
                                       "Stream de audio 44100 Hz mono."))

    # ------------------------------------------------------------------
    # 6. duración y sync A/V
    # ------------------------------------------------------------------

    def _check_duration_and_sync(self, checks: list[QualityCheck], content: dict | None,
                                 manifest: ProjectManifest | None) -> None:
        """Compara duraciones: timing, audio real, video real y sync A/V."""
        if manifest is None:
            logger.debug("Sin manifest: se omite el chequeo de duraciones.")
            return

        sum_timing = _sum_scene_timing(content) if content else 0.0
        audio_duration = self._audio_real_duration(manifest)
        video_path = self._abs(manifest.output_file)
        video_duration = (
            _duration_from_probe(self._probe(video_path)) if video_path.is_file() else None
        )

        if audio_duration is None:
            checks.append(QualityCheck("audio duración disponible", False, SEVERITY_WARNING,
                                       "No se pudo medir la duración del audio."))
        else:
            checks.append(QualityCheck("audio duración disponible", True, SEVERITY_INFO,
                                       f"Audio real: {audio_duration:.3f} s."))

        if sum_timing <= 0:
            checks.append(QualityCheck("timing sum disponible", False, SEVERITY_WARNING,
                                       "La suma de timing_seconds no es positiva; no se puede "
                                       "verificar la duración estimada."))
            return

        # Duración esperada = min(timings, audio) si hay audio (por -shortest).
        parts = [sum_timing]
        if audio_duration is not None:
            parts.append(audio_duration)
        expected = min(parts)

        if video_duration is None:
            checks.append(QualityCheck("video duración esperada", False, SEVERITY_ERROR,
                                       "Sin duración de video medible; no se compara."))
            return

        delta = abs(video_duration - expected)
        fraction = delta / expected if expected > 0 else delta
        if delta <= AV_OK_DELTA_SECONDS:
            checks.append(QualityCheck("video duración esperada", True, SEVERITY_INFO,
                                       f"Video {video_duration:.3f} s ≈ esperado {expected:.3f} s "
                                       f"(Δ {delta:.3f} s)."))
        elif delta < AV_WARN_DELTA_SECONDS or fraction <= DURATION_FRACTION_FAIL:
            checks.append(QualityCheck("video duración esperada", False, SEVERITY_WARNING,
                                       f"Video {video_duration:.3f} s vs esperado {expected:.3f} s "
                                       f"(Δ {delta:.3f} s)."))
        else:
            checks.append(QualityCheck("video duración esperada", False, SEVERITY_ERROR,
                                       f"Video {video_duration:.3f} s vs esperado {expected:.3f} s "
                                       f"(Δ {delta:.3f} s = {fraction:.1%}) > tolerancia."))

        # A/V sync: desfase entre video y audio reales.
        if audio_duration is not None and video_duration is not None:
            av_delta = abs(video_duration - audio_duration)
            if av_delta <= AV_OK_DELTA_SECONDS:
                checks.append(QualityCheck("sync A/V", True, SEVERITY_INFO,
                                           f"Desfase A/V {av_delta:.3f} s ≤ {AV_OK_DELTA_SECONDS} s."))
            elif av_delta < AV_WARN_DELTA_SECONDS or (av_delta / max(video_duration, 1e-9)) <= DURATION_FRACTION_FAIL:
                checks.append(QualityCheck("sync A/V", False, SEVERITY_WARNING,
                                           f"Desfase A/V {av_delta:.3f} s (leve)."))
            else:
                checks.append(QualityCheck("sync A/V", False, SEVERITY_ERROR,
                                           f"Desfase A/V {av_delta:.3f} s > tolerancia."))

        # Caso sintético: audio más corto que la estimación, coherente con -shortest.
        if audio_duration is not None and audio_duration < sum_timing and video_duration is not None:
            if abs(video_duration - audio_duration) <= AV_OK_DELTA_SECONDS:
                checks.append(QualityCheck("audio < estimado", False, SEVERITY_WARNING,
                                           f"Audio real ({audio_duration:.3f} s) menor que la "
                                           f"duración estimada ({sum_timing:.3f} s); el render se "
                                           "sincroniza por -shortest (coherente)."))
            else:
                checks.append(QualityCheck("audio < estimado", False, SEVERITY_WARNING,
                                           f"Audio real ({audio_duration:.3f} s) menor que la "
                                           f"estimación ({sum_timing:.3f} s) sin -shortest coherente."))

    def _audio_real_duration(self, manifest: ProjectManifest) -> float | None:
        """Duración real del audio declarado aplicando el mismo path de
        medición que el chequeo de audio."""
        audio_assets = [a for a in manifest.assets if a.kind is AssetKind.AUDIO]
        if len(audio_assets) != 1:
            return None
        asset = audio_assets[0]
        path = self._abs(asset.path)
        if not path.is_file():
            return None
        extension = (asset.extension or path.suffix.lstrip(".")).lower()
        if extension == "wav":
            try:
                with wave.open(str(path), "rb") as w:
                    rate = w.getframerate()
                    frames = w.getnframes()
            except (wave.Error, OSError, ValueError):
                return None
            return frames / rate if rate else None
        return _duration_from_probe(self._probe(path))

    # ------------------------------------------------------------------
    # 7. provenance
    # ------------------------------------------------------------------

    def _check_provenance(self, checks: list[QualityCheck], manifest: ProjectManifest | None) -> None:
        """Compara providers.json con provider/model del manifest (WARN)."""
        if manifest is None:
            logger.debug("Sin manifest: se omite el chequeo de provenance.")
            return
        sidecar = self._load_providers_sidecar(self._abs(PROVIDERS_SIDECAR))
        image_assets = [a for a in manifest.assets if a.kind is AssetKind.IMAGE]
        if not image_assets:
            checks.append(QualityCheck("provenance imágenes", False, SEVERITY_WARNING,
                                       "Sin imágenes que comparar con el sidecar."))
            return
        if not sidecar:
            checks.append(QualityCheck("provenance sidecar", False, SEVERITY_WARNING,
                                       "No hay sidecar images/providers.json; no se puede validar "
                                       "provenance."))
            return
        mismatches: list[str] = []
        for asset in image_assets:
            entry = sidecar.get(asset.name)
            if entry is None:
                mismatches.append(f"{asset.name}: ausente en sidecar")
                continue
            if entry.get("provider") != asset.provider:
                mismatches.append(f"{asset.name}: provider sidecar="
                                  f"{entry.get('provider')} manifest={asset.provider}")
            if entry.get("model") != asset.model:
                mismatches.append(f"{asset.name}: model sidecar="
                                  f"{entry.get('model')} manifest={asset.model}")
        if mismatches:
            checks.append(QualityCheck("provenance consistente", False, SEVERITY_WARNING,
                                       "Provenance inconsistente: " + "; ".join(mismatches) + "."))
        else:
            checks.append(QualityCheck("provenance consistente", True, SEVERITY_INFO,
                                       "provider/model del manifest coinciden con el sidecar."))

    # ------------------------------------------------------------------
    # 8. residuos
    # ------------------------------------------------------------------

    def _check_residues(self, checks: list[QualityCheck], manifest: ProjectManifest | None) -> None:
        """Detecta `.tmp` y archivos extra no declarados (WARN)."""
        declared_files = {
            self._abs(a.path)
            for a in (manifest.assets if manifest else ())
        }
        if manifest is not None and manifest.output_file:
            declared_files.add(self._abs(manifest.output_file))
        declared_files.add(self._abs(CONTENT_FILE))
        declared_files.add(self._abs(MANIFEST_FILE))

        tmp_files: list[str] = []
        extra_files: list[str] = []
        for path in sorted(self.output_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() == ".tmp":
                tmp_files.append(str(path.relative_to(self.output_dir)))
                continue
            if path in declared_files:
                continue
            rel = path.relative_to(self.output_dir).as_posix()
            if rel.startswith("images/") and path.suffix.lstrip(".").lower() in IMAGE_EXTENSIONS:
                extra_files.append(rel)
            elif rel.startswith("audio/") and path.suffix.lstrip(".").lower() in AUDIO_EXTENSIONS:
                extra_files.append(rel)
            elif rel.startswith("video/") and path.suffix.lstrip(".").lower() in VIDEO_EXTENSIONS:
                extra_files.append(rel)

        if tmp_files:
            checks.append(QualityCheck("sin .tmp", False, SEVERITY_WARNING,
                                       "Archivos temporales residuales: " + ", ".join(tmp_files) + "."))
        else:
            checks.append(QualityCheck("sin .tmp", True, SEVERITY_INFO,
                                       "No hay archivos .tmp residuales."))

        if extra_files:
            checks.append(QualityCheck("sin assets extra", False, SEVERITY_WARNING,
                                       "Archivos extra no declarados: " + ", ".join(sorted(extra_files)) + "."))
        else:
            checks.append(QualityCheck("sin assets extra", True, SEVERITY_INFO,
                                       "No hay assets extra en el run."))


def _duration_from_probe(data: dict) -> float | None:
    """Duración en segundos desde la salida de ffprobe (format.duration)."""
    if not data:
        return None
    fmt = data.get("format") or {}
    raw = fmt.get("duration")
    try:
        value = float(raw or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _validate_png(path: Path) -> list[str]:
    """Valida un PNG de forma ligera con la stdlib.

    Verifica la firma (8 bytes) y recorre los chunks comprobando que el IHDR
    exista al inicio con dimensiones positivas y que el CRC32 de cada chunk
    coincida (detección de corrupción de datos). No descomprime los datos de
    imagen (solo integridad estructural).

    Args:
        path: ruta al archivo PNG.

    Returns:
        Lista de errores (vacía si el PNG es estructuralmente válido).
    """
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"no se pudo leer: {exc}"]
    if len(data) < 33:
        return ["archivo demasiado corto para ser PNG"]
    if not data.startswith(PNG_SIGNATURE):
        return ["firma PNG inválida"]

    seen_ihdr = False
    ok_ihdr = False
    pos = 8
    while pos < len(data):
        if pos + 12 > len(data):
            return ["chunk truncado (faltan longitud/tipo/CRC)"]
        length, chunk_type = struct.unpack(">I4s", data[pos : pos + 8])
        end = pos + 12 + length
        if end > len(data):
            return [f"chunk {chunk_type!r} declara {length} bytes pero el archivo se corta"]
        payload = data[pos + 8 : pos + 8 + length]
        stored_crc = struct.unpack(">I", data[pos + 8 + length : end])[0]
        calc_crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        if stored_crc != calc_crc:
            return [f"CRC inválido en el chunk {chunk_type!r}"]

        if chunk_type == b"IHDR":
            seen_ihdr = True
            if len(payload) < 13:
                return ["IHDR sin campos de imagen"]
            width, height = struct.unpack(">II", payload[:8])
            if width <= 0 or height <= 0:
                return [f"dimensiones IHDR inválidas ({width}x{height})"]
            ok_ihdr = True
        elif chunk_type == b"IEND":
            return [] if ok_ihdr else ["PNG sin IHDR válido"]
        pos = end

    if not seen_ihdr:
        return ["falta el chunk IHDR"]
    return [] if ok_ihdr else ["IHDR con dimensiones inválidas"]


def run_quality_gate(output_dir: Path) -> QualityGateResult:
    """Ejecuta el Quality Gate sobre un directorio de salida.

    Args:
        output_dir: directorio de salida del run.

    Returns:
        :class:`QualityGateResult` con el veredicto.
    """
    gate = QualityGate(output_dir)
    return gate.run()