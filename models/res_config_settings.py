from odoo import models, fields, api


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # ── AI Provider ───────────────────────────────────────────────────────────

    doc_intel_ai_provider = fields.Selection([
        ('openai', 'OpenAI (GPT-4o, GPT-4o-mini)'),
        ('groq', 'Groq (Llama 3 — fast & free tier)'),
    ], string='AI Provider',
        config_parameter='document_intelligence.ai_provider',
        default='openai',
    )

    # ── OpenAI ────────────────────────────────────────────────────────────────

    doc_intel_openai_api_key = fields.Char(
        string='OpenAI API Key',
        config_parameter='document_intelligence.openai_api_key',
    )
    doc_intel_openai_model = fields.Char(
        string='OpenAI Model',
        config_parameter='document_intelligence.openai_model',
        default='gpt-4o-mini',
        help='Examples: gpt-4o-mini (fast, cheap), gpt-4o (best accuracy)',
    )

    # ── Groq ──────────────────────────────────────────────────────────────────

    doc_intel_groq_api_key = fields.Char(
        string='Groq API Key',
        config_parameter='document_intelligence.groq_api_key',
    )
    doc_intel_groq_model = fields.Char(
        string='Groq Model',
        config_parameter='document_intelligence.groq_model',
        default='llama-3.3-70b-versatile',
        help='Examples: llama-3.3-70b-versatile, mixtral-8x7b-32768',
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
