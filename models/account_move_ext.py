from odoo import models, fields, api, _


class AccountMove(models.Model):
    _inherit = 'account.move'

    doc_intel_count = fields.Integer(
        string='AI Extractions',
        compute='_compute_doc_intel_count',
    )

    def _compute_doc_intel_count(self):
        for rec in self:
            rec.doc_intel_count = self.env['document.intelligence.record'].search_count([
                ('source_model', '=', 'account.move'),
                ('source_res_id', '=', rec.id),
            ])

    def action_open_doc_intel(self):
        """Open Document Intelligence records linked to this invoice."""
        self.ensure_one()
        records = self.env['document.intelligence.record'].search([
            ('source_model', '=', 'account.move'),
            ('source_res_id', '=', self.id),
        ])
        if len(records) == 1:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'document.intelligence.record',
                'res_id': records.id,
                'view_mode': 'form',
            }
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'document.intelligence.record',
            'view_mode': 'tree,form',
            'domain': [('id', 'in', records.ids)],
            'name': _('AI Extractions for %s') % self.name,
        }

    def action_extract_with_ai(self):
        """Launch the From-Odoo wizard for this invoice."""
        self.ensure_one()
        return self.env['document.intelligence.from.odoo.wizard'].action_launch_for_record(
            self._name, self.id
        )
