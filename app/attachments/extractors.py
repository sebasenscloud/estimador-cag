"""Camino B: extracción local de texto.

- pypdf: Python puro y liviano. Alcanza para PDFs de texto simple.
- PyMuPDF (vía pymupdf4llm): más rápido en documentos largos y convierte tablas
  y columnas a Markdown, conservando la estructura.
- python-docx: documentos Word, incluidas sus tablas.
"""

import io

import docx
import pymupdf
import pymupdf4llm
import pypdf

from app.attachments.base import AttachmentError


def extract_pdf_with_pypdf(data: bytes) -> str:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except pypdf.errors.PdfReadError as error:
        raise AttachmentError(f"El PDF no se pudo leer: {error}") from error


def extract_pdf_with_pymupdf(data: bytes) -> str:
    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            return pymupdf4llm.to_markdown(document, show_progress=False)
    except AttachmentError:
        raise
    except Exception as error:
        raise AttachmentError(f"El PDF no se pudo leer: {error}") from error


def extract_docx(data: bytes) -> str:
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as error:
        raise AttachmentError(f"El documento Word no se pudo leer: {error}") from error

    lines = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            lines.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(lines)
