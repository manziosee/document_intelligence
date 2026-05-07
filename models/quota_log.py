from odoo import models, fields, api, _


class DocumentIntelligenceQuotaLog(models.Model):
    _name = 'document.intelligence.quota.log'
    _description = 'Document Intelligence API Quota Log'
    _order = 'call_date desc'
    _rec_name = 'provider'

    document_id = fields.Many2one(
        'document.intelligence.record',
        string='Document',
        ondelete='set null',
        readonly=True,
    )
    user_id = fields.Many2one(
        'res.users', string='User',
        default=lambda self: self.env.user,
        readonly=True,
    )
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company,
        readonly=True,
    )
    call_date = fields.Datetime(
        string='Date', default=fields.Datetime.now, readonly=True,
    )
    provider = fields.Selection([
        ('openai', 'OpenAI'),
        ('groq', 'Groq'),
        ('anthropic', 'Anthropic'),
    ], string='Provider', readonly=True)
    model_used = fields.Char(string='Model', readonly=True)
    text_chars = fields.Integer(string='Characters Sent', readonly=True)
    estimated_tokens = fields.Integer(
        string='Est. Tokens',
        compute='_compute_estimated_tokens',
        store=True,
    )
    success = fields.Boolean(string='Success', default=True, readonly=True)
    response_ms = fields.Integer(string='Response Time (ms)', readonly=True)

    @api.depends('text_chars')
    def _compute_estimated_tokens(self):
        for rec in self:
            # ~4 chars per token is a standard rough estimate
            rec.estimated_tokens = int((rec.text_chars or 0) / 4)

    # ── Class-level helper ────────────────────────────────────────────────────

    @api.model
    def log_call(self, document, provider, model_used, text_chars,
                 success=True, response_ms=0):
        """Record a single AI API call. Called from document_processor."""
        self.sudo().create({
            'document_id': document.id if document else False,
            'user_id': document.create_uid.id if document else self.env.user.id,
            'provider': provider,
            'model_used': model_used,
            'text_chars': text_chars,
            'success': success,
            'response_ms': response_ms,
        })

    @api.model
    def get_user_stats(self, days=30):
        """Return summary stats for the current user over the last N days."""
        from datetime import datetime, timedelta
        since = fields.Datetime.now() - timedelta(days=days)
        records = self.search([
            ('user_id', '=', self.env.user.id),
            ('call_date', '>=', since),
        ])
        total = len(records)
        ok = len(records.filtered('success'))
        return {
            'total_calls': total,
            'failed_calls': total - ok,
            'total_tokens': sum(records.mapped('estimated_tokens')),
            'providers_used': list(set(r for r in records.mapped('provider') if r)),
        }
