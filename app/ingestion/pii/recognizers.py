"""Reconocedores de Presidio propios del dominio.

Presidio trae de serie personas, emails o lugares; nuestros identificadores,
no. El corpus del Proyecto 2 tiene dos:

- BUDGET_ID: `BUDGET-YYYY-NNNN` (año + serie de cuatro dígitos).
- CLIENT_CODE: `CLI-NNNN`.

Son patrones deterministas con forma clara: regex de alta precisión con score
0,95. Se declaran con `supported_language="es"` para que se activen junto al
pipeline en español de analyzer.build_analyzer.

Portado de app/ingestion/pii/recognizers.py del curso.
"""

from presidio_analyzer import Pattern, PatternRecognizer

PATTERN_SCORE = 0.95


class BudgetIdRecognizer(PatternRecognizer):
    def __init__(self) -> None:
        super().__init__(
            supported_entity="BUDGET_ID",
            name="BudgetIdRecognizer",
            patterns=[Pattern("budget_id", r"\bBUDGET-\d{4}-\d{4}\b", PATTERN_SCORE)],
            supported_language="es",
        )


class ClientCodeRecognizer(PatternRecognizer):
    def __init__(self) -> None:
        super().__init__(
            supported_entity="CLIENT_CODE",
            name="ClientCodeRecognizer",
            patterns=[Pattern("client_code", r"\bCLI-\d{4}\b", PATTERN_SCORE)],
            supported_language="es",
        )
