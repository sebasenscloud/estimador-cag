"""Camino A: el PDF va al LLM multimodal, que lo lee él mismo (texto e imágenes).

Dos modos, elegidos con ATTACHMENT_MULTIMODAL_MODE:

- inline: el PDF viaja en base64 dentro del mensaje. Sin estado en el proveedor;
  se reenvía completo en cada llamada (incluida la del extractor de metadata).
- files_api: el PDF se sube una vez a la Files API del proveedor y el mensaje
  solo lleva su file_id. Hay que borrarlo después: `cleanup` lo hace al final
  del turno.

Los dos devuelven un bloque {"type": "file"} que LiteLLM traduce al formato de
cada proveedor (Anthropic: bloque "document"; OpenAI: bloque "file").
"""

import base64

import litellm

from app.attachments.base import Attachment, ProcessedAttachment
from app.config import settings
from app.services.llm_wrapper import LLMProviderError, api_key_for, provider_name

PDF_MIME_TYPE = "application/pdf"

# Propósito de la subida según el proveedor (así lo pide cada Files API).
FILES_API_PURPOSE = {"anthropic": "messages", "openai": "assistants"}


def model_supports_pdf(model: str) -> bool:
    try:
        return litellm.utils.supports_pdf_input(model)
    except Exception:  # noqa: BLE001 - modelo desconocido para LiteLLM: sin camino A
        return False


def _label(attachment: Attachment) -> dict:
    return {
        "type": "text",
        "text": f"--- attachment: {attachment.filename} (PDF adjunto) ---",
    }


def process_inline(attachment: Attachment, reason: str) -> ProcessedAttachment:
    encoded = base64.b64encode(attachment.data).decode("ascii")
    return ProcessedAttachment(
        filename=attachment.filename,
        strategy="multimodal",
        tool="multimodal_inline",
        reason=reason,
        content_parts=[
            _label(attachment),
            {
                "type": "file",
                "file": {
                    "filename": attachment.filename,
                    "file_data": f"data:{PDF_MIME_TYPE};base64,{encoded}",
                },
            },
        ],
    )


def process_files_api(attachment: Attachment, reason: str) -> ProcessedAttachment:
    model = settings.llm_model
    provider = provider_name(model)
    purpose = FILES_API_PURPOSE.get(provider)
    if purpose is None:
        raise LLMProviderError(
            f"El modo files_api no está soportado para el proveedor '{provider}'."
        )

    api_key = api_key_for(model)
    try:
        uploaded = litellm.create_file(
            file=(attachment.filename, attachment.data, PDF_MIME_TYPE),
            purpose=purpose,
            custom_llm_provider=provider,
            api_key=api_key,
        )
    except Exception as error:
        raise LLMProviderError(f"No se pudo subir el PDF: {error}") from error

    def delete_uploaded_file() -> None:
        litellm.file_delete(uploaded.id, custom_llm_provider=provider, api_key=api_key)

    return ProcessedAttachment(
        filename=attachment.filename,
        strategy="multimodal",
        tool="multimodal_files_api",
        reason=reason,
        content_parts=[
            _label(attachment),
            {"type": "file", "file": {"file_id": uploaded.id, "format": PDF_MIME_TYPE}},
        ],
        cleanup=delete_uploaded_file,
    )


def process_multimodal(attachment: Attachment, reason: str) -> ProcessedAttachment:
    if settings.attachment_multimodal_mode == "files_api":
        return process_files_api(attachment, reason)
    return process_inline(attachment, reason)
