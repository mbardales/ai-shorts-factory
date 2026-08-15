"""Worker aislado de Stable Diffusion 1.5 para AI Shorts Factory.

Ejecuta la generación de imágenes con ``runwayml/stable-diffusion-v1-5`` fuera
del intérprete principal del proyecto (Python 3.14). SD 1.5 y su dependencia
PyTorch requieren Python <3.13; este worker se ejecuta con un intérprete
Python 3.12 dedicado (ver ``KOKORO_PYTHON`` en ``.env.example``).

Protocolo (sesión larga):
    Lee una línea JSON por petición desde ``stdin`` y responde una línea JSON
    por ``stdout``. El pipeline se carga una sola vez (perezosamente) y se
    reutiliza en todas las peticiones de la sesión, de modo que las siguientes
    peticiones no relanzan el modelo.

    Petición: ``{"model", "hf_home", "prompt", "negative_prompt", "steps",
    "guidance", "seed", "output", "output_width", "output_height"}``.
    Respuesta éxito: ``{"ok": true, "output", "width", "height",
    "size_bytes", "seconds"}``.
    Respuesta error: ``{"ok": false, "error"}``.

    Los logs van EXCLUSIVAMENTE a ``stderr``; el canal ``stdout`` solo
    transporta JSON. Al cerrarse ``stdin`` (EOF) el worker termina con código 0.

Restricciones:
    - No realiza llamadas HTTP intencionales; requiere el modelo ya cacheado en
      ``hf_home`` (no lo descarga por petición).
    - Genera 512x512 (resolución nativa de SD 1.5) y adapta (resize LANCZOS) al
      tamaño de salida solicitado (720x1280 por defecto).
    - CUDA obligatorio; CPU fallback no implementado.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import traceback
from pathlib import Path

import torch
from PIL import Image

#: Resolución nativa de generación de SD 1.5.
GEN_WIDTH = 512
GEN_HEIGHT = 512

#: Stream real de ``stdout`` (canal de datos). Las librerías (transformers, HF)
#: pueden escribir avisos en ``sys.stdout``; se redirigen a un buffer
#: descartable para que el canal de datos contenga únicamente JSON.
_DATA_STDOUT: "io.TextIOBase" = sys.stdout

#: Pipeline de SD 1.5 cargado (se reutiliza en toda la sesión).
_PIPELINE = None


def _emit(payload: dict) -> None:
    """Escribe el JSON de respuesta en el canal de datos."""
    _DATA_STDOUT.write(json.dumps(payload, ensure_ascii=False))
    _DATA_STDOUT.write("\n")
    _DATA_STDOUT.flush()


def _validate_output_path(raw: str) -> Path:
    """Valida la ruta de salida proporcionada por el proveedor."""
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError(f"'output' debe ser una ruta absoluta: {raw!r}.")
    if path.suffix.lower() != ".png":
        raise ValueError(f"'output' debe terminar en '.png': {raw!r}.")
    if ".." in path.parts:
        raise ValueError(f"'output' no puede contener '..': {raw!r}.")
    return path


def _get_pipeline(model: str, hf_home: str):
    """Carga (una sola vez) el pipeline de SD 1.5 en fp16 con CUDA."""
    global _PIPELINE
    if _PIPELINE is None:
        os.environ["HF_HOME"] = hf_home
        from diffusers import StableDiffusionPipeline

        _PIPELINE = StableDiffusionPipeline.from_pretrained(
            model,
            torch_dtype=torch.float16,
            variant="fp16",
            safety_checker=None,
        )
        _PIPELINE.enable_model_cpu_offload()
        _PIPELINE.enable_attention_slicing()
        _PIPELINE.set_progress_bar_config(disable=True)
    return _PIPELINE


def _handle(payload: dict) -> dict:
    """Procesa una petición de generación y devuelve la respuesta JSON."""
    model = str(payload.get("model") or "runwayml/stable-diffusion-v1-5")
    hf_home = str(payload.get("hf_home") or "")
    prompt = str(payload.get("prompt") or "")
    negative = str(payload.get("negative_prompt") or "") or None
    steps = int(payload.get("steps") or 20)
    guidance = float(payload.get("guidance") or 7.5)
    seed = payload.get("seed")
    output_raw = str(payload.get("output") or "")
    out_w = int(payload.get("output_width") or 720)
    out_h = int(payload.get("output_height") or 1280)

    if not prompt.strip():
        raise ValueError("El campo 'prompt' no puede estar vacío.")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA no está disponible en el worker de SD15.")
    output_path = _validate_output_path(output_raw)

    pipeline = _get_pipeline(model, hf_home)
    kwargs: dict = {
        "prompt": prompt,
        "height": GEN_HEIGHT,
        "width": GEN_WIDTH,
        "num_inference_steps": steps,
        "guidance_scale": guidance,
    }
    if negative:
        kwargs["negative_prompt"] = negative
    if seed is not None:
        kwargs["generator"] = torch.Generator(device="cuda").manual_seed(int(seed))

    start = time.time()
    image = pipeline(**kwargs).images[0]
    elapsed = time.time() - start

    if image.size != (out_w, out_h):
        image = image.resize((out_w, out_h), Image.LANCZOS)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)

    return {
        "ok": True,
        "output": str(output_path),
        "width": out_w,
        "height": out_h,
        "size_bytes": output_path.stat().st_size,
        "seconds": round(elapsed, 3),
    }


def main() -> int:
    """Punto de entrada del worker. Devuelve el código de salida."""
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        _DATA_STDOUT.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    # Descartar cualquier aviso de las librerías escrito en stdout.
    sys.stdout = io.StringIO()
    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                result = _handle(payload)
                _emit(result)
            except Exception as exc:  # noqa: BLE001 - el worker aísla errores
                _emit({"ok": False, "error": f"{exc}"})
                traceback.print_exc(file=sys.stderr)
        return 0
    finally:
        sys.stdout = _DATA_STDOUT


if __name__ == "__main__":
    sys.exit(main())