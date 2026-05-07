from odoo import models, fields, api, _
from odoo.exceptions import UserError


class DocumentReviewWizard(models.TransientModel):
    """Human-in-the-loop review step before Odoo record creation."""
    _name = 'document.intelligence.review.wizard'
    _description = 'Document Review & Approval'

    record_id = fields.Many2one(
        'document.intelligence.record',
        string='Document',
        required=True,
        ondelete='cascade',
    )

    # ── Read-only context ──────────────────────────────────────────────────

    detected_document_type = fields.Selection(
        related='record_id.detected_document_type', readonly=True,
    )
    confidence_score = fields.Float(
        related='record_id.confidence_score', readonly=True,
    )
    raw_text = fields.Text(
        related='record_id.raw_text', readonly=True, string='Raw OCR Text',
    )
    processing_notes = fields.Text(
        related='record_id.processing_notes', readonly=True,
    )
    extracted_data_json = fields.Text(
        related='record_id.extracted_data_json', readonly=True,
    )

    # ── Editable extracted fields (copied from record, user can correct) ───

    suggested_action = fields.Selection([
        ('create_invoice', 'Create Vendor Bill'),
        ('update_invoice', 'Update Existing Invoice'),
        ('create_partner', 'Create / Update Contact'),
        ('create_hr_applicant', 'Create HR Applicant'),
        ('create_expense_claim', 'Create Expense Claim'),
        ('store_only', 'Store Document Only'),
    ], string='Action to Perform', required=True)

    vendor_name = fields.Char(string='Vendor / Sender')
    document_date = fields.Date(string='Document Date')
    total_amount = fields.Float(string='Total Amount', digits=(16, 2))
    currency_detected = fields.Char(string='Currency')
    tax_amount = fields.Float(string='Tax Amount', digits=(16, 2))
    reference_number = fields.Char(string='Reference / Invoice No.')
    contact_name = fields.Char(string='Contact Name')
    contact_phone = fields.Char(string='Phone')
    contact_email = fields.Char(string='Email')
    contact_address = fields.Text(string='Address')
    # New fields
    vat_number = fields.Char(string='VAT / Tax ID')
    iban = fields.Char(string='IBAN')
    swift = fields.Char(string='SWIFT / BIC')
    expense_employee_id = fields.Many2one(
        'hr.employee',
        string='Expense Employee',
        help='Employee who will submit the expense claim (for receipts).',
    )
    extra_fields_display = fields.Text(
        string='Other Extracted Fields', readonly=True,
    )
    partner_id = fields.Many2one('res.partner', string='Matched Partner')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        record_id = self.env.context.get('default_record_id')
        if record_id:
            rec = self.env['document.intelligence.record'].browse(record_id)
            res.update({
                'record_id': rec.id,
                'suggested_action': rec.suggested_action or 'store_only',
                'vendor_name': rec.vendor_name,
                'document_date': rec.document_date,
                'total_amount': rec.total_amount,
                'currency_detected': rec.currency_detected,
                'tax_amount': rec.tax_amount,
                'reference_number': rec.reference_number,
                'contact_name': rec.contact_name,
                'contact_phone': rec.contact_phone,
                'contact_email': rec.contact_email,
                'contact_address': rec.contact_address,
                'vat_number': rec.vat_number,
                'iban': rec.iban,
                'swift': rec.swift,
                'expense_employee_id': rec.expense_employee_id.id if rec.expense_employee_id else False,
                'extra_fields_display': rec.extra_fields_display,
                'partner_id': rec.partner_id.id if rec.partner_id else False,
            })
        return res

    def _correction_vals(self):
        """Return the dict of user-corrected values to write back to the document record."""
        return {
            'vendor_name': self.vendor_name,
            'document_date': self.document_date,
            'total_amount': self.total_amount,
            'currency_detected': self.currency_detected,
            'tax_amount': self.tax_amount,
            'reference_number': self.reference_number,
            'contact_name': self.contact_name,
            'contact_phone': self.contact_phone,
            'contact_email': self.contact_email,
            'contact_address': self.contact_address,
            'vat_number': self.vat_number,
            'iban': self.iban,
            'swift': self.swift,
            'expense_employee_id': self.expense_employee_id.id if self.expense_employee_id else False,
            'partner_id': self.partner_id.id if self.partner_id else False,
            'suggested_action': self.suggested_action,
        }

    def _get_record(self):
        """Return a fresh browse so the OLS resolves write() with the correct signature."""
        return self.env['document.intelligence.record'].browse(self.record_id.id)

    def action_approve_and_create(self):
        """User approved — push corrections back to the record and create Odoo record."""
        self.ensure_one()
        record = self._get_record()
        record.write(self._correction_vals())
        return record.action_create_odoo_record()

    def action_save_and_close(self):
        """Save corrections without creating an Odoo record yet."""
        self.ensure_one()
        self._get_record().write(self._correction_vals())
        return {'type': 'ir.actions.act_window_close'}

    def action_reject(self):
        """Reject extraction — reset the document to draft."""
        self.ensure_one()
        self._get_record().action_reset_to_draft()
        return {'type': 'ir.actions.act_window_close'}
