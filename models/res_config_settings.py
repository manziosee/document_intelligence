import importlib
import subprocess
import sys

from odoo import models, fields, _
from odoo.exceptions import UserError


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # ── Package management ────────────────────────────────────────────────────

    @staticmethod
    def _has_pkg(import_name: str) -> bool:
        try:
            importlib.import_module(import_name)
            return True
        except ImportError:
            return False

    def action_check_package_status(self):
        """Show a live package status report as a sticky notification."""
        ok = self._has_pkg
        has_pdf    = ok('pdfminer') or ok('pypdf')
        has_pymupdf = ok('fitz')
        has_ocr    = ok('easyocr') or ok('pytesseract')
        has_docx   = ok('docx')
        has_ole    = ok('olefile')

        def row(label, installed, tip=''):
            icon = '✓' if installed else '✗'
            suffix = f' — {tip}' if (not installed and tip) else ''
            return f'{icon}  {label}{suffix}'

        lines = [
            row('Digital PDFs  (pdfminer / pypdf)',   has_pdf,    'pip install pypdf'),
            row('PDF rendering (PyMuPDF)',             has_pymupdf,'pip install PyMuPDF'),
            row('Image / Scanned PDF OCR  (easyocr)', has_ocr,    'pip install easyocr'),
            row('DOCX  (python-docx, stdlib fallback active)', has_docx, 'pip install python-docx'),
            row('Legacy .doc  (olefile)',              has_ole,    'pip install olefile'),
        ]

        all_good = has_pdf and has_pymupdf and has_ocr
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Package Status — Document Intelligence'),
                'message': '\n'.join(lines),
                'type': 'success' if all_good else 'warning',
                'sticky': True,
            },
        }

    def _pip_install(self, packages: list, label: str = ''):
        """Run pip install and return a success/error notification."""
        pkg_str = ' '.join(packages)
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '--quiet'] + packages,
                capture_output=True, text=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            raise UserError(
                _('Installation timed out (5 min). '
                  'Install manually in your terminal:\n  pip install {}').format(pkg_str)
            )

        if result.returncode != 0:
            raise UserError(
                _('Installation failed for: {}\n\nError:\n{}\n\n'
                  'Install manually:\n  pip install {}').format(
                    label or pkg_str, (result.stderr or result.stdout)[:600], pkg_str,
                )
            )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Installed: {}').format(label or pkg_str),
                'message': _(
                    '{} installed successfully.\n'
                    'Restart the Odoo server for the change to take effect.'
                ).format(pkg_str),
                'type': 'success',
                'sticky': True,
            },
        }

    def action_install_pdf_packages(self):
        return self._pip_install(['pypdf'], 'Digital PDF support')

    def action_install_easyocr(self):
        return self._pip_install(['easyocr'], 'Image & scanned PDF OCR')

    def action_install_pymupdf(self):
        return self._pip_install(['PyMuPDF'], 'PDF page rendering')

    def action_install_python_docx(self):
        return self._pip_install(['python-docx'], 'Rich DOCX extraction')

    def action_install_olefile(self):
        return self._pip_install(['olefile'], 'Legacy .doc support')

    def action_install_full_ocr_stack(self):
        """One-click: install everything needed for full offline OCR."""
        return self._pip_install(
            ['pypdf', 'PyMuPDF', 'easyocr'],
            'Full OCR stack (pypdf + PyMuPDF + easyocr)',
        )

    # ── Connection test actions ───────────────────────────────────────────────

    def action_test_ollama_connection(self):
        """Called from the Settings page — test Ollama and show result in a notification."""
        from ..services import ai_providers as _ai
        url = (
            self.env['ir.config_parameter'].sudo().get_param(
                'document_intelligence.ollama_url', 'http://localhost:11434'
            ) or 'http://localhost:11434'
        )
        model = (
            self.env['ir.config_parameter'].sudo().get_param(
                'document_intelligence.ollama_model', 'llama3'
            ) or 'llama3'
        )
        provider = _ai.OllamaProvider(base_url=url, model=model)
        result = provider.ping()

        if result['ok']:
            models_text = ''
            if result.get('models'):
                models_text = '\nAvailable models: ' + ', '.join(result['models'][:8])
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Ollama — Connected'),
                    'message': result['message'] + models_text,
                    'type': 'success',
                    'sticky': False,
                },
            }
        raise UserError(f"Ollama connection failed:\n{result['message']}")

    def action_test_cloud_connection(self):
        """Called from the Settings page — test the selected cloud provider."""
        from ..services import ai_providers as _ai
        ICP = self.env['ir.config_parameter'].sudo()
        cloud_provider = (
            ICP.get_param('document_intelligence.ai_provider', 'groq') or 'groq'
        )
        openai_key = ICP.get_param('document_intelligence.openai_api_key', '') or ''
        groq_key = ICP.get_param('document_intelligence.groq_api_key', '') or ''
        anthropic_key = ICP.get_param('document_intelligence.anthropic_api_key', '') or ''

        try:
            provider = _ai.get_provider(
                provider_name=cloud_provider,
                openai_key=openai_key,
                groq_key=groq_key,
                anthropic_key=anthropic_key,
            )
        except _ai.ProviderAuthError as exc:
            raise UserError(str(exc))

        result = provider.ping()

        if result['ok']:
            models_text = ''
            if result.get('models'):
                models_text = '\nModels: ' + ', '.join(result['models'][:5])
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _(f'{cloud_provider.title()} — Connected'),
                    'message': result['message'] + models_text,
                    'type': 'success',
                    'sticky': False,
                },
            }
        raise UserError(f"Connection failed:\n{result['message']}")

    # ── Extraction Tier ───────────────────────────────────────────────────────

    doc_intel_extraction_tier = fields.Selection([
        ('rule_based', 'Rule-Based Only — Free, no setup, no API key'),
        ('ollama', 'Local AI (Ollama) — Free, private, runs on your server'),
        ('cloud', 'Cloud AI — Groq / OpenAI / Anthropic (API key required)'),
    ], string='Extraction Method',
        config_parameter='document_intelligence.extraction_tier',
        default='rule_based',
    )

    # ── Ollama (Tier 2) ───────────────────────────────────────────────────────

    doc_intel_ollama_url = fields.Char(
        string='Ollama Base URL',
        config_parameter='document_intelligence.ollama_url',
        default='http://localhost:11434',
        help='URL where Ollama is running. Default: http://localhost:11434',
    )
    doc_intel_ollama_model = fields.Char(
        string='Ollama Model',
        config_parameter='document_intelligence.ollama_model',
        default='llama3',
        help='Any model you have pulled with "ollama pull <model>". E.g. llama3, mistral, qwen2',
    )

    # ── AI Provider (Tier 3 — Cloud) ──────────────────────────────────────────

    doc_intel_ai_provider = fields.Selection([
        ('groq', 'Groq (Llama 3 — fast & free tier)'),
        ('openai', 'OpenAI (GPT-4o, GPT-4o-mini)'),
        ('anthropic', 'Anthropic (Claude)'),
    ], string='Cloud AI Provider',
        config_parameter='document_intelligence.ai_provider',
        default='groq',
    )

    # ── OpenAI ────────────────────────────────────────────────────────────────

    doc_intel_openai_api_key = fields.Char(
        string='OpenAI API Key',
        config_parameter='document_intelligence.openai_api_key',
    )
    doc_intel_openai_model = fields.Selection([
        ('gpt-4o-mini', 'GPT-4o-mini (fast, cheap)'),
        ('gpt-4o', 'GPT-4o (high quality)'),
        ('gpt-3.5-turbo', 'GPT-3.5 Turbo'),
    ],
        string='OpenAI Model',
        config_parameter='document_intelligence.openai_model',
        default='gpt-4o-mini',
    )

    # ── Groq ──────────────────────────────────────────────────────────────────

    doc_intel_groq_api_key = fields.Char(
        string='Groq API Key',
        config_parameter='document_intelligence.groq_api_key',
    )
    doc_intel_groq_model = fields.Selection([
        ('llama-3.3-70b-versatile', 'Llama 3.3 70B — best accuracy (recommended)'),
        ('llama-3.1-70b-versatile', 'Llama 3.1 70B'),
        ('llama-3.1-8b-instant',    'Llama 3.1 8B Instant — fastest'),
        ('gemma2-9b-it',            'Gemma 2 9B — good multilingual'),
        ('mixtral-8x7b-32768',      'Mixtral 8x7B — long documents (32K context)'),
    ],
        string='Groq Model',
        config_parameter='document_intelligence.groq_model',
        default='llama-3.3-70b-versatile',
    )

    # ── Anthropic ─────────────────────────────────────────────────────────────

    doc_intel_anthropic_api_key = fields.Char(
        string='Anthropic API Key',
        config_parameter='document_intelligence.anthropic_api_key',
    )
    doc_intel_anthropic_model = fields.Selection([
        ('claude-haiku-4-5-20251001', 'Claude Haiku (fast, cheap)'),
        ('claude-sonnet-4-6', 'Claude Sonnet (balanced)'),
        ('claude-opus-4-7', 'Claude Opus (high quality)'),
    ],
        string='Anthropic Model',
        config_parameter='document_intelligence.anthropic_model',
        default='claude-haiku-4-5-20251001',
    )

    # ── General ───────────────────────────────────────────────────────────────

    doc_intel_default_extraction_mode = fields.Selection([
        ('auto', 'Auto Detection'),
        ('custom', 'Custom Fields'),
        ('template', 'Template'),
    ], string='Default Extraction Mode',
        config_parameter='document_intelligence.default_extraction_mode',
        default='auto',
    )
    doc_intel_tesseract_lang = fields.Char(
        string='Tesseract OCR Language',
        config_parameter='document_intelligence.tesseract_lang',
        default='eng',
        help='Language codes for Tesseract. Example: eng, fra, eng+fra+kin',
    )

    # ── Batch & Quota ─────────────────────────────────────────────────────────

    doc_intel_batch_size = fields.Integer(
        string='Batch Processing Size',
        config_parameter='document_intelligence.batch_size',
        default=10,
    )
    doc_intel_quota_warning_threshold = fields.Integer(
        string='Quota Warning Threshold (tokens/day)',
        config_parameter='document_intelligence.quota_warning_threshold',
        default=100000,
    )

    # ── AUTO-APPROVAL RULES ────────────────────────────────────────────────────

    doc_intel_enable_auto_approval = fields.Boolean(
        string='Enable Auto-Approval',
        config_parameter='document_intelligence.enable_auto_approval',
        default=False,
    )
    doc_intel_auto_approval_confidence = fields.Float(
        string='Min Confidence % for Auto-Approval',
        config_parameter='document_intelligence.auto_approval_confidence',
        default=95.0,
    )
    doc_intel_auto_approval_amount = fields.Float(
        string='Max Amount for Auto-Approval',
        config_parameter='document_intelligence.auto_approval_amount',
        default=10000.0,
    )

    # ── LINE ITEM EXTRACTION ───────────────────────────────────────────────────

    doc_intel_enable_line_items = fields.Boolean(
        string='Extract Line Items',
        config_parameter='document_intelligence.enable_line_items',
        default=True,
    )
    doc_intel_line_item_confidence_threshold = fields.Float(
        string='Line Item Confidence Threshold',
        config_parameter='document_intelligence.line_item_confidence_threshold',
        default=80.0,
    )
    doc_intel_default_product_id = fields.Many2one(
        'product.product',
        string='Default Fallback Product',
        config_parameter='document_intelligence.default_product_id',
    )

    # ── MULTI-CURRENCY ──────────────────────────────────────────────────────────

    doc_intel_auto_currency_conversion = fields.Boolean(
        string='Auto Currency Conversion',
        config_parameter='document_intelligence.auto_currency_conversion',
        default=True,
    )
    doc_intel_default_currency_id = fields.Many2one(
        'res.currency',
        string='Default Foreign Currency',
        config_parameter='document_intelligence.default_currency_id',
    )

    # ── AUTO-CREATE PARTNERS ───────────────────────────────────────────────────

    doc_intel_auto_create_partners = fields.Boolean(
        string='Auto-Create Unknown Vendors',
        config_parameter='document_intelligence.auto_create_partners',
        default=True,
    )

    # ── TAX / VAT ───────────────────────────────────────────────────────────────

    doc_intel_auto_tax_detection = fields.Boolean(
        string='Auto Tax Detection',
        config_parameter='document_intelligence.auto_tax_detection',
        default=True,
    )
    doc_intel_default_tax_id = fields.Many2one(
        'account.tax',
        string='Default VAT Tax (18%)',
        config_parameter='document_intelligence.default_tax_id',
        domain=[('type_tax_use', '=', 'purchase')],
    )
    doc_intel_tax_rate_mapping = fields.Char(
        string='Tax Rate Mapping',
        config_parameter='document_intelligence.tax_rate_mapping',
        default='{"18": "VAT 18%", "15": "VAT 15%"}',
    )

    # ── DUPLICATE DETECTION ─────────────────────────────────────────────────────

    doc_intel_enable_duplicate_detection = fields.Boolean(
        string='Enable Duplicate Detection',
        config_parameter='document_intelligence.enable_duplicate_detection',
        default=True,
    )
    doc_intel_duplicate_action = fields.Selection([
        ('block', 'Block & Warn'),
        ('allow', 'Allow with Warning'),
        ('ignore', 'Ignore Duplicates'),
    ], string='Duplicate Invoice Action',
        config_parameter='document_intelligence.duplicate_action',
        default='block',
    )

    # ── PRODUCT MATCHING ────────────────────────────────────────────────────────

    doc_intel_product_match_fuzziness = fields.Float(
        string='Product Name Match Threshold %',
        config_parameter='document_intelligence.product_match_fuzziness',
        default=85.0,
    )
    doc_intel_auto_create_products = fields.Boolean(
        string='Auto-Create Unknown Products',
        config_parameter='document_intelligence.auto_create_products',
        default=False,
    )
    doc_intel_unknown_product_category_id = fields.Many2one(
        'product.category',
        string='Category for Auto-Created Products',
        config_parameter='document_intelligence.unknown_product_category_id',
    )

    # ── WORKFLOW & APPROVALS ────────────────────────────────────────────────────

    doc_intel_enable_approval_workflow = fields.Boolean(
        string='Enable Approval Workflow',
        config_parameter='document_intelligence.enable_approval_workflow',
        default=False,
    )
    doc_intel_approval_threshold = fields.Float(
        string='Approval Threshold (Company Currency)',
        config_parameter='document_intelligence.approval_threshold',
        default=5000.0,
    )
    doc_intel_auto_post_approved = fields.Boolean(
        string='Auto-Post Approved Bills',
        config_parameter='document_intelligence.auto_post_approved',
        default=False,
    )

    # ── EXPENSES ─────────────────────────────────────────────────────────────────

    doc_intel_expense_product_id = fields.Many2one(
        'product.product',
        string='Default Expense Product',
        config_parameter='document_intelligence.expense_product_id',
        domain=[('type', '=', 'service')],
    )

    # ── EMAIL INTEGRATION ────────────────────────────────────────────────────────

    doc_intel_email_inbound_enabled = fields.Boolean(
        string='Enable Email Inbound Processing',
        config_parameter='document_intelligence.email_inbound_enabled',
        default=False,
    )
    doc_intel_email_processor_user_id = fields.Many2one(
        'res.users',
        string='Email Processor User',
        config_parameter='document_intelligence.email_processor_user_id',
    )
    doc_intel_email_vendor_filter_domain = fields.Char(
        string='Email Vendor Filter',
        config_parameter='document_intelligence.email_vendor_filter_domain',
        default='[]',
    )

    # ── VALIDATION ───────────────────────────────────────────────────────────────

    doc_intel_validate_dates = fields.Boolean(
        string='Validate Date Ranges',
        config_parameter='document_intelligence.validate_dates',
        default=True,
    )
    doc_intel_validate_amounts = fields.Boolean(
        string='Validate Amount Positivity',
        config_parameter='document_intelligence.validate_amounts',
        default=True,
    )
    doc_intel_require_vendor = fields.Boolean(
        string='Require Vendor Name',
        config_parameter='document_intelligence.require_vendor',
        default=True,
    )

    # ── VENDOR-SPECIFIC LEARNING ────────────────────────────────────────────────

    doc_intel_enable_vendor_learning = fields.Boolean(
        string='Enable Vendor-Specific Learning',
        config_parameter='document_intelligence.enable_vendor_learning',
        default=True,
    )
    doc_intel_vendor_correction_threshold = fields.Integer(
        string='Vendor Correction Threshold',
        config_parameter='document_intelligence.vendor_correction_threshold',
        default=3,
    )

    # ── WEBHOOK ─────────────────────────────────────────────────────────────────

    doc_intel_webhook_enabled = fields.Boolean(
        string='Enable Webhook',
        config_parameter='document_intelligence.webhook_enabled',
        default=False,
    )
    doc_intel_webhook_url = fields.Char(
        string='Webhook URL',
        config_parameter='document_intelligence.webhook_url',
    )
    doc_intel_webhook_auth_type = fields.Selection([
        ('none', 'No Authentication'),
        ('bearer', 'Bearer Token'),
        ('basic', 'Basic Auth'),
    ], string='Webhook Auth Type',
        config_parameter='document_intelligence.webhook_auth_type',
        default='none',
    )
    doc_intel_webhook_token = fields.Char(
        string='Webhook Token / Password',
        config_parameter='document_intelligence.webhook_token',
    )
    doc_intel_webhook_events = fields.Selection([
        ('all', 'All Documents'),
        ('approved', 'Only Approved Documents'),
        ('error', 'Errors Only'),
    ], string='Events to Send',
        config_parameter='document_intelligence.webhook_events',
        default='approved',
    )

    # ── STORAGE & RETENTION ───────────────────────────────────────────────────────

    doc_intel_keep_raw_file = fields.Boolean(
        string='Keep Original File',
        config_parameter='document_intelligence.keep_raw_file',
        default=True,
    )
    doc_intel_auto_cleanup_days = fields.Integer(
        string='Auto-Cleanup After Days',
        config_parameter='document_intelligence.auto_cleanup_days',
        default=365,
    )

    # ── BATCH PROCESSING ──────────────────────────────────────────────────────────

    doc_intel_enable_batch_processing = fields.Boolean(
        string='Enable Batch Processing',
        config_parameter='document_intelligence.enable_batch_processing',
        default=True,
    )
    doc_intel_default_batch_size = fields.Integer(
        string='Default Batch Size',
        config_parameter='document_intelligence.default_batch_size',
        default=10,
    )

    # ── QR / BARCODE ───────────────────────────────────────────────────────────────

    doc_intel_enable_qr_barcode = fields.Boolean(
        string='Enable QR/Barcode Reading',
        config_parameter='document_intelligence.enable_qr_barcode',
        default=False,
    )
    doc_intel_qr_prepend_to_text = fields.Boolean(
        string='Prepend QR Data to OCR Text',
        config_parameter='document_intelligence.qr_prepend_to_text',
        default=True,
    )

    # ── CUSTOM EXTRACTION RULES ───────────────────────────────────────────────────

    doc_intel_enable_custom_rules = fields.Boolean(
        string='Enable Custom Extraction Rules',
        config_parameter='document_intelligence.enable_custom_rules',
        default=False,
    )
    doc_intel_custom_rule_priority = fields.Selection([
        ('before_ai', 'Apply Before AI (AI fills gaps)'),
        ('override_ai', 'Override AI (Custom rules take precedence)'),
        ('fallback', 'Use Only if AI Fails'),
    ], string='Custom Rule Priority',
        config_parameter='document_intelligence.custom_rule_priority',
        default='before_ai',
    )

    # ── RECEIPT → EXPENSE CLAIM ────────────────────────────────────────────────────

    doc_intel_auto_expense_creation = fields.Boolean(
        string='Auto-Create Expense Claims from Receipts',
        config_parameter='document_intelligence.auto_expense_creation',
        default=False,
    )
    doc_intel_expense_payment_mode = fields.Selection([
        ('own_account', 'Employee (Personal)'),
        ('company_account', 'Company Account'),
        ('both', 'Based on Amount Threshold'),
    ], string='Default Payment Mode',
        config_parameter='document_intelligence.expense_payment_mode',
        default='own_account',
    )

    # ── BANK RECONCILIATION ────────────────────────────────────────────────────────

    doc_intel_enable_bank_reconciliation = fields.Boolean(
        string='Enable Bank Statement Matching',
        config_parameter='document_intelligence.enable_bank_reconciliation',
        default=True,
    )
    doc_intel_bank_match_window_days = fields.Integer(
        string='Bank Match Window (Days)',
        config_parameter='document_intelligence.bank_match_window_days',
        default=30,
    )
    doc_intel_bank_auto_match = fields.Boolean(
        string='Auto-Match Bank Lines',
        config_parameter='document_intelligence.bank_auto_match',
        default=False,
    )

    # ── AUDIT TRAIL & REPORTING ────────────────────────────────────────────────────

    doc_intel_enable_audit_trail = fields.Boolean(
        string='Enable Comprehensive Audit Trail',
        config_parameter='document_intelligence.enable_audit_trail',
        default=True,
    )
    doc_intel_audit_retention_months = fields.Integer(
        string='Audit Log Retention (Months)',
        config_parameter='document_intelligence.audit_retention_months',
        default=24,
    )
    doc_intel_auto_generate_reports = fields.Boolean(
        string='Auto-Generate Monthly Reports',
        config_parameter='document_intelligence.auto_generate_reports',
        default=False,
    )

    # ── SCHEDULED EXTRACTION ───────────────────────────────────────────────────────

    doc_intel_scheduled_extraction_enabled = fields.Boolean(
        string='Enable Scheduled Email Extraction',
        config_parameter='document_intelligence.scheduled_extraction_enabled',
        default=False,
    )
    doc_intel_default_schedule_interval = fields.Integer(
        string='Default Schedule Interval (hours)',
        config_parameter='document_intelligence.default_schedule_interval',
        default=24,
    )
