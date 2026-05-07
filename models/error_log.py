import traceback as tb_module
from odoo import models, fields, api, _


class DocumentIntelligenceErrorLog(models.Model):
    _name = 'document.intelligence.error.log'
    _description = 'Document Intelligence Error Log'
    _order = 'error_date desc'
    _rec_name = 'error_type'

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
    error_date = fields.Datetime(
        string='Date', default=fields.Datetime.now, readonly=True,
    )
    error_type = fields.Selection([
        ('ocr', 'OCR Extraction'),
        ('ai', 'AI Processing'),
        ('network', 'Network / API'),
        ('quota', 'Quota / Rate Limit'),
        ('other', 'Other'),
    ], string='Error Type', required=True, readonly=True)

    provider = fields.Char(string='AI Provider', readonly=True)
    file_name = fields.Char(string='File Name', readonly=True)
    error_message = fields.Text(string='Error Message', readonly=True)
    traceback_text = fields.Text(string='Traceback', readonly=True)

    resolved = fields.Boolean(string='Resolved', default=False)
    resolution_note = fields.Text(string='Resolution Note')

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_mark_resolved(self):
        self.write({'resolved': True})

    def action_open_document(self):
        self.ensure_one()
        if not self.document_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'document.intelligence.record',
            'res_id': self.document_id.id,
            'view_mode': 'form',
        }

    # ── Class-level helper ────────────────────────────────────────────────────

    @api.model
    def log_error(self, document, error_type, error, provider=None):
        """Log an OCR/AI error. Called from document_processor."""
        vals = {
            'error_type': error_type,
            'error_message': str(error),
            'traceback_text': tb_module.format_exc(),
            'provider': provider or '',
        }
        if document:
            vals.update({
                'document_id': document.id,
                'user_id': document.create_uid.id,
                'file_name': document.file_name or '',
            })
        self.sudo().create(vals)
