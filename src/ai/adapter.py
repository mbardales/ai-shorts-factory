"""Adaptador de IA desacoplado del proveedor concreto.

``AIAdapter`` envuelve un proveedor que implementa :class:`ai.base.BaseAIProvider`
y expone métodos orientados al dominio (``generate_text``). El adaptador no
conoce el SDK ni la API del proveedor: depende únicamente del contrato base.

Incluye además:

- :class:`ProviderSettings`: representación tipada de la configuración de un
  proveedor leída desde ``providers.json``.
- ``AIAdapter.from_config``: fábrica que construye el adaptador a partir de
  un archivo de configuración (formato de ``config/providers.json``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Type, Union

from .base import BaseAIProvider
from .exceptions import AIError, ConfigurationError, ProviderError
from .providers import GeminiProvider

logger = logging.getLogger(__name__)

#: Registro de tipos de proveedor conocidos (clave = ``provider_type`` en config).
_PROVIDER_TYPES: dict[str, Type[BaseAIProvider]] = {
    "gemini": GeminiProvider,
}

#: Nombre del bloque que contiene la lista de proveedores en la configuración.
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "providers.json"


@dataclass(frozen=True)
class ProviderSettings:
    """Configuración tipada de un proveedor, extraída de ``providers.json``.

    Attributes:
        id: identificador del proveedor (ej. ``"google"``).
        name: nombre legible.
        base_url: URL base de la API (si aplica).
        models: modelos disponibles.
        default_model: modelo por defecto.
        env_var: variable de entorno que contiene la credencial (si aplica).
        enabled: si el proveedor está habilitado en la configuración.
        provider_type: tipo de proveedor usado para resolver la implementación.
    """

    id: str
    name: str
    base_url: Optional[str]
    models: tuple[str, ...]
    default_model: Optional[str]
    env_var: Optional[str]
    enabled: bool
    provider_type: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProviderSettings":
        """Construye :class:`ProviderSettings` desde un dict de la configuración.

        Args:
            data: objeto de un elemento de ``ai_providers.providers``.

        Raises:
            ConfigurationError: si el elemento no tiene identificador.
        """
        provider_id = data.get("id")
        if not provider_id:
            raise ConfigurationError("Elemento de proveedor sin campo 'id'.")
        auth = data.get("auth") or {}
        models = data.get("models") or []
        return cls(
            id=provider_id,
            name=data.get("name", provider_id),
            base_url=data.get("base_url"),
            models=tuple(models),
            default_model=data.get("default_model") or (models[0] if models else None),
            env_var=auth.get("env_var"),
            enabled=bool(data.get("enabled", False)),
            provider_type=data.get("provider_type", ""),
        )


class AIAdapter:
    """Envoltorio orientado al dominio que delega en un ``BaseAIProvider``.

    Args:
        provider: implementación concreta de un proveedor de IA.

    Raises:
        TypeError: si ``provider`` no implementa ``BaseAIProvider``.
    """

    def __init__(self, provider: BaseAIProvider) -> None:
        if not isinstance(provider, BaseAIProvider):
            raise TypeError(
                f"Se esperaba una instancia de BaseAIProvider, se recibió "
                f"{type(provider).__name__}."
            )
        self._provider = provider
        self._logger = logging.getLogger(f"{__name__}.AIAdapter")

    @property
    def provider(self) -> BaseAIProvider:
        """Proveedor de IA subyacente."""
        return self._provider

    def generate_text(self, prompt: str, **kwargs: Any) -> str:
        """Genera texto y devuelve solo la cadena de respuesta.

        Args:
            prompt: texto de entrada.
            **kwargs: parámetros de generación adicionales.

        Returns:
            Texto generado por el proveedor.

        Raises:
            AIError: cualquier error de configuración o del proveedor.
        """
        self._logger.info(
            "Generando texto con proveedor '%s' (modelo '%s').",
            self._provider.name,
            self._provider.model,
        )
        try:
            result = self._provider.generate(prompt, **kwargs)
        except AIError:
            raise
        except Exception as exc:  # noqa: BLE001 - envolver errores inesperados
            self._logger.exception("Error no controlado en el proveedor.")
            raise AIError(f"Error no controlado en el proveedor: {exc}") from exc

        self._logger.debug(
            "Texto generado: %d caracteres (finish_reason=%s).",
            len(result.text),
            result.finish_reason,
        )
        return result.text

    @classmethod
    def from_config(
        cls,
        config_path: Union[str, Path, None] = None,
        *,
        provider_id: Optional[str] = None,
    ) -> "AIAdapter":
        """Construye un adaptador a partir de la configuración de proveedores.

        Lee ``providers.json``, selecciona el proveedor indicado (o el por
        defecto), resuelve su implementación y lo instancia.

        Args:
            config_path: ruta al archivo de configuración. Si es ``None`` se
                usa ``config/providers.json`` relativo a la raíz del proyecto.
            provider_id: identificador del proveedor a usar. Si es ``None`` se
                usa el campo ``ai_providers.default`` de la configuración.

        Returns:
            :class:`AIAdapter` con el proveedor instanciado.

        Raises:
            ConfigurationError: si la configuración es inválida o el proveedor
                no está disponible.
        """
        path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
        if not path.is_file():
            raise ConfigurationError(f"No se encontró la configuración en '{path}'.")

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                f"El archivo de configuración '{path}' no es JSON válido."
            ) from exc

        ai_providers = data.get("ai_providers") or {}
        providers_data = ai_providers.get("providers") or []
        selected_id = provider_id or ai_providers.get("default")
        if not selected_id:
            raise ConfigurationError(
                "No hay proveedor 'default' definido y no se indicó provider_id."
            )

        raw = next((p for p in providers_data if p.get("id") == selected_id), None)
        if raw is None:
            raise ConfigurationError(
                f"El proveedor '{selected_id}' no existe en la configuración."
            )

        settings = ProviderSettings.from_dict(raw)
        factory = _PROVIDER_TYPES.get(settings.provider_type)
        if factory is None:
            raise ConfigurationError(
                f"Tipo de proveedor '{settings.provider_type}' no soportado. "
                f"Soportados: {sorted(_PROVIDER_TYPES)}."
            )

        if not settings.enabled:
            logger.warning(
                "El proveedor '%s' está deshabilitado (enabled=false).",
                settings.id,
            )
        if not settings.default_model:
            raise ConfigurationError(
                f"El proveedor '{settings.id}' no define un modelo."
            )

        provider = factory(model=settings.default_model)
        logger.info(
            "Adaptador construido desde configuración: proveedor='%s' modelo='%s'.",
            settings.id,
            settings.default_model,
        )
        return cls(provider)
