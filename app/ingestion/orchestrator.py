"""Orquestador: pega catálogo + loader + parser en una corrida de ingesta.

Contrato acotado a propósito:

    ingest_source(catalog, source_name, loader=..., registry=..., jobs=..., job_id=...)
        → list[Document]

Tres garantías:

1. La fuente tiene que existir en el catálogo y tener decision == include.
   Si no, IngestionRejected (el router lo traduce a 404 / 400).
2. El job avanza: pending → running → completed | failed.
3. Es SÍNCRONO: el router lo lanza como BackgroundTask. La ingesta es
   offline (por lotes, sin usuario esperando); la consulta de la Sesión 7
   será online y rápida.

Portado de app/ingestion/orchestrator.py del curso.
"""

import uuid
from datetime import UTC, datetime

import structlog

from app.ingestion.catalog.models import CatalogDecision, DataCatalog
from app.ingestion.documents.models import Document
from app.ingestion.jobs import InMemoryJobStore
from app.ingestion.loaders.filesystem import FileSystemLoader
from app.ingestion.parsers.protocol import ParseContext
from app.ingestion.parsers.registry import ParserRegistry

logger = structlog.get_logger()


class IngestionRejected(Exception):
    """La fuente no existe o no es `include`: la corrida no puede empezar."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason  # "unknown_source" | "source_not_included"


def check_ingestable(catalog: DataCatalog, source_name: str):
    source = catalog.find(source_name)
    if source is None:
        raise IngestionRejected(
            f"la fuente {source_name!r} no está en el catálogo", reason="unknown_source"
        )
    if source.decision is not CatalogDecision.INCLUDE:
        raise IngestionRejected(
            f"la fuente {source_name!r} tiene decision={source.decision.value!r}; "
            "solo se ingieren las fuentes 'include'",
            reason="source_not_included",
        )
    return source


def ingest_source(
    *,
    catalog: DataCatalog,
    source_name: str,
    loader: FileSystemLoader,
    registry: ParserRegistry,
    jobs: InMemoryJobStore,
    job_id: uuid.UUID,
) -> list[Document]:
    log = logger.bind(job_id=str(job_id), source_name=source_name)
    source = check_ingestable(catalog, source_name)

    jobs.mark_running(job_id)
    log.info("ingestion_started", format=source.format)

    context = ParseContext(
        source=source,
        source_version=catalog.version,
        ingested_at=datetime.now(UTC),
    )
    try:
        parser = registry.get(source.format)
        documents = [
            document
            for blob in loader.iter_blobs(source.location, {source.format})
            for document in parser.parse(blob, context)
        ]
    except Exception as error:
        log.error("ingestion_failed", error=str(error)[:300])
        jobs.mark_failed(job_id, str(error))
        raise

    jobs.mark_completed(job_id, documents)
    log.info("ingestion_completed", documents_count=len(documents))
    return documents
