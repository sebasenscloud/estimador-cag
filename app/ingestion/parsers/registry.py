"""Registro de parsers: un formato → un parser.

Un dict disfrazado de clase, para hacer cumplir "un solo parser por formato"
y darle al orquestador un único objeto del que depender. Agregar XLSX, DOCX o
PDF es un `registry.register(...)`.

Portado de app/ingestion/parsers/registry.py del curso.
"""

from app.ingestion.parsers.budget_json import BudgetJsonParser
from app.ingestion.parsers.protocol import Parser
from app.ingestion.parsers.transcript_txt import TranscriptTxtParser


class ParserRegistry:
    def __init__(self) -> None:
        self._by_format: dict[str, Parser] = {}

    def register(self, parser: Parser) -> None:
        for fmt in parser.supported_formats:
            if fmt in self._by_format:
                raise ValueError(
                    f"El formato {fmt!r} ya tiene parser "
                    f"({type(self._by_format[fmt]).__name__})"
                )
            self._by_format[fmt] = parser

    def get(self, fmt: str) -> Parser:
        try:
            return self._by_format[fmt]
        except KeyError as exc:
            raise KeyError(f"No hay parser registrado para el formato {fmt!r}") from exc

    def formats(self) -> set[str]:
        return set(self._by_format)


def default_registry() -> ParserRegistry:
    """JSON y TXT, como en el curso. XLSX, DOCX y PDF siguen el mismo patrón."""
    registry = ParserRegistry()
    registry.register(BudgetJsonParser())
    registry.register(TranscriptTxtParser())
    return registry
