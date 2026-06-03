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

    _AUTO_SYSTEM_PROMPT = (
        "You are a document data extraction specialist. Extract structured data from OCR text.\n"
        "The text may have alignment or formatting issues from scanning — use context clues to recover values.\n"
        "Return a valid JSON object. Omit any field not present in the document.\n\n"
        "FIELD-BY-FIELD EXTRACTION GUIDE:\n\n"
        "vendor_name\n"
        "  The company or person who ISSUED/SENT the document (seller, supplier, service provider).\n"
        "  Look for: company name at the very top, 'From:', 'Supplier:', 'Issued by:', 'Sold by:'.\n"
        "  NOT the recipient, buyer, or 'Bill To' party. NOT just an address or PO Box.\n\n"
        "reference_number\n"
        "  The unique ID of this specific document.\n"
        "  Labels: 'Invoice No', 'Invoice #', 'Invoice Number', 'Ref:', 'Reference:', 'Reference No',\n"
        "  'Bill No', 'Receipt No', 'PO Number', 'Order No', 'Quotation No', 'Doc No', 'N°', '#', 'No.'.\n"
        "  Format hints: INV-2024-001 | REF/24/0123 | 2024/INV/00123 | REC-00456\n"
        "  Do NOT use a date or TIN/VAT number as the reference.\n\n"
        "document_date\n"
        "  Date the document was issued. Format: YYYY-MM-DD.\n"
        "  Labels: 'Invoice Date', 'Date:', 'Date of Issue', 'Issued:', 'Document Date'.\n\n"
        "total_amount\n"
        "  The FINAL amount to pay/paid. Usually the LARGEST amount on the document.\n"
        "  Labels: 'Total', 'Grand Total', 'Total Amount', 'Amount Due', 'Balance Due',\n"
        "  'Net Total', 'Total Payable', 'Amount Payable', 'Total TTC', 'Montant Total'.\n"
        "  Return as plain number: 1234567.89 — NO commas, NO currency symbols, NO spaces.\n"
        "  Do NOT use a subtotal or line item amount as the total.\n\n"
        "currency\n"
        "  3-letter ISO code. Rwanda: RWF (also Rwf, FRW, Frw). Others: USD, EUR, GBP, KES.\n\n"
        "tax_amount\n"
        "  The VAT/tax AMOUNT in currency (not the rate %). Labels: 'VAT', 'TVA', 'Tax Amount', 'VAT Amount'.\n"
        "  Return as plain number: 195652.17\n\n"
        "tax_rate\n"
        "  Tax percentage. Labels: 'VAT Rate', '18%', '15%'. Return as plain number: 18.0\n\n"
        "contact_phone\n"
        "  Phone number anywhere in the document. Labels: 'Tel:', 'Phone:', 'Mobile:', 'Cell:', '+250', '07'.\n"
        "  Keep as written including country code: +250788123456 or 0788 123 456.\n\n"
        "contact_email\n"
        "  Any email address (user@domain.com). Search the full document.\n\n"
        "contact_name\n"
        "  Recipient/customer name the document is addressed TO (Bill To, Sold To).\n\n"
        "contact_address\n"
        "  Full postal address (street, city, country). Combine multi-line into one string.\n\n"
        "vat_number\n"
        "  VAT/TIN. Labels: 'TIN:', 'VAT Reg No:', 'VAT No:', 'Tax ID:'. Rwanda TIN = 9 digits.\n\n"
        "iban\n"
        "  Bank IBAN starting with 2 country-code letters then digits.\n\n"
        "swift\n"
        "  SWIFT/BIC code. Labels: 'SWIFT:', 'BIC:'. 8 or 11 uppercase alphanumeric characters.\n\n"
        "bank_ref\n"
        "  Bank transaction or statement reference number.\n\n"
        "line_items\n"
        "  Array of product/service rows from the invoice body table.\n"
        "  One object per row: {\"description\": \"...\", \"quantity\": 1.0, \"unit_price\": 0.0, \"total\": 0.0}\n"
        "  Scan ALL pages. Extract EVERY row. Do not skip or merge rows.\n\n"
        "document_type\n"
        "  One of: invoice | proof_of_payment | receipt | cv | contract | proforma | other\n\n"
        "suggested_action\n"
        "  One of: create_invoice | create_contact | create_applicant | create_expense_claim | review | none\n\n"
        "confidence  — float 0.0-1.0 overall confidence\n"
        "notes       — one short sentence about the document or any issues\n"
    )

    _CUSTOM_SYSTEM_PROMPT = (
        "You are a document data extraction specialist. Extract only the fields listed in the user message.\n"
        "The OCR text may have formatting issues — use context clues to recover values.\n"
        "Return a valid JSON object with exactly those keys plus:\n"
        "  confidence: float 0.0-1.0\n"
        "  notes: one short sentence\n"
    )

    _TEMPLATE_SYSTEM_PROMPT = (
        "You are a document data extraction specialist. Extract exactly the fields defined in the template.\n"
        "The OCR text may have formatting issues — use context clues to recover values.\n"
        "Return a valid JSON object with those keys plus:\n"
        "  confidence: float 0.0-1.0\n"
        "  notes: one short sentence\n"
    )

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

        # Normalise AI output — clean amounts, phones, etc.
        data = self._normalize_fields(data)

        # Fill any gaps the AI missed with rule-based results
        for key, value in custom_rule_results.items():
            if key not in data or not data[key]:
                data[key] = value

        # Rule-based safety net: if critical fields are still missing after AI,
        # run the rule-based extractor and use its values to fill the gaps.
        _CRITICAL = ('total_amount', 'vendor_name', 'reference_number', 'document_date')
        if any(not data.get(k) for k in _CRITICAL):
            try:
                rb_data = RuleBasedExtractor().extract(raw_text)
                rb_data.pop('confidence', None)
                rb_data.pop('notes', None)
                rb_data.pop('suggested_action', None)
                for k in _CRITICAL:
                    if not data.get(k) and rb_data.get(k):
                        data[k] = rb_data[k]
                        _logger.info(
                            'Rule-based fallback filled missing field %s = %r', k, rb_data[k]
                        )
            except Exception as exc:
                _logger.debug('Rule-based safety net failed (non-fatal): %s', exc)

        if template:
            template.sudo().write({'usage_count': template.usage_count + 1})

        record.populate_from_extracted(data, raw_text, confidence, notes)

        _logger.info(
            'Extraction done for record %s: type=%s confidence=%.1f%% time=%dms tier=%s',
            record.id, record.detected_document_type, confidence, elapsed_ms, tier,
        )

        # ── Auto-routing by confidence threshold ──────────────────────────────
        self._apply_auto_approval(record)

    # ── Post-extraction field normalisation ───────────────────────────────────

    @staticmethod
    def _normalize_fields(data: dict) -> dict:
        """
        Clean up AI-extracted field values so they are ready for populate_from_extracted().

        Problems this fixes:
        - total_amount / tax_amount returned as strings with commas or currency symbols
          ("1,234,000 RWF" → 1234000.0)
        - tax_rate returned as "18%" instead of 18.0
        - contact_phone returned with extra spaces or labels ("Tel: +250 788 123456")
        - vendor_name / contact_name with trailing punctuation
        - reference_number accidentally set to a date or TIN number
        - line_items with string prices instead of floats
        """
        import re

        def _to_float(val):
            if val is None:
                return None
            if isinstance(val, (int, float)):
                return float(val) if val >= 0 else None
            s = str(val).strip()
            # Strip currency symbols and ISO codes
            s = re.sub(r'[A-Z]{3}\s*', '', s, flags=re.IGNORECASE)  # RWF, USD, etc.
            s = re.sub(r'[$€£¥₦₵]', '', s)
            # Remove commas and spaces used as thousands separators
            # Handle "1,234,567.89" → "1234567.89"
            # Handle "1.234.567,89" → "1234567.89"
            if re.match(r'^\d{1,3}(?:\.\d{3})+,\d{1,2}$', s.replace(' ', '')):
                s = s.replace('.', '').replace(',', '.')
            elif re.match(r'^\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?$', s.replace(' ', '')):
                s = s.replace(',', '')
            else:
                s = re.sub(r'[,\s]', '', s)
            try:
                v = float(s)
                return v if v >= 0 else None
            except (ValueError, TypeError):
                return None

        def _clean_phone(val):
            if not val:
                return val
            s = str(val).strip()
            # Strip common labels that the AI includes
            s = re.sub(r'^(?:tel|phone|mobile|mob|cell|contact|tél)\s*[:\-]?\s*', '', s, flags=re.IGNORECASE)
            s = s.strip()
            # Keep digits, +, spaces, dashes, parentheses
            cleaned = re.sub(r'[^\d\+\s\-\(\)]', '', s).strip()
            # Must have at least 7 digits
            digits_only = re.sub(r'\D', '', cleaned)
            return cleaned if len(digits_only) >= 7 else val

        def _clean_name(val):
            if not val:
                return val
            return str(val).strip().rstrip('.,;:')

        def _clean_ref(val):
            if not val:
                return val
            s = str(val).strip()
            # Reject if it looks exactly like a date
            if re.match(r'^\d{4}[-/]\d{2}[-/]\d{2}$', s):
                return None
            if re.match(r'^\d{1,2}[-/\.]\d{1,2}[-/\.]\d{4}$', s):
                return None
            # Reject if it's a bare 9-digit Rwanda TIN
            if re.match(r'^\d{9}$', s):
                return None
            return s if len(s) >= 2 else None

        # ── total_amount ────────────────────────────────────────────────────────
        # Use _to_float but allow 0 only when it truly isn't set
        if 'total_amount' in data:
            val = _to_float(data['total_amount'])
            if val and val > 0:
                data['total_amount'] = val
            else:
                data.pop('total_amount', None)

        # ── tax_amount vs tax_rate confusion ────────────────────────────────────
        # The most common AI mistake: returning the TAX RATE (e.g. 18.0) in
        # the tax_amount field instead of the monetary tax amount.
        # Heuristic: values ≤ 30 that look like a whole percentage are rates.
        _COMMON_TAX_RATES = {
            5.0, 6.0, 7.0, 7.5, 8.0, 9.0, 10.0, 12.0, 12.5, 13.0, 14.0,
            15.0, 16.0, 17.0, 18.0, 19.0, 20.0, 21.0, 23.0, 25.0, 30.0,
        }
        if 'tax_amount' in data:
            tax_val = _to_float(data['tax_amount'])
            total_val = data.get('total_amount') or 0.0
            if not tax_val or tax_val <= 0:
                data.pop('tax_amount', None)
            elif tax_val <= 30.0:
                # Check if this value looks like a percentage rate
                is_rate = (
                    tax_val in _COMMON_TAX_RATES  # known rate
                    or tax_val == int(tax_val)      # whole number ≤ 30
                )
                if is_rate:
                    # Confirm: real tax AMOUNT should be > 1% of total
                    if total_val == 0.0 or (total_val > 0 and tax_val / total_val < 0.001):
                        _logger.info(
                            'Moved tax_amount=%.1f to tax_rate (looks like a percentage, total=%.2f)',
                            tax_val, total_val,
                        )
                        if not data.get('tax_rate'):
                            data['tax_rate'] = tax_val
                        data.pop('tax_amount', None)
                    else:
                        data['tax_amount'] = tax_val
                else:
                    data['tax_amount'] = tax_val
            else:
                data['tax_amount'] = tax_val

        # ── tax_rate: strip "%" if present ──────────────────────────────────────
        if 'tax_rate' in data:
            s = str(data['tax_rate']).replace('%', '').strip()
            try:
                data['tax_rate'] = float(s)
            except (ValueError, TypeError):
                data.pop('tax_rate', None)

        # ── Phone ────────────────────────────────────────────────────────────────
        if 'contact_phone' in data:
            data['contact_phone'] = _clean_phone(data['contact_phone'])

        # ── Names ────────────────────────────────────────────────────────────────
        for field in ('vendor_name', 'contact_name'):
            if field in data:
                data[field] = _clean_name(data[field])

        # Fix vendor/contact confusion: if vendor_name is empty but contact_name
        # looks like a company (has Ltd, Inc, Corp, SARL, etc.), it's the vendor.
        _COMPANY_SUFFIX = re.compile(
            r'\b(?:ltd\.?|limited|inc\.?|corp\.?|corporation|sarl|s\.a\.?|'
            r'plc\.?|llc\.?|gmbh|sas|pty\.?|co\.?\s*ltd\.?|company|enterprise|'
            r'enterprises|group|holding)\b',
            re.IGNORECASE,
        )
        if not data.get('vendor_name') and data.get('contact_name'):
            if _COMPANY_SUFFIX.search(data['contact_name']):
                data['vendor_name'] = data.pop('contact_name')
                _logger.info(
                    'Moved contact_name="%s" to vendor_name (looks like a company)',
                    data['vendor_name'],
                )

        # ── Reference number ─────────────────────────────────────────────────────
        if 'reference_number' in data:
            cleaned = _clean_ref(data['reference_number'])
            if cleaned:
                data['reference_number'] = cleaned
            else:
                data.pop('reference_number', None)

        # ── Line items ───────────────────────────────────────────────────────────
        if 'line_items' in data and isinstance(data['line_items'], list):
            clean_items = []
            for item in data['line_items']:
                if not isinstance(item, dict):
                    continue
                desc = str(item.get('description', '')).strip()
                if not desc:
                    continue
                qty = _to_float(item.get('quantity')) or 1.0
                unit_price = _to_float(item.get('unit_price')) or 0.0
                total = _to_float(item.get('total')) or 0.0
                # Derive total from qty × unit_price if missing
                if total == 0.0 and unit_price > 0:
                    total = round(qty * unit_price, 2)
                clean_items.append({
                    'description': desc,
                    'quantity': qty,
                    'unit_price': unit_price,
                    'total': total,
                })
            data['line_items'] = clean_items

            # Derive total_amount from line items if still missing
            if not data.get('total_amount') and clean_items:
                item_sum = sum(i['total'] or (i['quantity'] * i['unit_price'])
                               for i in clean_items)
                if item_sum > 0:
                    data['total_amount'] = round(item_sum, 2)
                    _logger.info(
                        'Derived total_amount=%.2f from %d line items',
                        data['total_amount'], len(clean_items),
                    )

        return data

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

        vision_model = settings.get('model', 'gpt-4o')

        all_text: list[str] = []
        for i, img_b64 in enumerate(images_b64):
            try:
                page_text = self._ai_vision_single_image(
                    provider_name, key, img_b64, _OCR_PROMPT, vision_model,
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
        self, provider: str, key: str, img_b64: str, prompt: str, model: str = ''
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
            # Use the model passed in; fall back to haiku for any claude-2 non-vision model
            configured = model or 'claude-haiku-4-5-20251001'
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
