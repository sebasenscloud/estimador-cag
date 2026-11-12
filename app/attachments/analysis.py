"""Análisis rápido de un PDF para decidir cómo procesarlo.

Se usa PyMuPDF porque abre el documento una sola vez y da, por página, el
texto, las imágenes y las tablas detectadas. El análisis no extrae el contenido
final: solo mide lo necesario para elegir la herramienta.
"""

from dataclasses import dataclass

import pymupdf

from app.attachments.base import AttachmentError

# Las tablas se buscan solo en las primeras páginas: es la medición más costosa.
TABLE_SCAN_PAGES = 10


@dataclass(frozen=True)
class PdfProfile:
    pages: int
    text_chars: int
    images: int
    tables: int

    @property
    def chars_per_page(self) -> float:
        return self.text_chars / self.pages if self.pages else 0.0

    @property
    def images_per_page(self) -> float:
        return self.images / self.pages if self.pages else 0.0


def analyze_pdf(data: bytes) -> PdfProfile:
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as error:
        raise AttachmentError(f"El PDF no se pudo leer: {error}") from error

    with document:
        text_chars = 0
        images = 0
        tables = 0
        for number, page in enumerate(document):
            text_chars += len(page.get_text().strip())
            images += len(page.get_images())
            if number < TABLE_SCAN_PAGES:
                tables += len(page.find_tables().tables)
        return PdfProfile(
            pages=document.page_count,
            text_chars=text_chars,
            images=images,
            tables=tables,
        )
