"""Extractor de project_metadata: una segunda llamada al LLM por turno.

Recibe la ficha actual, el contenido del turno (transcripción y adjuntos, como
bloques de contenido) y la estimación devuelta, y pide al modelo la ficha
actualizada con `ProjectMetadata` como response_model (Instructor valida y
reintenta igual que en la estimación).

Política de fallo: degradar. Si la extracción falla, se conserva la ficha
anterior y se registra el error; la estimación del usuario ya se generó y no
tiene sentido perderla porque falló la actualización de la memoria.
"""

import structlog

from app.config import settings
from app.prompts.loader import render_metadata_extraction_prompt
from app.schemas import EstimationResult
from app.services.llm_wrapper import (
    InvalidLLMOutputError,
    LLMProviderError,
    generate_structured_from_messages,
)
from app.sessions import ProjectMetadata

logger = structlog.get_logger()


def extract_project_metadata(
    current: ProjectMetadata,
    turn_content: list[dict],
    estimation: EstimationResult,
) -> ProjectMetadata:
    system_prompt, header = render_metadata_extraction_prompt(
        current, version=settings.prompt_version
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": header},
                # El mismo contenido que recibió el estimador: con el camino A,
                # los bloques "file" también llegan al extractor.
                *turn_content,
                {
                    "type": "text",
                    "text": (
                        "<estimacion_devuelta>\n"
                        f"{estimation.model_dump_json()}\n"
                        "</estimacion_devuelta>"
                    ),
                },
            ],
        },
    ]

    try:
        updated = generate_structured_from_messages(messages, ProjectMetadata)
    except (InvalidLLMOutputError, LLMProviderError) as error:
        logger.warning(
            "metadata_extraction_failed",
            policy="degrade",
            error_type=type(error).__name__,
            error=str(error),
        )
        return current

    logger.info(
        "metadata_extracted",
        changed=updated != current,
        technologies=len(updated.mentioned_technologies),
    )
    return updated
