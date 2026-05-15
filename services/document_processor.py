"""
Orchestrates the full extraction pipeline for one DocumentRecord.

Flow:
  1. Read settings
  2. Resolve the file (uploaded OR existing Odoo attachment)
  3. OCR / parse raw text (uses cache unless force_re_ocr)
  4. Apply custom extraction rules (regex/patterns) - optional
  5. Extract QR/Barcode data - optional
  6. Call AI to extract structured data (via pluggable provider)
  7. Merge AI + custom rule results
  8. Write results back to the record
  9. Log quota usage and errors
"""
import base64
import io
import logging
import os
import time

from odoo.exceptions import UserError

from . import ocr_service
from . import ai_providers as _ai
from .rule_based_extractor import RuleBasedExtractor

_logger = logging.getLogger(__name__)


class DocumentProcessor:

    def __init__(self, record):
        self.record = record
        self.env = record.env

    # ── Settings ──────────────────────────────────────────────────────────────

    def _read_settings(self):
        ICP = self.env['ir.config_parameter'].sudo()

        tier = (
            ICP.get_param('document_intelligence.extraction_tier', 'rule_based') or 'rule_based'
        ).strip()

        provider = (
            ICP.get_param('document_intelligence.ai_provider', 'groq') or 'groq'
        ).strip()

        openai_key = (
            ICP.get_param('document_intelligence.openai_api_key', '')
            or os.getenv('OPENAI_API_KEY', '')
        ).strip()
        groq_key = (
            ICP.get_param('document_intelligence.groq_api_key', '')
            or os.getenv('GROQ_API_KEY', '')
        ).strip()
        anthropic_key = (
            ICP.get_param('document_intelligence.anthropic_api_key', '')
            or os.getenv('ANTHROPIC_API_KEY', '')
        ).strip()

        ollama_url = (
            ICP.get_param('document_intelligence.ollama_url', 'http://localhost:11434')
            or 'http://localhost:11434'
        ).strip()
        ollama_model = (
            ICP.get_param('document_intelligence.ollama_model', 'llama3')
            or 'llama3'
        ).strip()

        model_map = {
            'openai': (
                ICP.get_param('document_intelligence.openai_model', 'gpt-4o-mini')
                or 'gpt-4o-mini'
            ).strip(),
            'groq': (
                ICP.get_param('document_intelligence.groq_model', 'llama-3.3-70b-versatile')
                or 'llama-3.3-70b-versatile'
            ).strip(),
            'anthropic': (
                ICP.get_param('document_intelligence.anthropic_model', 'claude-haiku-4-5-20251001')
                or 'claude-haiku-4-5-20251001'
            ).strip(),
            'ollama': ollama_model,
        }
        model = model_map.get(provider, 'gpt-4o-mini')

        return {
            'tier': tier,
            'provider': provider,
            'openai_key': openai_key,
            'groq_key': groq_key,
            'anthropic_key': anthropic_key,
            'ollama_url': ollama_url,
            'ollama_model': ollama_model,
            'model': model,
        }

    # ── AI prompt helpers ─────────────────────────────────────────────────────

    _AUTO_SYSTEM_PROMPT = """\
You are a document intelligence AI. Analyze the raw text and extract all meaningful data.
Return ONLY a valid JSON object (no markdown fences, no extra text) with these keys:
  document_type  : one of invoice, proof_of_payment, receipt, cv, contract, proforma, other
                   Use proof_of_payment for payment confirmations, bank transfer receipts,
                   SWIFT confirmations, remittance advice, and any document confirming a
                   payment was made (not requesting payment).
  vendor_name    : company or person name on the document
  reference_number : document reference or invoice number
  document_date  : date in YYYY-MM-DD format
  total_amount   : numeric total (no currency symbol)
  currency       : 3-letter ISO currency code
  tax_amount     : numeric tax
  tax_rate       : numeric tax rate as percentage (e.g., 18.0 for 18%)
  contact_name   : contact person name
  contact_phone  : phone number
  contact_email  : email address
  contact_address: full address
  vat_number     : VAT/Tax ID number
  iban           : bank account IBAN
  swift          : SWIFT/BIC code
  bank_ref       : bank statement reference
  line_items     : array of objects with keys: description, quantity, unit_price, tax_amount, tax_rate
  suggested_action: one of create_invoice, create_contact, update_invoice, create_applicant, create_expense_claim, review, none
  confidence     : float 0.0-1.0 how confident you are
  notes          : short processing note

For line_items, include EVERY line with product/service description, quantity, unit price, and subtotal.
For invoices, include ALL line rows from the body of the invoice.
For receipts, extract expense-related fields.
"""

    _CUSTOM_SYSTEM_PROMPT = """\
You are a document intelligence AI. Extract only the fields listed in the user message.
Return ONLY a valid JSON object with exactly those keys plus:
  confidence: float 0.0-1.0
  notes     : short processing note
"""

    _TEMPLATE_SYSTEM_PROMPT = """\
You are a document intelligence AI. Extract exactly the fields defined in the template below.
Return ONLY a valid JSON object with those keys plus:
  confidence: float 0.0-1.0
  notes     : short processing note
"""

    def _build_prompt(self, mode, fields, template, extra_prompt, raw_text):
        if mode == 'custom' and fields:
            system = self._CUSTOM_SYSTEM_PROMPT
            user = f"Extract these fields: {', '.join(fields)}\n\n---\n{raw_text}"
        elif mode == 'template' and template:
            field_list = template.get_fields_list()
            system = self._TEMPLATE_SYSTEM_PROMPT + f"\nTemplate: {template.name}\nFields: {', '.join(field_list)}"
            user = raw_text
        else:
            system = self._AUTO_SYSTEM_PROMPT
            user = raw_text

        if extra_prompt:
            system += f"\n\nAdditional context:\n{extra_prompt}"

        return system, user

    # ── Main pipeline ─────────────────────────────────────────────────────────

    def run(self):
        record = self.record
        settings = self._read_settings()
        tier = settings['tier']

        # ── 1. Resolve file data ────────────────────────────────────────────
        file_data_b64, file_name = record.get_file_data_and_name()

        if not file_data_b64:
            raise ValueError(
                'No document data found. '
                'Please upload a file or select an existing Odoo attachment.'
            )

        if isinstance(file_data_b64, bytes):
            file_data_b64 = file_data_b64.decode('ascii')

        _logger.info(
            'Starting extraction for document %s (%s) — source: %s tier: %s',
            record.id, file_name, record.input_mode, tier,
        )

        # ── 2. OCR (with cache) ─────────────────────────────────────────────
        if record.raw_text and not record.force_re_ocr:
            raw_text = record.raw_text
            _logger.info('Using cached OCR text for document %s', record.id)
        else:
            lang = record.get_effective_ocr_language()
            try:
                qr_data = None
                qr_enabled = self.env['ir.config_parameter'].sudo().get_param(
                    'document_intelligence.enable_qr_barcode', 'False'
                ) == 'True'
                if qr_enabled:
                    qr_data = self._extract_qr_barcode(file_data_b64, file_name)

                raw_text = ocr_service.extract_text(
                    file_data_b64=file_data_b64,
                    file_name=file_name or '',
                    lang=lang,
                )

                if qr_data:
                    raw_text = f"QR_DATA: {qr_data}\n\n{raw_text}"

            except RuntimeError as exc:
                # Normal OCR failed — try AI vision before giving up
                vision_text = self._try_ai_vision_ocr(
                    file_data_b64, file_name or '', settings,
                )
                if vision_text and vision_text.strip():
                    raw_text = vision_text
                    _logger.info(
                        'AI vision OCR recovered text (%d chars) for record %s',
                        len(raw_text), record.id,
                    )
                else:
                    self.env['document.intelligence.error.log'].log_error(
                        document=record, error_type='ocr', error=exc,
                    )
                    raise UserError(str(exc)) from None
            except Exception as exc:
                self.env['document.intelligence.error.log'].log_error(
                    document=record, error_type='ocr', error=exc,
                )
                raise

        # If OCR returned empty (e.g. blank PDF), try AI vision as a second chance
        if not raw_text.strip():
            raw_text = self._try_ai_vision_ocr(file_data_b64, file_name or '', settings)

        if not raw_text.strip():
            raise UserError(
                'No readable text could be extracted from this document.\n\n'
                'Options to fix this:\n'
                '• Digital PDF / DOCX: pip install pdfminer.six  or  pip install pypdf\n'
                '• Scanned PDF / image (local):  pip install easyocr\n'
                '  (no system binary — downloads ML models ~100 MB on first use)\n'
                '• Scanned PDF / image (cloud): configure a Cloud AI provider in Settings\n'
                '  — GPT-4o, Claude, or Groq vision models extract text without any extra install\n'
                '• Traditional OCR (any OS): pip install pytesseract Pillow PyMuPDF\n'
                '  + tesseract binary: apt install tesseract-ocr (Linux) | brew install tesseract (macOS)\n'
                '  | download from github.com/UB-Mannheim/tesseract/wiki (Windows)\n\n'
                'Tip: Most digital invoices (exported from accounting software) work with '
                'pip install pdfminer.six alone — no system binary required.'
            )

        # ── TIER 1: Rule-based extraction (no AI, no key, zero cost) ─────────
        if tier == 'rule_based':
            self._run_rule_based(record, raw_text)
            return

        # ── 3. Apply custom extraction rules (shared by Tier 2 & 3) ──────────
        custom_rule_results = {}
        custom_rules_enabled = self.env['ir.config_parameter'].sudo().get_param(
            'document_intelligence.enable_custom_rules', 'False'
        ) == 'True'
        if custom_rules_enabled:
            custom_rule_results = self._apply_custom_rules(raw_text, record.detected_document_type)

        # ── 4. Build extraction context ──────────────────────────────────────
        mode = record.extraction_mode or 'auto'
        fields = None
        template = None

        if mode == 'custom' and record.custom_fields_input:
            fields = [f.strip() for f in record.custom_fields_input.split(',') if f.strip()]

        if mode == 'template' and record.template_id:
            template = record.template_id

        extra_prompt = record.extra_prompt or ''

        if custom_rule_results:
            rule_context = "\n".join([f"{k}: {v}" for k, v in custom_rule_results.items()])
            extra_prompt = f"Pre-extracted fields from patterns:\n{rule_context}\n\n{extra_prompt}"

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

        system_prompt, user_message = self._build_prompt(
            mode, fields, template, extra_prompt, raw_text,
        )

        # ── 5. AI extraction (Tier 2: Ollama / Tier 3: Cloud) ────────────────
        if tier == 'ollama':
            provider_name = 'ollama'
            model_label = settings['ollama_model']
        else:
            provider_name = settings['provider']
            model_label = settings['model']

        ai_provider = _ai.get_provider(
            provider_name=provider_name,
            openai_key=settings['openai_key'],
            groq_key=settings['groq_key'],
            anthropic_key=settings['anthropic_key'],
            ollama_url=settings['ollama_url'],
            ollama_model=settings['ollama_model'],
        )

        t0 = time.monotonic()
        success = True
        elapsed_ms = 0
        try:
            raw_response = ai_provider.extract(
                system_prompt=system_prompt,
                user_message=user_message,
                model=model_label,
            )
            data = ai_provider._parse_json(raw_response)

        except _ai.OllamaNotAvailable as exc:
            # Tier 2 fallback: Ollama unreachable → silently use Tier 1
            success = False
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            _logger.warning(
                'Ollama unavailable for record %s (%s) — falling back to rule-based. '
                'Start Ollama with: ollama serve',
                record.id, exc,
            )
            record.message_post(
                body=(
                    '<b>Ollama not reachable</b> — used rule-based extraction as fallback.<br/>'
                    f'Reason: {exc}<br/>'
                    'Fix: make sure Ollama is running (<code>ollama serve</code>), '
                    'then re-extract.'
                )
            )
            self._run_rule_based(record, raw_text)
            return

        except (_ai.ProviderAuthError, _ai.ProviderQuotaError) as exc:
            # Auth / quota errors: log and raise — no point retrying
            success = False
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            self.env['document.intelligence.quota.log'].log_call(
                document=record,
                provider=provider_name,
                model_used=model_label,
                text_chars=len(raw_text),
                success=False,
                response_ms=elapsed_ms,
            )
            self.env['document.intelligence.error.log'].log_error(
                document=record,
                error_type='ai',
                error=exc,
                provider=provider_name,
            )
            raise RuntimeError(str(exc)) from exc

        except Exception as exc:
            success = False
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            self.env['document.intelligence.quota.log'].log_call(
                document=record,
                provider=provider_name,
                model_used=model_label,
                text_chars=len(raw_text),
                success=False,
                response_ms=elapsed_ms,
            )
            self.env['document.intelligence.error.log'].log_error(
                document=record,
                error_type='ai',
                error=exc,
                provider=provider_name,
            )
            raise

        finally:
            if success:
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                self.env['document.intelligence.quota.log'].log_call(
                    document=record,
                    provider=provider_name,
                    model_used=model_label,
                    text_chars=len(raw_text),
                    success=True,
                    response_ms=elapsed_ms,
                )

        confidence = float(data.pop('confidence', 0.75)) * 100
        notes = data.pop('notes', '')

        for key, value in custom_rule_results.items():
            if key not in data or not data[key]:
                data[key] = value

        if template:
            template.sudo().write({'usage_count': template.usage_count + 1})

        record.populate_from_extracted(data, raw_text, confidence, notes)

        _logger.info(
            'Extraction done for record %s: type=%s confidence=%.1f%% time=%dms tier=%s',
            record.id, record.detected_document_type, confidence, elapsed_ms, tier,
        )

        # ── Auto-routing by confidence threshold ──────────────────────────────
        self._apply_auto_approval(record)

    # ── Tier 1 helper ─────────────────────────────────────────────────────────

    def _run_rule_based(self, record, raw_text: str):
        """Run pure regex extraction — no network, no key, free forever."""
        t0 = time.monotonic()
        extractor = RuleBasedExtractor()
        data = extractor.extract(raw_text)

        confidence = float(data.pop('confidence', 0.5)) * 100
        notes = data.pop('notes', '')

        # Merge any enabled custom rules on top
        custom_rules_enabled = self.env['ir.config_parameter'].sudo().get_param(
            'document_intelligence.enable_custom_rules', 'False'
        ) == 'True'
        if custom_rules_enabled:
            custom_rule_results = self._apply_custom_rules(raw_text, data.get('document_type', ''))
            for key, value in custom_rule_results.items():
                if key not in data or not data[key]:
                    data[key] = value

        record.populate_from_extracted(data, raw_text, confidence, notes)

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        _logger.info(
            'Rule-based extraction done for record %s: type=%s confidence=%.1f%% time=%dms',
            record.id, record.detected_document_type, confidence, elapsed_ms,
        )

        self._apply_auto_approval(record)

    # ── Auto-routing by confidence threshold ──────────────────────────────────

    def _apply_auto_approval(self, record):
        """
        Check auto-approval rules after extraction.
        - Confidence ≥ rule threshold + amount ≤ max + trusted vendor → auto-create record.
        - Confidence below the low-confidence floor → flag for priority review.
        """
        ICP = self.env['ir.config_parameter'].sudo()

        # Global low-confidence floor — flag anything below this for priority review
        low_floor = float(ICP.get_param('document_intelligence.low_confidence_floor', '40'))
        if record.confidence_score < low_floor and record.confidence_score > 0:
            record.write({'state': 'review'})
            record.message_post(
                body=(
                    f'<b>Low confidence ({record.confidence_score:.0f}%)</b> — '
                    'please review all extracted fields carefully before approving.'
                )
            )
            _logger.info(
                'Record %s flagged for review: confidence %.1f%% < floor %.1f%%',
                record.id, record.confidence_score, low_floor,
            )

        # Check auto-approval rules (ordered by sequence)
        if 'document.intelligence.auto.approval.rule' not in self.env:
            return
        rules = self.env['document.intelligence.auto.approval.rule'].search(
            [('active', '=', True)], order='sequence'
        )
        for rule in rules:
            if not rule.check_applies(record):
                continue

            _logger.info(
                'Auto-approval rule "%s" matched record %s — action: %s',
                rule.name, record.id, rule.action,
            )
            rule.sudo().write({'usage_count': rule.usage_count + 1})
            record.write({
                'auto_approval_rule_id': rule.id,
                'auto_approved': True,
            })

            if rule.action in ('auto_create', 'auto_post'):
                try:
                    record.action_create_odoo_record()
                    if rule.action == 'auto_post' and record.created_move_id:
                        record.created_move_id.action_post()
                except Exception as exc:
                    _logger.warning(
                        'Auto-approval create failed for record %s: %s', record.id, exc
                    )
            elif rule.action == 'notify':
                record.message_post(
                    body=(
                        f'<b>Auto-approval rule "{rule.name}" matched</b> — '
                        'confidence {record.confidence_score:.0f}%. Awaiting manual approval.'
                    )
                )
            break  # first matching rule wins

    # ── AI vision OCR ──────────────────────────────────────────────────────────

    def _try_ai_vision_ocr(self, file_data_b64: str, file_name: str, settings: dict) -> str:
        """
        Use a vision-capable AI model to extract raw text from an image or scanned PDF.

        Called when normal OCR (Tesseract / pdfminer / pypdf) produces no text.
        Requires a configured cloud AI provider with a vision-capable model.
        For scanned PDFs also requires PyMuPDF (pip install PyMuPDF) to render pages.
        Returns empty string if vision OCR is not available or fails.
        """
        tier = settings['tier']

        # Determine which provider + key to use for vision
        if tier == 'cloud':
            provider_name = settings['provider']
            key = {
                'openai': settings['openai_key'],
                'anthropic': settings['anthropic_key'],
                'groq': settings['groq_key'],
            }.get(provider_name, '')
            if not key:
                return ''
        else:
            # Tier 1 / Tier 2: no cloud key configured — skip vision
            return ''

        raw_bytes = base64.b64decode(file_data_b64)
        fn = file_name.lower()

        # Collect images to send: PDF → render pages; image → send directly
        images_b64: list[str] = []
        if fn.endswith('.pdf') or raw_bytes[:4] == b'%PDF':
            images_b64 = ocr_service.pdf_pages_to_png_b64(raw_bytes, dpi=150, max_pages=6)
            if not images_b64:
                _logger.info(
                    'AI vision OCR: cannot render PDF pages without PyMuPDF '
                    '(pip install PyMuPDF). Skipping vision OCR.'
                )
                return ''
        elif fn.endswith(('.jpg', '.jpeg', '.png', '.tiff', '.tif', '.bmp', '.webp', '.gif')):
            images_b64 = [file_data_b64]
        else:
            return ''  # Not an image/PDF — vision OCR not applicable

        _logger.info(
            'Attempting AI vision OCR: provider=%s pages=%d', provider_name, len(images_b64)
        )

        _OCR_PROMPT = (
            'Extract ALL text from this document image exactly as it appears. '
            'Preserve numbers, amounts, dates, and table structure. '
            'Return ONLY the raw extracted text — no commentary, no JSON, no markdown.'
        )

        all_text: list[str] = []
        for i, img_b64 in enumerate(images_b64):
            try:
                page_text = self._ai_vision_single_image(
                    provider_name, key, img_b64, _OCR_PROMPT,
                )
                if page_text:
                    all_text.append(page_text)
            except Exception as exc:
                _logger.warning('AI vision OCR page %d failed: %s', i + 1, exc)

        if all_text:
            combined = '\n\n'.join(all_text)
            _logger.info('AI vision OCR succeeded: %d chars total', len(combined))
            return combined
        return ''

    def _ai_vision_single_image(
        self, provider: str, key: str, img_b64: str, prompt: str
    ) -> str:
        """Send one base64 PNG/JPG image to the provider's vision API, return extracted text."""

        if provider in ('openai', 'groq'):
            try:
                from openai import OpenAI  # noqa: PLC0415
            except ImportError as exc:
                raise RuntimeError(
                    f'openai package not installed. Run: pip install openai\nDetail: {exc}'
                ) from exc
            base_url = 'https://api.groq.com/openai/v1' if provider == 'groq' else None
            client = OpenAI(api_key=key, **(dict(base_url=base_url) if base_url else {}))
            vision_model = (
                'llama-3.2-11b-vision-preview' if provider == 'groq' else 'gpt-4o'
            )
            response = client.chat.completions.create(
                model=vision_model,
                max_tokens=4096,
                messages=[{
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': prompt},
                        {'type': 'image_url', 'image_url': {
                            'url': f'data:image/png;base64,{img_b64}',
                            'detail': 'high',
                        }},
                    ],
                }],
            )
            return (response.choices[0].message.content or '').strip()

        if provider == 'anthropic':
            try:
                import anthropic as _anthropic  # noqa: PLC0415
            except ImportError as exc:
                raise RuntimeError(
                    f'anthropic package not installed. Run: pip install anthropic\nDetail: {exc}'
                ) from exc
            # Use configured model; fall back to haiku if it looks like a non-vision model
            configured = settings.get('model', 'claude-haiku-4-5-20251001')
            vision_model = configured if 'claude-2' not in configured else 'claude-haiku-4-5-20251001'
            client = _anthropic.Anthropic(api_key=key)
            response = client.messages.create(
                model=vision_model,
                max_tokens=4096,
                messages=[{
                    'role': 'user',
                    'content': [
                        {'type': 'image', 'source': {
                            'type': 'base64',
                            'media_type': 'image/png',
                            'data': img_b64,
                        }},
                        {'type': 'text', 'text': prompt},
                    ],
                }],
            )
            return (response.content[0].text or '').strip()

        return ''

    # ── Helper methods ─────────────────────────────────────────────────────────

    def _extract_qr_barcode(self, file_data_b64, file_name):
        """
        Attempt to extract QR codes or barcodes from the document.
        Returns decoded text if found, None otherwise.
        """
        try:
            # For images, use pyzbar
            raw_bytes = base64.b64decode(file_data_b64)
            fn = (file_name or '').lower()

            # Only process image types for now (PDF would need conversion)
            if not fn.endswith(('.jpg', '.jpeg', '.png', '.tiff', '.tif', '.bmp', '.webp')):
                return None

            try:
                from PIL import Image
                import pyzbar.pyzbar as pyzbar
            except ImportError:
                _logger.warning('pyzbar not installed; skipping QR/barcode extraction')
                return None

            img = Image.open(io.BytesIO(raw_bytes))
            decoded_objs = pyzbar.decode(img)

            if decoded_objs:
                # Return all decoded data concatenated
                data_list = [obj.data.decode('utf-8', errors='replace') for obj in decoded_objs]
                return ' | '.join(data_list)

        except Exception as e:
            _logger.warning('QR/barcode extraction failed: %s', e)

        return None

    def _apply_custom_rules(self, raw_text, doc_type):
        """
        Apply all relevant custom extraction rules to raw text.
        Returns dict of {field_name: extracted_value}.
        """
        results = {}
        rules = self.env['document.intelligence.custom.rule'].search([
            '|', ('active', '=', True),
            ('active', '=', False),  # include inactive for one-off
            ('document_type', 'in', [doc_type, 'general']),
        ])

        for rule in rules:
            # Check vendor filter
            if rule.vendor_ids and self.record.partner_id not in rule.vendor_ids:
                continue

            value, confidence = rule.apply_rule(raw_text)
            if value:
                # Map target_field to actual field name in document
                field_mapping = {
                    'invoice_number': 'reference_number',
                    'order_number': 'reference_number',  # might need separate field
                    'vat_number': 'vat_number',  # may need to add this field
                    'iban': 'iban',
                    'swift': 'swift',
                    'custom': rule.custom_field_name,
                }
                target = field_mapping.get(rule.target_field, rule.target_field)
                if target:
                    results[target] = value

        return results
