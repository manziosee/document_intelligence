"""
Orchestrates the full extraction pipeline for one DocumentRecord.

Flow:
  1. Read settings
  2. Resolve the file (uploaded OR existing Odoo attachment)
  3. OCR / parse raw text
  4. Call AI to extract structured data
  5. Write results back to the record
"""
import logging
import os

from . import ocr_service, ai_service

_logger = logging.getLogger(__name__)


class DocumentProcessor:

    def __init__(self, record):
        self.record = record
        self.env = record.env

    def run(self):
        record = self.record

        # ── 1. Read settings ────────────────────────────────────────────────
        ICP = self.env['ir.config_parameter'].sudo()
        provider = (ICP.get_param('document_intelligence.ai_provider', 'openai') or 'openai').strip()
        api_key = (ICP.get_param('document_intelligence.openai_api_key', '') or os.getenv('OPENAI_API_KEY') or '').strip()
        groq_api_key = (ICP.get_param('document_intelligence.groq_api_key', '') or os.getenv('GROQ_API_KEY') or '').strip()
        lang = (ICP.get_param('document_intelligence.tesseract_lang', 'eng') or 'eng').strip()

        # Choose model based on provider
        if provider == 'groq':
            model = (ICP.get_param('document_intelligence.groq_model', 'llama-3.3-70b-versatile') or 'llama-3.3-70b-versatile').strip()
        else:
            model = (ICP.get_param('document_intelligence.openai_model', 'gpt-4o-mini') or 'gpt-4o-mini').strip()

        # ── 2. Resolve file data ────────────────────────────────────────────
        file_data_b64, file_name = record.get_file_data_and_name()

        if not file_data_b64:
            raise ValueError(
                'No document data found. '
                'Please upload a file or select an existing Odoo attachment.'
            )

        # Normalize: Odoo Binary fields return bytes when accessed via ORM
        if isinstance(file_data_b64, bytes):
            file_data_b64 = file_data_b64.decode('ascii')

        _logger.info(
            'Starting extraction for document %s (%s) — source: %s',
            record.id, file_name, record.input_mode,
        )

        # ── 3. Extract raw text ─────────────────────────────────────────────
        raw_text = ocr_service.extract_text(
            file_data_b64=file_data_b64,
            file_name=file_name or '',
            lang=lang,
        )

        if not raw_text.strip():
            raise ValueError(
                'No readable text found in the document. '
                'Check image quality or file format.'
            )

        # ── 4. Determine extraction mode / fields ───────────────────────────
        mode = record.extraction_mode or 'auto'
        fields = None
        template = None

        if mode == 'custom' and record.custom_fields_input:
            fields = [f.strip() for f in record.custom_fields_input.split(',') if f.strip()]

        if mode == 'template' and record.template_id:
            template = record.template_id

        # Inject context about source record into AI prompt
        extra_prompt = record.extra_prompt or ''
        if record.source_model == 'account.move' and record.linked_move_id:
            move = record.linked_move_id
            extra_prompt = (
                f'This document is attached to an existing Odoo invoice/bill: '
                f'"{move.name}" (partner: {move.partner_id.name or "unknown"}). '
                + extra_prompt
            )
        elif record.source_model == 'hr.applicant' and record.linked_applicant_id:
            extra_prompt = (
                'This document is a CV / resume attached to an HR applicant record. '
                + extra_prompt
            )

        # ── 5. AI extraction ────────────────────────────────────────────────
        data = ai_service.extract_with_ai(
            raw_text=raw_text,
            api_key=api_key,
            model=model,
            mode=mode,
            fields=fields,
            template=template,
            extra_prompt=extra_prompt,
            provider=provider,
            groq_api_key=groq_api_key,
        )

        confidence = float(data.pop('confidence', 0.75)) * 100  # store as %
        notes = data.pop('notes', '')

        # ── 6. Update template usage counter ───────────────────────────────
        if template:
            template.sudo().write({'usage_count': template.usage_count + 1})

        # ── 7. Populate the record ──────────────────────────────────────────
        record.populate_from_extracted(data, raw_text, confidence, notes)

        _logger.info(
            'Extraction done for record %s: type=%s confidence=%.1f%%',
            record.id, record.detected_document_type, confidence,
        )
