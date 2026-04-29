"""
Wizard: Extract from Existing Odoo Record

Called via server actions on account.move, hr.applicant, res.partner, etc.
Shows all file attachments on the record and lets the user pick one to extract.
"""
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class FromOdooWizard(models.TransientModel):
    _name = 'document.intelligence.from.odoo.wizard'
    _description = 'Extract from Existing Odoo Document'

    # ── Source record info (set by server action) ─────────────────────────────

    source_model = fields.Char(string='Source Model', readonly=True)
    source_res_id = fields.Integer(string='Source Record ID', readonly=True)
    source_record_name = fields.Char(string='Source Record', readonly=True)

    # ── Available attachments ─────────────────────────────────────────────────

    available_attachment_ids = fields.Many2many(
        'ir.attachment',
        string='Available Documents',
        compute='_compute_available_attachments',
    )
    attachment_id = fields.Many2one(
        'ir.attachment',
        string='Select Document to Extract',
        required=True,
        domain="[('id', 'in', available_attachment_ids)]",
    )

    @api.depends('source_model', 'source_res_id')
    def _compute_available_attachments(self):
        for wiz in self:
            if wiz.source_model and wiz.source_res_id:
                attachments = self.env['ir.attachment'].search([
                    ('res_model', '=', wiz.source_model),
                    ('res_id', '=', wiz.source_res_id),
                    ('type', '=', 'binary'),
                ])
                wiz.available_attachment_ids = attachments
            else:
                wiz.available_attachment_ids = False

    # ── Extraction options ────────────────────────────────────────────────────

    extraction_mode = fields.Selection([
        ('auto', 'Auto Detection'),
        ('custom', 'Custom Fields'),
        ('template', 'Template'),
    ], default='auto', string='Extraction Mode', required=True)

    template_id = fields.Many2one(
        'document.intelligence.template',
        string='Extraction Template',
    )
    custom_fields_input = fields.Char(
        string='Fields to Extract',
        help='Comma-separated. Example: vendor, date, total, phone',
    )
    extra_prompt = fields.Text(
        string='AI Instructions (optional)',
        help='Additional context for the AI.',
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Actions
    # ─────────────────────────────────────────────────────────────────────────

    def action_extract(self):
        """Create a DocumentRecord from the selected attachment and run extraction."""
        self.ensure_one()

        if not self.attachment_id:
            raise UserError(_('Please select a document to extract.'))

        # Build values for the document record
        vals = {
            'name': self.attachment_id.name,
            'input_mode': 'existing',
            'source_attachment_id': self.attachment_id.id,
            'source_model': self.source_model,
            'source_res_id': self.source_res_id,
            'file_name': self.attachment_id.name,
            'extraction_mode': self.extraction_mode,
            'extra_prompt': self.extra_prompt or '',
        }
        if self.extraction_mode == 'custom' and self.custom_fields_input:
            vals['custom_fields_input'] = self.custom_fields_input
        if self.extraction_mode == 'template' and self.template_id:
            vals['template_id'] = self.template_id.id

        # Link back to specific source model records for smart suggested_action
        if self.source_model == 'account.move':
            vals['linked_move_id'] = self.source_res_id
            vals['suggested_action'] = 'update_invoice'
        elif self.source_model == 'hr.applicant':
            vals['linked_applicant_id'] = self.source_res_id
            vals['suggested_action'] = 'create_hr_applicant'

        record = self.env['document.intelligence.record'].create(vals)
        return record.action_extract()

    @api.model
    def action_launch_for_record(self, source_model, source_res_id):
        """
        Called from server actions. Opens this wizard pre-filled for a specific record.
        Use from ir.actions.server like:
            env['document.intelligence.from.odoo.wizard'].action_launch_for_record(
                model._name, record.id
            )
        """
        record = self.env[source_model].browse(source_res_id)
        attachments = self.env['ir.attachment'].search([
            ('res_model', '=', source_model),
            ('res_id', '=', source_res_id),
            ('type', '=', 'binary'),
        ])
        if not attachments:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('No documents found'),
                    'message': _(
                        'There are no file attachments on "%s". '
                        'Please attach a PDF or image first.'
                    ) % record.display_name,
                    'type': 'warning',
                },
            }

        wiz = self.create({
            'source_model': source_model,
            'source_res_id': source_res_id,
            'source_record_name': record.display_name,
            # Pre-select if only one attachment
            'attachment_id': attachments[0].id if len(attachments) == 1 else False,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': wiz.id,
            'view_mode': 'form',
            'target': 'new',
            'name': _('Extract from: %s') % record.display_name,
        }
