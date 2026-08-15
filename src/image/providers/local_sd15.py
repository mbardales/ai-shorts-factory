"""Proveedor local de imágenes con Stable Diffusion 1.5.

Implementa :class:`image.base.ImageProvider` delegando la generación en un
worker aislado de Python 3.12 (``scripts/sd15_worker.py``). Stable Diffusion y
PyTorch requieren Python <3.13, por lo que el proveedor ejecuta el worker con
un intérprete dedicado (``KOKORO_PYTHON``) manteniendo el pipeline cargado en
memoria para reutilizarlo entre peticiones de la sesión.

Características:

- Genera 512x512 (resolución nativa de SD 1.5) y adapta al formato vertical del
  proyecto (720x1280 por defecto), respetando el contrato actual del Image Core
  sin depender de APIs externas ni API key.
- CUDA obligatorio; CPU fallback no implementado.
- Caché de Hugging Face configurable con ``HF_HOME`` (``LOCAL_SD15_HF_HOME``);
  el modelo no se descarga por petición: el worker lo carga una vez desde la
  caché.
- Errores tipificados con la jerarquía de ``image.exceptions`` existente.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from ..base import AspectRatio, ImageOptions, ImageProvider, ImageRequest, ImageResult
from ..exceptions import (
    ImageError,
    ImageErrorCode,
    ImageGenerationError,
    ImageProviderError,
)
from media import ImageMetadata

logger = logging.getLogger(__name__)


#: Variables de entorno de configuración del proveedor.
MODEL_ENV = "LOCAL_SD15_MODEL"
HF_HOME_ENV = "LOCAL_SD15_HF_HOME"
STEPS_ENV = "LOCAL_SD15_STEPS"
GUIDANCE_ENV = "LOCAL_SD15_GUIDANCE"
KOKORO_PYTHON_ENV = "KOKORO_PYTHON"

#: Valores por defecto (seguros si las variables no existen).
DEFAULT_MODEL = "runwayml/stable-diffusion-v1-5"
DEFAULT_HF_HOME = r"D:\ai-hf-cache"
DEFAULT_STEPS = 20
DEFAULT_GUIDANCE = 7.5
DEFAULT_KOKORO_PYTHON_REL = Path(".venv-kokoro") / "Scripts" / "python.exe"

#: Dimensiones del formato vertical del proyecto (contrato actual).
DEFAULT_WIDTH = 720
DEFAULT_HEIGHT = 1280

#: Tiempo límite por petición al worker (segundos).
DEFAULT_TIMEOUT_SECONDS = 300.0


class LocalSD15ImageProvider(ImageProvider):
    """Proveedor local de imágenes SD 1.5 vía worker Python 3.12.

    Args:
        model: identificador del modelo SD 1.5 (env ``LOCAL_SD15_MODEL``).
        options: opciones compartidas del Image Core.
        timeout_seconds: tiempo límite por petición al worker.
    """

    name: str = "local-sd15"

    def __init__(
        self,
        model: Optional[str] = None,
        *,
        options: Optional[ImageOptions] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        model = model or os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL
        super().__init__(model, options=options)
        self._timeout_seconds = timeout_seconds
        self._root = Path(__file__).resolve().parents[3]
        self._worker_path = self._root / "scripts" / "sd15_worker.py"
        self._python = self._resolve_kokoro_python()
        self._hf_home = os.environ.get(HF_HOME_ENV, "").strip() or DEFAULT_HF_HOME
        self._steps = self._read_int_env(STEPS_ENV, DEFAULT_STEPS)
        self._guidance = self._read_float_env(GUIDANCE_ENV, DEFAULT_GUIDANCE)
        self._lock = threading.Lock()
        self._worker = None
        self._closed = False
        self._session_dir = Path(tempfile.mkdtemp(prefix="local-sd15-"))
        self._stderr_path = self._session_dir / "worker.log"
        self._logger = logging.getLogger(f"{__name__}.LocalSD15ImageProvider")
        atexit.register(self.close)

    # ------------------------------------------------------------------
    # Contrato ImageProvider
    # ------------------------------------------------------------------

    def generate(
        self,
        request: ImageRequest,
        **kwargs: Any,
    ) -> ImageResult:
        """Genera una imagen con SD 1.5 y devuelve sus bytes PNG.

        Args:
            request: solicitud de la imagen a generar.
            **kwargs: ``seed``, ``steps`` y ``guidance_scale`` sobrescriben las
                opciones base.

        Returns:
            :class:`ImageResult` con los bytes PNG (720x1280 por defecto).

        Raises:
            TypeError: si ``request`` no es un :class:`ImageRequest`.
            ValueError: si el prompt está vacío.
            ImageGenerationError: si el worker no produce una imagen válida.
            ImageProviderError: si no hay intérprete de Kokoro, CUDA no está
                disponible, el worker falla o excede el tiempo límite.
        """
        if self._closed:
            raise ImageProviderError("El proveedor LocalSD15 está cerrado.")
        if not isinstance(request, ImageRequest):
            raise TypeError("request debe ser un ImageRequest.")
        if not request.prompt or not request.prompt.strip():
            raise ValueError("El prompt de la imagen no puede estar vacío.")

        options = self.resolve_options(**kwargs)
        steps = int(options.steps or self._steps)
        guidance = float(
            options.guidance_scale
            if options.guidance_scale is not None
            else self._guidance
        )
        width, height = self._resolve_output_size(request)

        temp_path = self._session_dir / f"img_{uuid.uuid4().hex}.png"
        payload = {
            "model": self.model,
            "hf_home": self._hf_home,
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt or "",
            "steps": steps,
            "guidance": guidance,
            "seed": options.seed,
            "output": str(temp_path),
            "output_width": width,
            "output_height": height,
        }

        try:
            response = self._request(payload)
        except ImageError:
            raise

        if not temp_path.is_file() or temp_path.stat().st_size == 0:
            raise ImageGenerationError(
                "El worker de SD15 terminó sin generar un PNG válido.",
                error_code=ImageErrorCode.EMPTY_RESPONSE,
            )

        content = temp_path.read_bytes()
        metadata = ImageMetadata(
            name=self._build_name(request.prompt),
            mime_type="image/png",
            size_bytes=len(content),
            width=width,
            height=height,
            format="png",
            mode="RGB",
        )
        self._logger.debug(
            "Imagen SD15 generada: %dx%d, %d bytes, %.2f s.",
            width,
            height,
            len(content),
            response.get("seconds"),
        )
        return ImageResult(
            prompt=request.prompt,
            content=content,
            metadata=metadata,
            model=self.model,
            provider=self.name,
        )

    # ------------------------------------------------------------------
    # Gestión del worker (sesión persistente)
    # ------------------------------------------------------------------

    def _request(self, payload: dict) -> dict:
        """Envía una petición al worker y espera la respuesta con timeout."""
        with self._lock:
            self._ensure_worker()
            try:
                line = (json.dumps(payload, ensure_ascii=False) + "\n").encode(
                    "utf-8"
                )
                assert self._worker is not None
                self._worker.stdin.write(line)
                self._worker.stdin.flush()
                raw = self._read_line(self._worker.stdout, self._timeout_seconds)
            except Exception as exc:  # noqa: BLE001 - envolver en error tipificado
                self._terminate_worker()
                raise ImageProviderError(
                    f"No se pudo comunicar con el worker de SD15: {exc}",
                    error_code=ImageErrorCode.UNEXPECTED,
                ) from exc

            if raw is None:
                self._terminate_worker()
                raise ImageProviderError(
                    f"El worker de SD15 superó el tiempo límite "
                    f"({self._timeout_seconds:.0f} s).",
                    error_code=ImageErrorCode.TIMEOUT,
                )

            try:
                response = json.loads(raw)
            except json.JSONDecodeError as exc:
                self._terminate_worker()
                raise ImageGenerationError(
                    "El worker de SD15 devolvió una respuesta JSON inválida.",
                    error_code=ImageErrorCode.INVALID_RESPONSE,
                ) from exc

            if not response.get("ok"):
                raise ImageGenerationError(
                    "SD15 no generó la imagen: "
                    f"{response.get('error') or 'error desconocido.'}",
                    error_code=ImageErrorCode.UNKNOWN,
                )
            return response

    def _ensure_worker(self) -> None:
        """Reutiliza el worker vivo o lo relanza si murió."""
        if self._worker is not None and self._worker.poll() is None:
            return
        self._start_worker()

    def _start_worker(self) -> None:
        self._terminate_worker()
        self._validate_python()
        argv = [str(self._python), str(self._worker_path)]
        self._logger.debug("Iniciando worker de SD15: %s", " ".join(argv))
        stderr_file = self._stderr_path.open("wb")
        try:
            self._worker = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                shell=False,
            )
        except OSError:
            stderr_file.close()
            raise

    def _terminate_worker(self) -> None:
        proc = self._worker
        self._worker = None
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        for stream in (proc.stdin, proc.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Ayudantes privados
    # ------------------------------------------------------------------

    @staticmethod
    def _read_line(stream, timeout: float) -> Optional[str]:
        """Lee una línea de ``stream`` con timeout (thread auxiliar)."""
        bucket: list = []

        def reader() -> None:
            line = stream.readline()
            bucket.append(line)

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            return None
        if not bucket:
            return None
        line = bucket[0]
        if isinstance(line, bytes):
            return line.decode("utf-8", errors="replace")
        return line

    def _resolve_output_size(self, request: ImageRequest) -> tuple[int, int]:
        """Resuelve el tamaño de salida respetando el contrato actual.

        Por defecto vertical 720x1280 (formato del proyecto). Honra el
        ``aspect_ratio`` y ``width``/``height`` de la solicitud como el resto
        de proveedores.
        """
        width = request.width or DEFAULT_WIDTH
        height = request.height or DEFAULT_HEIGHT
        if width <= 0 or height <= 0:
            raise ImageGenerationError(
                "Las dimensiones de la imagen deben ser positivas.",
                error_code=ImageErrorCode.BAD_REQUEST,
            )
        if request.aspect_ratio is AspectRatio.SQUARE:
            size = min(width, height)
            return size, size
        if request.aspect_ratio is AspectRatio.LANDSCAPE:
            return 1280, 720
        if request.aspect_ratio is AspectRatio.VERTICAL:
            return width, height
        raise ImageGenerationError(
            f"Aspect ratio no soportado por LocalSD15ImageProvider: "
            f"{request.aspect_ratio.value!r}.",
            error_code=ImageErrorCode.BAD_REQUEST,
        )

    def _resolve_kokoro_python(self) -> Path:
        """Resuelve el intérprete Python 3.12 de Kokoro/SD (env o por defecto)."""
        raw = os.environ.get(KOKORO_PYTHON_ENV, "").strip()
        if not raw:
            return self._root / DEFAULT_KOKORO_PYTHON_REL
        candidate = Path(raw)
        if candidate.is_absolute():
            return candidate
        return self._root / candidate

    def _validate_python(self) -> None:
        """Valida que el intérprete Python 3.12 exista antes de lanzar el worker."""
        if not self._python.is_file():
            raise ImageProviderError(
                f"No se encontró el intérprete de Python 3.12: {self._python}. "
                f"Instala Kokoro/SD en un Python 3.12 (p. ej. .venv-kokoro) o "
                f"define {KOKORO_PYTHON_ENV} con la ruta al python.exe."
            )

    @staticmethod
    def _read_int_env(name: str, default: int) -> int:
        raw = os.environ.get(name, "").strip()
        try:
            return int(raw) if raw else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _read_float_env(name: str, default: float) -> float:
        raw = os.environ.get(name, "").strip()
        try:
            return float(raw) if raw else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _build_name(prompt: str) -> str:
        """Construye un nombre lógico corto para los metadatos."""
        normalized = " ".join(prompt.split())
        if not normalized:
            return "local-sd15"
        return normalized[:50].strip() or "local-sd15"

    def close(self) -> None:
        """Termina el worker y limpia los temporales de la sesión."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._terminate_worker()
        shutil.rmtree(self._session_dir, ignore_errors=True)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001 - cleanup best-effort en GC
            pass