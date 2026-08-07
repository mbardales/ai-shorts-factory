"""Contratos base del AI Core de AI Shorts Factory.

Define el contrato que deben implementar todos los proveedores de IA
(``BaseAIProvider``) y los tipos de datos tipados que intercambian
(``GenerationResult`` y ``GenerationOptions``). Los consumidores deben
depender solo de estos tipos y nunca de un proveedor concreto.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class GenerationResult:
    """Resultado tipado de una generación de texto.

    Attributes:
        text: texto generado por el modelo.
        model: modelo que produjo la respuesta.
        finish_reason: motivo de finalización (si lo reporta el proveedor).
        usage: métricas de uso de tokens (si las reporta el proveedor).
        parsed: objeto estructurado (dict/list) si el proveedor soporta
            Structured Output y se indicó ``response_schema``; ``None`` en
            caso contrario.
    """

    text: str
    model: str
    finish_reason: Optional[str] = None
    usage: dict[str, int] = field(default_factory=dict)
    parsed: Optional[Any] = None


@dataclass(frozen=True)
class GenerationOptions:
    """Parámetros de generación opcionales compartidos entre proveedores.

    Los valores ``None`` indican que el proveedor debe usar su valor por
    defecto. No todos los proveedores soportan todos los parámetros.
    """

    temperature: Optional[float] = None
    max_output_tokens: Optional[int] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None


class BaseAIProvider(ABC):
    """Contrato que deben implementar todos los proveedores de IA.

    Args:
        model: identificador del modelo a utilizar.
        options: parámetros de generación opcionales.
    """

    #: Nombre corto y estable del proveedor (ej. ``"gemini"``).
    name: str = "base"

    def __init__(
        self,
        model: str,
        *,
        options: Optional[GenerationOptions] = None,
    ) -> None:
        self.model = model
        self.options = options or GenerationOptions()

    @abstractmethod
    def generate(self, prompt: str, **kwargs: Any) -> GenerationResult:
        """Genera una respuesta de texto a partir de un prompt.

        Args:
            prompt: texto de entrada enviado al modelo.
            **kwargs: parámetros de generación que sobrescriben los de
                ``self.options`` (temperatura, límites, etc.).

        Returns:
            :class:`GenerationResult` con el texto generado y metadatos.

        Raises:
            ai.exceptions.AIError: cualquier error del proveedor.
        """
