"""Fábrica del AnalyzerEngine de Presidio configurado para español.

Por defecto Presidio monta spaCy en INGLÉS. Sobre nombres españoles ("Laura
Fernández", "Javier Romero") devuelve CERO personas y el pipeline parece
funcionar: en producción alguien pregunta por un cliente y el nombre sale en
claro. Es peor que un falso positivo porque no se detecta. Este módulo es el
único lugar que conecta Presidio con el modelo en español (por defecto
es_core_news_md; lo verifica el pre-flight).

Singleton (lru_cache): cargar spaCy tarda y Presidio es seguro entre hilos
para análisis de solo lectura. Los reconocedores del dominio se registran
acá para que nadie tenga que acordarse.

Portado de app/ingestion/pii/analyzer.py del curso.
"""

from functools import lru_cache

from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider

from app.config import settings
from app.ingestion.pii.recognizers import BudgetIdRecognizer, ClientCodeRecognizer


@lru_cache
def build_analyzer() -> AnalyzerEngine:
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [
                {"lang_code": "es", "model_name": settings.presidio_spacy_model}
            ],
        }
    )
    engine = AnalyzerEngine(
        nlp_engine=provider.create_engine(), supported_languages=["es"]
    )
    engine.registry.add_recognizer(BudgetIdRecognizer())
    engine.registry.add_recognizer(ClientCodeRecognizer())
    return engine
