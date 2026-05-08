import json
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Module-level so it is never intercepted by Odoo's metaclass
_DOC_TYPE_LABELS = {
    'invoice':          'Invoice',
    'proof_of_payment': 'Proof of Payment',
    'receipt':          'Receipt',
    'proforma':         'Proforma Invoice',
    'contract':         'Contract',
    'cv':               'CV',
    'form':             'Form',
    'general':          'Document',
}


class DocumentRecord(models.Model):
    _name = 'document.intelligence.record'
    _description = 'Document Intelligence Record'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    # ── Basic info ────────────────────────────────────────────────────────────

    name = fields.Char(
        string='Document Name', required=True,
        default=lambda self: _('New Document'), tracking=True,
    )
    state = fields.Selection([
        ('draft', 'Draft'),
        ('processing', 'Processing'),
        ('review', 'Under Review'),
        ('done', 'Done'),
        ('error', 'Error'),
    ], default='draft', string='Status', tracking=True, readonly=True)

    # ── Input mode ────────────────────────────────────────────────────────────

    input_mode = fields.Selection([
        ('upload', 'Upload New File'),
        ('existing', 'Select from Odoo'),
    ], default='upload', string='Document Source', required=True)

    # Option A — upload
    file_data = fields.Binary(string='Upload Document', attachment=True)
    file_name = fields.Char(string='File Name')
    file_mimetype = fields.Char(string='MIME Type', compute='_compute_file_mimetype', store=True)

    @api.depends('file_name')
    def _compute_file_mimetype(self):
        for rec in self:
            fn = (rec.file_name or '').lower()
            if fn.endswith('.pdf'):
                rec.file_mimetype = 'application/pdf'
            elif fn.endswith('.docx'):
                rec.file_mimetype = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            elif fn.endswith(('.jpg', '.jpeg')):
                rec.file_mimetype = 'image/jpeg'
            elif fn.endswith('.png'):
                rec.file_mimetype = 'image/png'
            elif fn.endswith(('.tiff', '.tif')):
                rec.file_mimetype = 'image/tiff'
            else:
                rec.file_mimetype = 'application/octet-stream'

    # Option B — existing Odoo attachment
    source_attachment_id = fields.Many2one(
        'ir.attachment', string='Existing Odoo Document',
        domain=[('type', '=', 'binary')],
    )
    source_model = fields.Char(string='Source Model', readonly=True)
    source_res_id = fields.Integer(string='Source Record ID', readonly=True)
    source_record_display = fields.Char(
        string='Source Record',
        compute='_compute_source_record_display', store=True,
    )

    @api.depends('source_model', 'source_res_id')
    def _compute_source_record_display(self):
        for rec in self:
            if rec.source_model and rec.source_res_id:
                try:
                    obj = self.env[rec.source_model].browse(rec.source_res_id)
                    rec.source_record_display = f'{rec.source_model} → {obj.display_name}'
                except Exception:
                    rec.source_record_display = f'{rec.source_model} #{rec.source_res_id}'
            else:
                rec.source_record_display = False

    @api.onchange('source_attachment_id')
    def _onchange_source_attachment(self):
        if self.source_attachment_id:
            self.file_name = self.source_attachment_id.name
            if not self.name or self.name == _('New Document'):
                self.name = self.source_attachment_id.name

    # ── Extraction configuration ───────────────────────────────────────────────

    extraction_mode = fields.Selection([
        ('auto', 'Auto Detection'),
        ('custom', 'Custom Fields'),
        ('template', 'Template'),
    ], default='auto', string='Extraction Mode', tracking=True)

    template_id = fields.Many2one('document.intelligence.template', string='Template')
    custom_fields_input = fields.Char(
        string='Fields to Extract',
        help='Comma-separated. Example: vendor, date, total, phone, address',
    )
    extra_prompt = fields.Text(
        string='AI Instructions',
        help='Extra context for the AI. Example: "Amounts are in RWF."',
    )

    # ── Per-document OCR settings ─────────────────────────────────────────────

    ocr_language = fields.Char(
        string='OCR Language',
        help='Tesseract language code(s). Leave blank to use the system default. '
             'Example: eng, fra, eng+fra+kin',
    )
    force_re_ocr = fields.Boolean(
        string='Force Re-OCR',
        default=False,
        help='Re-run OCR even if raw text was already extracted. '
             'Useful after changing the OCR language.',
    )

    # ── Raw OCR text (cached) ─────────────────────────────────────────────────

    raw_text = fields.Text(string='Raw Extracted Text', readonly=True)

    # ── AI output ────────────────────────────────────────────────────────────

    detected_document_type = fields.Selection([
        ('invoice', 'Invoice'),
        ('proof_of_payment', 'Proof of Payment'),
        ('receipt', 'Receipt'),
        ('proforma', 'Proforma Invoice'),
        ('contract', 'Contract'),
        ('cv', 'CV / Resume'),
        ('form', 'Form'),
        ('general', 'General Document'),
    ], string='Detected Type', readonly=True, tracking=True)

    extracted_data_json = fields.Text(string='Extracted JSON', readonly=True)
    confidence_score = fields.Float(string='Confidence %', readonly=True)
    processing_notes = fields.Text(string='Processing Notes', readonly=True)

    # ── Editable extracted fields ─────────────────────────────────────────────

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
    extra_fields_display = fields.Text(string='Other Extracted Fields', readonly=True)

    # ── Odoo matching ─────────────────────────────────────────────────────────

    suggested_action = fields.Selection([
        ('create_invoice', 'Create Vendor Bill'),
        ('update_invoice', 'Update Existing Invoice'),
        ('create_partner', 'Create / Update Contact'),
        ('create_hr_applicant', 'Create HR Applicant'),
        ('create_expense_claim', 'Create Expense Claim'),
        ('store_only', 'Store Document Only'),
    ], string='Suggested Action', readonly=True, tracking=True)

    partner_id = fields.Many2one('res.partner', string='Matched Partner')

    # ── Created / linked records ──────────────────────────────────────────────

    created_move_id = fields.Many2one('account.move', string='Created Bill', readonly=True)
    created_partner_id = fields.Many2one('res.partner', string='Created Contact', readonly=True)
    linked_move_id = fields.Many2one('account.move', string='Linked Invoice', readonly=True)
    linked_applicant_id = fields.Many2one('hr.applicant', string='Linked Applicant', readonly=True)

    # ── ADDITIONAL FIELDS ───────────────────────────────────────────────────────────

    # Additional extracted fields
    vat_number = fields.Char(string='VAT / Tax ID')
    iban = fields.Char(string='IBAN / Bank Account')
    swift = fields.Char(string='SWIFT / BIC')
    bank_statement_ref = fields.Char(string='Bank Statement Reference')

    # Receipt/Expense claim flow
    is_receipt = fields.Boolean(string='Is Receipt', compute='_compute_is_receipt', store=False)
    expense_claim_id = fields.Many2one('hr.expense', string='Expense Claim Created', readonly=True)
    expense_employee_id = fields.Many2one('hr.employee', string='Expense Employee')

    # Bank reconciliation
    bank_statement_line_id = fields.Many2one('account.bank.statement.line', string='Matched Bank Statement Line', readonly=True)
    reconciliation_status = fields.Selection([
        ('pending', 'Pending Reconciliation'),
        ('matched', 'Matched'),
        ('reconciled', 'Reconciled'),
    ], string='Reconciliation Status', compute='_compute_reconciliation_status')

    # Line items
    line_item_ids = fields.One2many('document.intelligence.line.item', 'document_id', string='Line Items', copy=True)
    line_item_count = fields.Integer(string='Line Count', compute='_compute_line_item_count')
    has_line_items = fields.Boolean(string='Has Line Items', compute='_compute_line_item_count')

    # Duplicate detection
    file_hash = fields.Char(string='File SHA256 Hash', readonly=True, index=True, copy=False)
    duplicate_check_id = fields.Many2one('document.intelligence.duplicate.check', string='Duplicate Check Record', readonly=True, copy=False)
    is_duplicate = fields.Boolean(string='Is Duplicate', compute='_compute_is_duplicate', store=False)
    duplicate_of_ids = fields.One2many('document.intelligence.duplicate.check', 'duplicate_of_id', string='Possible Duplicates', compute='_compute_duplicate_suggestions')

    # Validation results
    validation_ids = fields.One2many('document.intelligence.validation', 'document_id', string='Validation Results')
    has_validation_errors = fields.Boolean(string='Has Validation Errors', compute='_compute_validation_errors')
    validation_error_count = fields.Integer(string='Error Count', compute='_compute_validation_errors')
    validation_warning_count = fields.Integer(string='Warning Count', compute='_compute_validation_errors')

    # Vendor corrections & learning
    vendor_correction_ids = fields.One2many('document.intelligence.vendor.correction', 'document_id', string='Vendor Corrections', readonly=True)
    vendor_correction_count = fields.Integer(string='Corrections Made', compute='_compute_vendor_corrections')
    vendor_pattern_applied = fields.Boolean(string='Vendor Pattern Applied', readonly=True, help='If True, a vendor-specific correction pattern was auto-applied during extraction.')

    # Auto-approval rules
    auto_approval_rule_id = fields.Many2one('document.intelligence.auto.approval.rule', string='Matched Auto-Approval Rule', readonly=True)
    auto_approved = fields.Boolean(string='Auto-Approved', readonly=True, help='Document was automatically approved and created without manual review.')
    requires_approval = fields.Boolean(string='Requires Manager Approval', compute='_compute_requires_approval', store=False)

    # Cost & performance tracking
    estimated_token_count = fields.Integer(string='Estimated Tokens', readonly=True)
    extraction_cost_usd = fields.Float(string='Extraction Cost (USD)', readonly=True)
    processing_time_ms = fields.Integer(string='Processing Time (ms)', readonly=True)

    # Batch processing
    batch_id = fields.Many2one('document.intelligence.batch', string='Batch', index=True)
    email_message_id = fields.Char(string='Email Message ID', help='If extracted from email, stores the Message-ID.')
    email_sender = fields.Char(string='Email Sender')
    email_received_date = fields.Datetime(string='Email Received Date')

    # Multi-currency
    currency_id = fields.Many2one('res.currency', string='Currency', compute='_compute_currency', store=True)

    # Tax details
    tax_rate = fields.Float(string='Tax Rate %')
    tax_ids = fields.Many2many('account.tax', 'di_record_tax_rel', string='Detected Taxes', help='Taxes detected from the document.')
    tax_applicability = fields.Selection([
        ('none', 'No Tax'),
        ('single', 'Single Tax'),
        ('multiple', 'Multiple Taxes'),
    ], string='Tax Applicability', compute='_compute_tax_applicability')

    # ── Quota + error log counts (stat buttons) ───────────────────────────────

    quota_log_count = fields.Integer(
        string='API Calls', compute='_compute_log_counts',
    )
    error_log_count = fields.Integer(
        string='Errors', compute='_compute_log_counts',
    )

    @api.depends('line_item_ids')
    def _compute_line_item_count(self):
        for rec in self:
            rec.line_item_count = len(rec.line_item_ids)
            rec.has_line_items = bool(rec.line_item_ids)

    @api.depends('detected_document_type')
    def _compute_is_receipt(self):
        for rec in self:
            rec.is_receipt = rec.detected_document_type == 'receipt'

    @api.depends('bank_statement_line_id')
    def _compute_reconciliation_status(self):
        for rec in self:
            if rec.bank_statement_line_id:
                rec.reconciliation_status = 'matched'
            else:
                rec.reconciliation_status = 'pending'

    @api.depends('duplicate_check_id')
    def _compute_is_duplicate(self):
        for rec in self:
            rec.is_duplicate = bool(rec.duplicate_check_id)

    def _compute_duplicate_suggestions(self):
        for rec in self:
            rec.duplicate_of_ids = self.env['document.intelligence.duplicate.check'].search([
                ('duplicate_of_id', '=', rec.id)
            ])

    @api.depends('validation_ids', 'validation_ids.severity', 'validation_ids.resolved')
    def _compute_validation_errors(self):
        for rec in self:
            active = rec.validation_ids.filtered(lambda v: not v.resolved)
            rec.has_validation_errors = any(v.severity == 'error' for v in active)
            rec.validation_error_count = len(active.filtered(lambda v: v.severity == 'error'))
            rec.validation_warning_count = len(active.filtered(lambda v: v.severity == 'warning'))

    @api.depends('vendor_correction_ids')
    def _compute_vendor_corrections(self):
        for rec in self:
            rec.vendor_correction_count = len(rec.vendor_correction_ids)

    def _compute_requires_approval(self):
        ICP = self.env['ir.config_parameter'].sudo()
        threshold = float(ICP.get_param('document_intelligence.approval_threshold', '5000'))
        for rec in self:
            rec.requires_approval = (
                rec.total_amount > threshold and not rec.auto_approved
            )

    @api.depends('currency_detected')
    def _compute_currency(self):
        for rec in self:
            if rec.currency_detected:
                currency = self.env['res.currency'].search(
                    [('name', '=ilike', rec.currency_detected.strip())], limit=1
                )
                rec.currency_id = currency.id if currency else self.env.company.currency_id.id
            else:
                rec.currency_id = self.env.company.currency_id.id

    @api.depends('tax_ids')
    def _compute_tax_applicability(self):
        for rec in self:
            count = len(rec.tax_ids)
            if count == 0:
                rec.tax_applicability = 'none'
            elif count == 1:
                rec.tax_applicability = 'single'
            else:
                rec.tax_applicability = 'multiple'

    def _compute_log_counts(self):
        QuotaLog = self.env['document.intelligence.quota.log']
        ErrorLog = self.env['document.intelligence.error.log']
        for rec in self:
            rec.quota_log_count = QuotaLog.search_count([('document_id', '=', rec.id)])
            rec.error_log_count = ErrorLog.search_count([('document_id', '=', rec.id)])

    def action_view_quota_logs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'document.intelligence.quota.log',
            'view_mode': 'tree,form',
            'domain': [('document_id', '=', self.id)],
            'name': _('API Calls for %s') % self.name,
        }

    def action_view_error_logs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'document.intelligence.error.log',
            'view_mode': 'tree,form',
            'domain': [('document_id', '=', self.id)],
            'name': _('Errors for %s') % self.name,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Actions
    # ─────────────────────────────────────────────────────────────────────────

    def action_extract(self):
        self.ensure_one()
        if self.input_mode == 'upload' and not self.file_data:
            raise UserError(_('Please upload a document before extracting.'))
        if self.input_mode == 'existing' and not self.source_attachment_id:
            raise UserError(_('Please select an existing Odoo document.'))

        self.write({'state': 'processing', 'processing_notes': False})
        self._cr.commit()

        try:
            from ..services.document_processor import DocumentProcessor
            DocumentProcessor(self).run()
        except Exception as e:
            _logger.exception('Extraction failed for record %s', self.id)
            self.write({'state': 'error', 'processing_notes': str(e)})
            raise UserError(_('Extraction failed: %s') % str(e))

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'document.intelligence.review.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_record_id': self.id},
        }

    def action_extract_batch(self):
        """Extract all selected records that are in Draft state."""
        draft_records = self.filtered(lambda r: r.state == 'draft')
        if not draft_records:
            raise UserError(_('No draft documents selected for extraction.'))

        errors = []
        for rec in draft_records:
            try:
                rec.write({'state': 'processing'})
                self._cr.commit()
                from ..services.document_processor import DocumentProcessor
                DocumentProcessor(rec).run()
            except Exception as e:
                _logger.exception('Batch extraction failed for record %s', rec.id)
                rec.write({'state': 'error', 'processing_notes': str(e)})
                errors.append(f'{rec.name}: {e}')

        if errors:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Batch Extraction Completed with Errors'),
                    'message': '\n'.join(errors),
                    'type': 'warning',
                },
            }
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Batch Extraction Complete'),
                'message': _('%d document(s) processed successfully.') % len(draft_records),
                'type': 'success',
            },
        }

    def action_open_review(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'document.intelligence.review.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_record_id': self.id},
        }

    def action_create_odoo_record(self):
        self.ensure_one()
        action = self.suggested_action
        if action == 'create_invoice':
            return self._create_vendor_bill()
        elif action == 'update_invoice':
            return self._update_linked_invoice()
        elif action == 'create_partner':
            return self._create_partner()
        elif action == 'create_hr_applicant':
            return self._create_hr_applicant()
        elif action == 'create_expense_claim':
            return self.action_create_expense_claim()
        else:
            self.write({'state': 'done'})
            return self._notify(_('Document stored successfully.'))

    def action_reset_to_draft(self):
        self.write({
            'state': 'draft',
            'extracted_data_json': False,
            'detected_document_type': False,
            'suggested_action': False,
            'confidence_score': 0.0,
            'processing_notes': False,
            'force_re_ocr': False,
        })

    # ─────────────────────────────────────────────────────────────────────────
    # Odoo record creation helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _create_vendor_bill(self):
        partner = self._get_or_create_partner()
        move_vals = {
            'move_type': 'in_invoice',
            'partner_id': partner.id if partner else False,
            'invoice_date': self.document_date,
            'ref': self.reference_number,
            'narration': _('Created by Document Intelligence — %s') % self.name,
        }
        if self.total_amount:
            move_vals['invoice_line_ids'] = [(0, 0, {
                'name': _('Extracted from document'),
                'price_unit': self.total_amount,
                'quantity': 1,
            })]
        move = self.env['account.move'].create(move_vals)
        self.write({'state': 'done', 'created_move_id': move.id})
        return {'type': 'ir.actions.act_window', 'res_model': 'account.move',
                'res_id': move.id, 'view_mode': 'form'}

    def _update_linked_invoice(self):
        move = self.linked_move_id
        if not move:
            return self._create_vendor_bill()
        update_vals = {}
        if not move.invoice_date and self.document_date:
            update_vals['invoice_date'] = self.document_date
        if not move.ref and self.reference_number:
            update_vals['ref'] = self.reference_number
        if not move.partner_id and self.partner_id:
            update_vals['partner_id'] = self.partner_id.id
        if update_vals:
            move.write(update_vals)
        self.write({'state': 'done'})
        return {'type': 'ir.actions.act_window', 'res_model': 'account.move',
                'res_id': move.id, 'view_mode': 'form'}

    def _create_partner(self):
        partner = self.env['res.partner'].create({
            'name': self.contact_name or self.vendor_name or _('Unknown'),
            'phone': self.contact_phone,
            'email': self.contact_email,
            'street': self.contact_address,
        })
        self.write({'state': 'done', 'created_partner_id': partner.id})
        return {'type': 'ir.actions.act_window', 'res_model': 'res.partner',
                'res_id': partner.id, 'view_mode': 'form'}

    def _create_hr_applicant(self):
        if 'hr.applicant' not in self.env:
            self.write({'state': 'done'})
            return self._notify(_('HR Recruitment not installed. Document stored only.'))
        vals = {
            'partner_name': self.contact_name or self.vendor_name or _('Unknown'),
            'partner_phone': self.contact_phone,
            'description': self.raw_text or '',
        }
        if self.linked_applicant_id:
            self.linked_applicant_id.write(vals)
            self.write({'state': 'done'})
            return {'type': 'ir.actions.act_window', 'res_model': 'hr.applicant',
                    'res_id': self.linked_applicant_id.id, 'view_mode': 'form'}
        applicant = self.env['hr.applicant'].create(vals)
        self.write({'state': 'done'})
        return {'type': 'ir.actions.act_window', 'res_model': 'hr.applicant',
                'res_id': applicant.id, 'view_mode': 'form'}

    def _get_or_create_partner(self):
        if self.partner_id:
            return self.partner_id
        name = self.vendor_name or self.contact_name
        if not name:
            return False
        # Try exact name first
        partner = self.env['res.partner'].search([
            ('name', '=ilike', name.strip()),
            '|', ('company_type', '=', 'company'), ('is_company', '=', True)
        ], limit=1)
        if not partner:
            # Fuzzy search by words
            words = name.strip().split()
            if len(words) >= 2:
                domain = ['|'] * (len(words) - 1)
                for word in words:
                    domain.append(('name', 'ilike', word))
                partner = self.env['res.partner'].search(domain + [
                    '|', ('company_type', '=', 'company'), ('is_company', '=', True)
                ], limit=1)
        if partner:
            return partner
        # Auto-create if configured
        ICP = self.env['ir.config_parameter'].sudo()
        if ICP.get_param('document_intelligence.auto_create_partners', 'False') == 'True':
            partner_vals = {
                'name': name,
                'is_company': True,
                'company_type': 'company',
                'phone': self.contact_phone,
                'email': self.contact_email,
                'vat': self.vendor_name and self.vendor_name.startswith('RW') and self.vendor_name.split()[-1] or False,
            }
            if self.contact_address:
                lines = self.contact_address.split('\n')
                partner_vals['street'] = lines[0] if lines else ''
                if len(lines) > 1:
                    partner_vals['street2'] = lines[1]
                if len(lines) > 2:
                    partner_vals['city'] = lines[2]
            partner = self.env['res.partner'].create(partner_vals)
            self.env['document.intelligence.validation'].create({
                'document_id': self.id,
                'rule_code': 'partner_auto_created',
                'severity': 'info',
                'message': _('Vendor "%s" was auto-created. Please verify details.') % name,
            })
            return partner
        return False

    # ── RECEIPT → EXPENSE CLAIM ───────────────────────────────────────────────────

    def action_create_expense_claim(self):
        """Create an expense claim from a receipt document."""
        if not self.is_receipt:
            raise UserError(_('This document is not detected as a receipt.'))
        if 'hr.expense' not in self.env:
            raise UserError(_('HR Expenses module is not installed.'))
        employee = self.expense_employee_id
        if not employee:
            if self.partner_id and self.partner_id.employee_ids:
                employee = self.partner_id.employee_ids[0]
            else:
                employee = self.env.user.employee_id
        if not employee:
            raise UserError(_('No employee identified for this expense. Please specify an employee.'))
        expense_vals = {
            'name': self.vendor_name or _('Receipt'),
            'employee_id': employee.id,
            'date': self.document_date or fields.Date.context_today(self),
            'total_amount': self.total_amount,
            'currency_id': self.currency_id.id if self.currency_id else self.env.company.currency_id.id,
            'payment_mode': 'own_account',
            'state': 'draft',
        }
        product_categ = self.env.ref('hr_expense.product_product_no_product', raise_if_not_found=False)
        expense_line_vals = []
        if self.line_item_ids:
            for line in self.line_item_ids:
                expense_line_vals.append((0, 0, {
                    'name': line.description,
                    'date': self.document_date or fields.Date.context_today(self),
                    'total_amount': line.price_subtotal,
                    'product_id': line.product_id.id if line.product_id else (product_categ.id if product_categ else False),
                    'quantity': line.quantity,
                    'unit_amount': line.unit_price,
                }))
        else:
            product = self.env['product.product'].search([('name', 'ilike', 'Miscellaneous Expense'), ('type', '=', 'service')], limit=1)
            expense_line_vals.append((0, 0, {
                'name': _('Expense from receipt: %s') % (self.name),
                'date': expense_vals['date'],
                'total_amount': self.total_amount,
                'product_id': product.id if product else False,
                'quantity': 1,
                'unit_amount': self.total_amount,
            }))
        expense_vals['expense_line_ids'] = expense_line_vals
        expense = self.env['hr.expense'].create(expense_vals)
        self.write({'state': 'done', 'expense_claim_id': expense.id, 'suggested_action': 'store_only'})
        self.env['document.intelligence.validation'].create({
            'document_id': self.id,
            'rule_code': 'expense_created',
            'severity': 'info',
            'message': _('Expense claim %s created from receipt.') % expense.name,
        })
        return {'type': 'ir.actions.act_window', 'res_model': 'hr.expense', 'res_id': expense.id, 'view_mode': 'form'}

    # ── BANK RECONCILIATION ────────────────────────────────────────────────────────

    def action_match_bank_statement(self):
        if not self.partner_id:
            raise UserError(_('A vendor/partner must be identified first.'))
        if not self.total_amount:
            raise UserError(_('Total amount must be extracted before matching.'))
        StatementLine = self.env['account.bank.statement.line']
        domain = [
            ('partner_id', '=', self.partner_id.id),
            ('amount', '=', -self.total_amount),
            ('statement_id.state', 'in', ['open', 'confirm']),
        ]
        if self.document_date:
            domain.append(('date', '=', self.document_date))
        if self.reference_number:
            domain.append(('ref', '=', self.reference_number))
        matching_lines = StatementLine.search(domain, limit=5)
        if not matching_lines:
            raise UserError(_('No matching bank statement line found. Check that the statement is in Open/Confirm state and amount/date match.'))
        if len(matching_lines) == 1:
            self.bank_statement_line_id = matching_lines.id
            try:
                matching_lines.write({'di_document_id': self.id})
            except Exception:
                pass
            self.message_post(body=_('Matched with bank statement line %s') % matching_lines.name)
            return {'type': 'ir.actions.act_window', 'res_model': 'account.bank.statement.line', 'res_id': matching_lines.id, 'view_mode': 'form'}
        else:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'document.intelligence.bank.match.wizard',
                'view_mode': 'form',
                'target': 'new',
                'context': {
                    'default_document_id': self.id,
                    'candidate_line_ids': matching_lines.ids,
                },
            }

    def action_apply_suggested_action(self, action):
        self.ensure_one()
        if action == 'create_expense':
            return self.action_create_expense_claim()
        elif action == 'match_bank':
            return self.action_match_bank_statement()
        else:
            return self.action_create_odoo_record()

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers for DocumentProcessor
    # ─────────────────────────────────────────────────────────────────────────

    def get_file_data_and_name(self):
        self.ensure_one()
        if self.input_mode == 'existing' and self.source_attachment_id:
            att = self.source_attachment_id
            return att.datas, att.name
        return self.file_data, self.file_name or 'document'

    def get_effective_ocr_language(self):
        """Per-document language, falling back to system config."""
        self.ensure_one()
        if self.ocr_language and self.ocr_language.strip():
            return self.ocr_language.strip()
        ICP = self.env['ir.config_parameter'].sudo()
        return (ICP.get_param('document_intelligence.tesseract_lang', 'eng') or 'eng').strip()

    # AI → model action key normaliser
    _ACTION_ALIASES = {
        'create_contact': 'create_partner',
        'create_applicant': 'create_hr_applicant',
        'review': 'store_only',
        'none': 'store_only',
    }

    def populate_from_extracted(self, data: dict, raw_text: str, confidence: float, notes: str = ''):
        # Keys consumed by this method — everything else → extra_fields_display
        STANDARD_KEYS = {
            'document_type', 'suggested_action', 'confidence', 'notes',
            # vendor
            'vendor_name', 'vendor', 'sender',
            # reference
            'reference_number', 'reference', 'invoice_number',
            # date
            'document_date', 'date',
            # amounts
            'total_amount', 'total', 'tax_amount', 'tax', 'tax_rate',
            # currency
            'currency',
            # contact
            'contact_name', 'contact_phone', 'phone', 'contact_email', 'email',
            'contact_address', 'address',
            # banking
            'vat_number', 'iban', 'swift', 'bank_ref',
            # line items (handled separately)
            'line_items',
        }
        extra = {k: v for k, v in data.items()
                 if k not in STANDARD_KEYS and not isinstance(v, (list, dict))}

        # ── Document type ──────────────────────────────────────────────────────
        doc_type = (data.get('document_type') or 'general').lower().strip()
        valid_types = dict(self._fields['detected_document_type'].selection)
        if doc_type not in valid_types:
            doc_type = 'general'

        # ── Suggested action ──────────────────────────────────────────────────
        suggested = data.get('suggested_action') or self._infer_action(doc_type)
        suggested = self._ACTION_ALIASES.get(suggested, suggested)
        if self.linked_move_id and doc_type in ('invoice', 'proforma', 'receipt'):
            suggested = 'update_invoice'
        if self.linked_applicant_id and doc_type == 'cv':
            suggested = 'create_hr_applicant'
        valid_actions = dict(self._fields['suggested_action'].selection)
        if suggested not in valid_actions:
            suggested = self._infer_action(doc_type)

        # ── Field extraction (accept both old short keys and new full keys) ───
        vendor  = (data.get('vendor_name') or data.get('vendor') or data.get('sender') or '').strip()
        ref     = (data.get('reference_number') or data.get('reference') or data.get('invoice_number') or '').strip()
        phone   = (data.get('contact_phone') or data.get('phone') or '').strip()
        email   = (data.get('contact_email') or data.get('email') or '').strip()
        address = (data.get('contact_address') or data.get('address') or '').strip()
        date_val = self._parse_date(data.get('document_date') or data.get('date') or '')
        total   = self._to_float(data.get('total_amount') if data.get('total_amount') is not None else data.get('total', 0))
        tax     = self._to_float(data.get('tax_amount') if data.get('tax_amount') is not None else data.get('tax', 0))
        extracted_currency = (data.get('currency') or '').strip().upper()
        currency = extracted_currency or self.env.company.currency_id.name or 'USD'

        # ── Auto-generate document name (only if still the default placeholder) ─
        try:
            auto_name = self._build_document_name(doc_type, vendor, ref, date_val)
        except Exception:
            _logger.exception('_build_document_name failed — will skip auto-naming')
            auto_name = vendor or False

        vals = {
            'state': 'review',
            'raw_text': raw_text,
            'extracted_data_json': json.dumps(data, indent=2, ensure_ascii=False),
            'confidence_score': confidence,
            'processing_notes': notes,
            'detected_document_type': doc_type,
            'suggested_action': suggested,
            'vendor_name': vendor,
            'document_date': date_val,
            'total_amount': total,
            'currency_detected': currency,
            'tax_amount': tax,
            'reference_number': ref,
            'contact_name': data.get('contact_name', ''),
            'contact_phone': phone,
            'contact_email': email,
            'contact_address': address,
            'vat_number': data.get('vat_number', ''),
            'iban': data.get('iban', ''),
            'swift': data.get('swift', ''),
            'extra_fields_display': json.dumps(extra, indent=2, ensure_ascii=False) if extra else False,
        }
        # Only replace the name when the user hasn't typed one yet
        _default_names = {_('New Document'), 'New Document', 'new document', '', False, None}
        current_name = self.name or ''
        is_default = (
            current_name in _default_names
            or current_name.lower() == 'new document'
            or current_name == (self.file_name or '')
        )
        _logger.info(
            'Doc %s auto-naming: current=%r, auto_name=%r, vendor=%r, is_default=%s',
            self.id, current_name, auto_name, vendor, is_default,
        )
        if is_default and auto_name:
            vals['name'] = auto_name

        self.write(vals)

        # ── Auto-match partner ────────────────────────────────────────────────
        if not self.partner_id and vendor:
            partner = self.env['res.partner'].search([('name', 'ilike', vendor)], limit=1)
            if partner:
                self.partner_id = partner

        # ── Create line items ─────────────────────────────────────────────────
        line_items = data.get('line_items') or []
        if line_items and 'document.intelligence.line.item' in self.env:
            self.line_item_ids.unlink()
            LineItem = self.env['document.intelligence.line.item']
            for item in line_items:
                if not isinstance(item, dict):
                    continue
                LineItem.create({
                    'document_id': self.id,
                    'description': item.get('description') or item.get('name', ''),
                    'quantity': self._to_float(item.get('quantity', 1)),
                    'unit_price': self._to_float(item.get('unit_price', 0)),
                })

    def _build_document_name(self, doc_type: str, vendor: str, ref: str, date_val) -> str:
        """Return the vendor/company name as the document name, falling back to the document type."""
        if vendor:
            return vendor
        # No vendor extracted — use the document type label as a minimal fallback
        return _DOC_TYPE_LABELS.get(doc_type, 'Document')

    @staticmethod
    def _parse_date(raw_date):
        if not raw_date:
            return False
        from datetime import datetime
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%B %d, %Y', '%d %B %Y', '%m/%d/%Y'):
            try:
                return datetime.strptime(str(raw_date).strip(), fmt).date()
            except (ValueError, TypeError):
                continue
        return False

    @staticmethod
    def _to_float(val):
        if val is None or val == '':
            return 0.0
        try:
            return float(str(val).replace(',', '').replace(' ', '').strip())
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _infer_action(doc_type):
        return {
            'invoice': 'create_invoice',
            'proforma': 'create_invoice',
            'receipt': 'create_expense_claim',
            'cv': 'create_hr_applicant',
            'contract': 'store_only',
            'form': 'create_partner',
            'general': 'store_only',
        }.get(doc_type, 'store_only')

    def _notify(self, message, msg_type='info'):
        """Return a display_notification action with the given message."""
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Document Intelligence'),
                'message': message,
                'type': msg_type,
                'sticky': False,
            },
        }