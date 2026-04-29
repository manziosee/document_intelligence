from odoo import models, fields, api, _

# Only extend hr.applicant when hr_recruitment is installed
try:
    from odoo.addons.hr_recruitment.models.hr_applicant import HrApplicant as _HrApplicantCheck  # noqa
    _HR_RECRUITMENT_INSTALLED = True
except ImportError:
    _HR_RECRUITMENT_INSTALLED = False


if _HR_RECRUITMENT_INSTALLED:

    class HrApplicant(models.Model):
        _inherit = 'hr.applicant'

        doc_intel_count = fields.Integer(
            string='AI Extractions',
            compute='_compute_doc_intel_count',
        )

        def _compute_doc_intel_count(self):
            for rec in self:
                rec.doc_intel_count = self.env['document.intelligence.record'].search_count([
                    ('source_model', '=', 'hr.applicant'),
                    ('source_res_id', '=', rec.id),
                ])

        def action_open_doc_intel(self):
            self.ensure_one()
            records = self.env['document.intelligence.record'].search([
                ('source_model', '=', 'hr.applicant'),
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
                'name': _('AI Extractions for %s') % self.display_name,
            }

        def action_extract_cv_with_ai(self):
            self.ensure_one()
            return self.env['document.intelligence.from.odoo.wizard'].action_launch_for_record(
                self._name, self.id
            )
