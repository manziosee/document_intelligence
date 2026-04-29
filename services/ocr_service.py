"""
OCR / text extraction layer.

Dispatches to the right extractor based on file type:
  - Image (jpg, png, tiff) → Tesseract via pytesseract
  - PDF                    → pdfminer.six (text layer) then fallback to Tesseract
  - DOCX                   → python-docx
  - Plain text             → returned as-is
"""
import base64
import io
import logging

_logger = logging.getLogger(__name__)


def extract_text(file_data_b64: str, file_name: str, lang: str = 'eng') -> str:
    """
    :param file_data_b64: base64-encoded file bytes (as stored in Odoo Binary field)
    :param file_name:     original filename (used to detect type)
    :param lang:          Tesseract language code(s), e.g. 'eng' or 'eng+fra'
    :return:              extracted plain text
    """
    raw_bytes = base64.b64decode(file_data_b64)
    fn = (file_name or '').lower()

    # 1️⃣ Direct handlers based on extension
    if fn.endswith('.pdf'):
        return _extract_pdf(raw_bytes, lang).strip()
    if fn.endswith('.docx'):
        return _extract_docx(raw_bytes).strip()
    if fn.endswith('.txt'):
        return raw_bytes.decode('utf-8', errors='replace').strip()

    # 2️⃣ Image‑type extensions – try image OCR first
    if fn.endswith(('.jpg', '.jpeg', '.png', '.tiff', '.tif', '.bmp', '.webp')):
        try:
            return _extract_image(raw_bytes, lang).strip()
        except RuntimeError as e:
            _logger.warning('Image OCR failed (%s), falling back to generic handlers', e)
            # continue to generic fallback below

    # 3️⃣ Generic fallback – attempt image OCR (covers mis‑labelled files) then PDF OCR
    try:
        return _extract_image(raw_bytes, lang).strip()
    except RuntimeError:
        _logger.info('Fallback image OCR failed, trying PDF OCR as last resort')
        return _extract_pdf(raw_bytes, lang).strip()


# ── Extractors ─────────────────────────────────────────────────────────────────


def _extract_image(raw_bytes: bytes, lang: str) -> str:
    try:
        import pytesseract
        from PIL import Image, UnidentifiedImageError
        img = Image.open(io.BytesIO(raw_bytes))
        text = pytesseract.image_to_string(img, lang=lang)
        _logger.info('OCR extracted %d chars from image', len(text))
        return text
    except ImportError:
        raise RuntimeError(
            'pytesseract and/or Pillow are not installed. '
            'Run: pip install pytesseract Pillow  and install tesseract-ocr system package.'
        )
    except UnidentifiedImageError:
        # Not a recognizable image – let caller try alternative extractors (e.g., PDF OCR)
        _logger.warning('File not identified as an image, falling back to other extractors')
        raise RuntimeError('Unidentified image format')
    except Exception as e:
        _logger.exception('Image OCR failed')
        raise RuntimeError(f'Image OCR error: {e}') from e


def _extract_pdf(raw_bytes: bytes, lang: str) -> str:
    # First try direct text extraction (fast, works when PDF has a text layer)
    text = _pdf_text_layer(raw_bytes)
    if text and len(text.strip()) > 50:
        _logger.info('PDF text layer extracted %d chars', len(text))
        return text

    # Fallback: render PDF pages to images and OCR them
    _logger.info('PDF has no/thin text layer — falling back to image OCR per page')
    return _pdf_ocr(raw_bytes, lang)


def _pdf_text_layer(raw_bytes: bytes) -> str:
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract
        text = pdfminer_extract(io.BytesIO(raw_bytes))
        return text or ''
    except ImportError:
        _logger.warning('pdfminer.six not installed; skipping text-layer extraction')
        return ''
    except Exception as e:
        _logger.warning('pdfminer extraction failed: %s', e)
        return ''


def _pdf_ocr(raw_bytes: bytes, lang: str) -> str:
    try:
        import fitz  # PyMuPDF
        import pytesseract
        from PIL import Image

        doc = fitz.open(stream=raw_bytes, filetype='pdf')
        pages_text = []
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            img = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
            pages_text.append(pytesseract.image_to_string(img, lang=lang))
        return '\n\n'.join(pages_text)
    except ImportError:
        raise RuntimeError(
            'PyMuPDF (fitz) and/or pytesseract not installed. '
            'Run: pip install PyMuPDF pytesseract Pillow'
        )
    except Exception as e:
        _logger.exception('PDF OCR failed')
        raise RuntimeError(f'PDF OCR error: {e}') from e


def _extract_docx(raw_bytes: bytes) -> str:
    try:
        from docx import Document
        doc = Document(io.BytesIO(raw_bytes))
        paragraphs = [p.text for p in doc.paragraphs]
        # Also extract table cells
        for table in doc.tables:
            for row in table.rows:
                paragraphs.append('\t'.join(cell.text for cell in row.cells))
        text = '\n'.join(paragraphs)
        _logger.info('DOCX extracted %d chars', len(text))
        return text
    except ImportError:
        raise RuntimeError(
            'python-docx is not installed. Run: pip install python-docx'
        )
    except Exception as e:
        _logger.exception('DOCX extraction failed')
        raise RuntimeError(f'DOCX extraction error: {e}') from e
