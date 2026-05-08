"""
Onboarding setup wizard — shown automatically on first install.
Guides the user through: choose tier → configure → test → done.
"""
import logging
from odoo import models, fields, _

_logger = logging.getLogger(__name__)


class DocumentIntelligenceSetupWizard(models.TransientModel):
    _name = 'document.intelligence.setup.wizard'
    _description = 'Document Intelligence — Setup Wizard'

    step = fields.Selection([
        ('welcome', 'Welcome'),
        ('tier', 'Choose Method'),
        ('configure', 'Configure'),
        ('done', 'Ready'),
    ], default='welcome', required=True)

    # ── Tier choice ───────────────────────────────────────────────────────────

    extraction_tier = fields.Selection([
        ('rule_based', 'Rule-Based (Free, no setup)'),
        ('ollama', 'Local AI — Ollama (Free, private)'),
        ('cloud', 'Cloud AI — Groq / OpenAI / Anthropic'),
    ], string='Extraction Method', default='rule_based', required=True)

    # ── Cloud provider choice ─────────────────────────────────────────────────

    cloud_provider = fields.Selection([
        ('groq', 'Groq — Free tier, fast (recommended)'),
        ('openai', 'OpenAI — GPT-4o'),
        ('anthropic', 'Anthropic — Claude'),
    ], string='Cloud Provider', default='groq')

    # ── API keys ──────────────────────────────────────────────────────────────

    groq_api_key = fields.Char(string='Groq API Key', password=True)
    openai_api_key = fields.Char(string='OpenAI API Key', password=True)
    anthropic_api_key = fields.Char(string='Anthropic API Key', password=True)

    # ── Ollama config ─────────────────────────────────────────────────────────

    ollama_url = fields.Char(string='Ollama URL', default='http://localhost:11434')
    ollama_model = fields.Char(string='Ollama Model', default='llama3')

    # ── Test result ───────────────────────────────────────────────────────────

    test_result = fields.Char(string='Connection Test Result', readonly=True)
    test_ok = fields.Boolean(default=False)

    # ── Navigation ────────────────────────────────────────────────────────────

    def action_next(self):
        step_order = ['welcome', 'tier', 'configure', 'done']
        idx = step_order.index(self.step)
        if self.step == 'tier' and self.extraction_tier == 'rule_based':
            # Rule-based needs no configuration — skip to done
            self.step = 'done'
        elif idx < len(step_order) - 1:
            self.step = step_order[idx + 1]
        return self._reopen()

    def action_back(self):
        step_order = ['welcome', 'tier', 'configure', 'done']
        idx = step_order.index(self.step)
        if idx > 0:
            self.step = step_order[idx - 1]
        return self._reopen()

    def action_test_connection(self):
        """Quick connectivity test for the selected tier/provider."""
        try:
            if self.extraction_tier == 'ollama':
                self._test_ollama()
            elif self.extraction_tier == 'cloud':
                self._test_cloud()
            else:
                self.test_result = 'Rule-based extraction needs no connection test.'
                self.test_ok = True
        except Exception as exc:
            self.test_result = f'Failed: {exc}'
            self.test_ok = False
        return self._reopen()

    def _test_ollama(self):
        from ..services import ai_providers as _ai
        provider = _ai.OllamaProvider(
            base_url=self.ollama_url or 'http://localhost:11434',
            model=self.ollama_model or 'llama3',
        )
        result = provider.ping()
        self.test_ok = result['ok']
        self.test_result = result['message']
        if result['ok'] and result.get('models'):
            self.test_result += f" Models: {', '.join(result['models'][:5])}"

    def _test_cloud(self):
        from ..services import ai_providers as _ai
        provider = _ai.get_provider(
            provider_name=self.cloud_provider,
            openai_key=self.openai_api_key or '',
            groq_key=self.groq_api_key or '',
            anthropic_key=self.anthropic_api_key or '',
        )
        result = provider.ping()
        self.test_ok = result['ok']
        self.test_result = result['message']

    def action_save_and_finish(self):
        """Write settings to ir.config_parameter and close the wizard."""
        ICP = self.env['ir.config_parameter'].sudo()

        ICP.set_param('document_intelligence.extraction_tier', self.extraction_tier)

        if self.extraction_tier == 'ollama':
            if self.ollama_url:
                ICP.set_param('document_intelligence.ollama_url', self.ollama_url)
            if self.ollama_model:
                ICP.set_param('document_intelligence.ollama_model', self.ollama_model)

        elif self.extraction_tier == 'cloud':
            ICP.set_param('document_intelligence.ai_provider', self.cloud_provider)
            if self.groq_api_key:
                ICP.set_param('document_intelligence.groq_api_key', self.groq_api_key)
            if self.openai_api_key:
                ICP.set_param('document_intelligence.openai_api_key', self.openai_api_key)
            if self.anthropic_api_key:
                ICP.set_param('document_intelligence.anthropic_api_key', self.anthropic_api_key)

        # Mark setup as completed so we don't show again
        ICP.set_param('document_intelligence.setup_done', 'True')

        _logger.info(
            'Document Intelligence setup completed — tier: %s', self.extraction_tier
        )
        return {'type': 'ir.actions.act_window_close'}

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
