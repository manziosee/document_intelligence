import json
import logging
import base64

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DocumentRecord(models.Model):
    _name = 'document.intelligence.record'
    _description = 'Document Intelligence Record'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    # ── Basic info ────────────────────────────────────────────────────────────

    name = fields.Char(
        string='Document Name',
        required=True,
        default=lambda self: _('New Document'),
        tracking=True,
    )
    state = fields.Selection([
        ('draft', 'Draft'),
        ('processing', 'Processing'),
        ('review', 'Under Review'),
        ('done', 'Done'),
        ('error', 'Error'),
    ], default='draft', string='Status', tracking=True, readonly=True)

    # ── Input mode: upload new OR pick from Odoo ──────────────────────────────

    input_mode = fields.Selection([
        ('upload', 'Upload New File'),
        ('existing', 'Select from Odoo'),
    ], default='upload', string='Document Source', required=True)

    # ── Option A: upload a new file ───────────────────────────────────────────

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

    # ── Option B: pick an existing Odoo attachment ────────────────────────────

    source_attachment_id = fields.Many2one(
        'ir.attachment',
        string='Existing Odoo Document',
        domain=[('type', '=', 'binary')],
        help='Select a file already attached to any Odoo record (invoice, HR applicant, etc.)',
    )

    # Where the attachment came from (populated automatically by server actions)
    source_model = fields.Char(string='Source Model', readonly=True)
    source_res_id = fields.Integer(string='Source Record ID', readonly=True)
    source_record_display = fields.Char(
        string='Source Record',
        compute='_compute_source_record_display',
        store=True,
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

    template_id = fields.Many2one(
        'document.intelligence.template',
        string='Extraction Template',
    )
    custom_fields_input = fields.Char(
        string='Fields to Extract',
        help='Comma-separated list of fields. Example: vendor, date, total, phone, address',
    )
    extra_prompt = fields.Text(
        string='AI Instructions',
        help='Additional context for the AI. Example: "Amounts are in RWF. Vendor is a local supplier."',
    )

    # ── Raw text (post-OCR) ───────────────────────────────────────────────────

    raw_text = fields.Text(string='Raw Extracted Text', readonly=True)

    # ── AI output ────────────────────────────────────────────────────────────

    detected_document_type = fields.Selection([
        ('invoice', 'Invoice'),
        ('receipt', 'Receipt'),
        ('proforma', 'Proforma Invoice'),
        ('contract', 'Contract'),
        ('cv', 'CV / Resume'),
        ('form', 'Form'),
        ('general', 'General Document'),
    ], string='Detected Type', readonly=True, tracking=True)

    extracted_data_json = fields.Text(string='Extracted JSON (raw)', readonly=True)
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
    extra_fields_display = fields.Text(
        string='Other Extracted Fields',
        readonly=True,
    )

    # ── Odoo matching / suggested action ─────────────────────────────────────

    suggested_action = fields.Selection([
        ('create_invoice', 'Create Vendor Bill'),
        ('update_invoice', 'Update Existing Invoice'),
        ('create_partner', 'Create / Update Contact'),
        ('create_hr_applicant', 'Create HR Applicant'),
        ('store_only', 'Store Document Only'),
    ], string='Suggested Action', readonly=True, tracking=True)

    partner_id = fields.Many2one('res.partner', string='Matched Partner')

    # ── Created / linked records ──────────────────────────────────────────────

    created_move_id = fields.Many2one('account.move', string='Created Bill', readonly=True)
    created_partner_id = fields.Many2one('res.partner', string='Created Contact', readonly=True)

    # ── Linked source Odoo record (for smart-button back-navigation) ──────────

    linked_move_id = fields.Many2one(
        'account.move', string='Linked Invoice / Proforma', readonly=True,
    )
    linked_applicant_id = fields.Many2one(
        'hr.applicant', string='Linked HR Applicant', readonly=True,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Actions
    # ─────────────────────────────────────────────────────────────────────────

    def action_extract(self):
        """Validate input → run OCR+AI pipeline → open review wizard."""
        self.ensure_one()

        if self.input_mode == 'upload' and not self.file_data:
            raise UserError(_('Please upload a document before extracting.'))
        if self.input_mode == 'existing' and not self.source_attachment_id:
            raise UserError(_('Please select an existing Odoo document before extracting.'))

        self.write({'state': 'processing', 'processing_notes': False})
        self._cr.commit()

        try:
            from ..services.document_processor import DocumentProcessor
            processor = DocumentProcessor(self)
            processor.run()
        except Exception as e:
            _logger.exception('Document extraction failed for record %s', self.id)
            self.write({'state': 'error', 'processing_notes': str(e)})
            raise UserError(_('Extraction failed: %s') % str(e))

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'document.intelligence.review.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_record_id': self.id},
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
        else:
            self.write({'state': 'done'})
            return self._notify(_('Document stored successfully.'))

    def action_reset_to_draft(self):
        self.write({
            'state': 'draft',
            'raw_text': False,
            'extracted_data_json': False,
            'detected_document_type': False,
            'suggested_action': False,
            'confidence_score': 0.0,
            'processing_notes': False,
        })

    # ─────────────────────────────────────────────────────────────────────────
    # Odoo record creation / update helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _create_vendor_bill(self):
        partner = self._get_or_create_partner()
        move_vals = {
            'move_type': 'in_invoice',
            'partner_id': partner.id if partner else False,
            'invoice_date': self.document_date,
            'ref': self.reference_number,
            'narration': _(
                'Created automatically by Document Intelligence\nDocument: %s'
            ) % self.name,
        }
        if self.total_amount:
            move_vals['invoice_line_ids'] = [(0, 0, {
                'name': _('Extracted from document'),
                'price_unit': self.total_amount,
                'quantity': 1,
            })]

        move = self.env['account.move'].create(move_vals)
        self.write({'state': 'done', 'created_move_id': move.id})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': move.id,
            'view_mode': 'form',
        }

    def _update_linked_invoice(self):
        """Fill missing fields on the Odoo invoice that triggered extraction."""
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
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': move.id,
            'view_mode': 'form',
        }

    def _create_partner(self):
        partner_vals = {
            'name': self.contact_name or self.vendor_name or _('Unknown'),
            'phone': self.contact_phone,
            'email': self.contact_email,
            'street': self.contact_address,
        }
        partner = self.env['res.partner'].create(partner_vals)
        self.write({'state': 'done', 'created_partner_id': partner.id})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'res.partner',
            'res_id': partner.id,
            'view_mode': 'form',
        }

    def _create_hr_applicant(self):
        if 'hr.applicant' not in self.env:
            self.write({'state': 'done'})
            return self._notify(_('HR Recruitment module is not installed. Document stored only.'))

        vals = {
            'partner_name': self.contact_name or self.vendor_name or _('Unknown'),
            'partner_phone': self.contact_phone,
            'description': self.raw_text or '',
        }
        if self.linked_applicant_id:
            # Update existing applicant instead of creating a new one
            self.linked_applicant_id.write(vals)
            self.write({'state': 'done'})
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'hr.applicant',
                'res_id': self.linked_applicant_id.id,
                'view_mode': 'form',
            }

        applicant = self.env['hr.applicant'].create(vals)
        self.write({'state': 'done'})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'hr.applicant',
            'res_id': applicant.id,
            'view_mode': 'form',
        }

    def _get_or_create_partner(self):
        if self.partner_id:
            return self.partner_id
        name = self.vendor_name or self.contact_name
        if not name:
            return False
        return self.env['res.partner'].search([('name', 'ilike', name)], limit=1) or False

    def _notify(self, message):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Document Intelligence'),
                'message': message,
                'type': 'success',
            },
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers used by DocumentProcessor
    # ─────────────────────────────────────────────────────────────────────────

    def get_file_data_and_name(self):
        """Return (base64_str, filename) regardless of input mode."""
        self.ensure_one()
        if self.input_mode == 'existing' and self.source_attachment_id:
            att = self.source_attachment_id
            return att.datas, att.name
        return self.file_data, self.file_name or 'document'

    def populate_from_extracted(self, data: dict, raw_text: str, confidence: float, notes: str = ''):
        """Called by DocumentProcessor after AI extraction."""
        STANDARD_KEYS = {
            'vendor', 'date', 'total', 'currency', 'tax',
            'reference', 'invoice_number', 'contact_name',
            'phone', 'email', 'address', 'document_type', 'suggested_action',
        }
        extra = {k: v for k, v in data.items() if k not in STANDARD_KEYS}

        doc_type = data.get('document_type', 'general')
        valid_types = dict(self._fields['detected_document_type'].selection)
        if doc_type not in valid_types:
            doc_type = 'general'

        # If the record came from an existing invoice/proforma, suggest updating it
        suggested = data.get('suggested_action', self._infer_action(doc_type))
        if self.linked_move_id and doc_type in ('invoice', 'proforma', 'receipt'):
            suggested = 'update_invoice'
        if self.linked_applicant_id and doc_type == 'cv':
            suggested = 'create_hr_applicant'

        valid_actions = dict(self._fields['suggested_action'].selection)
        if suggested not in valid_actions:
            suggested = 'store_only'

        date_val = self._parse_date(data.get('date', ''))

        self.write({
            'state': 'review',
            'raw_text': raw_text,
            'extracted_data_json': json.dumps(data, indent=2, ensure_ascii=False),
            'confidence_score': confidence,
            'processing_notes': notes,
            'detected_document_type': doc_type,
            'suggested_action': suggested,
            'vendor_name': data.get('vendor') or data.get('sender', ''),
            'document_date': date_val,
            'total_amount': self._to_float(data.get('total', 0)),
            'currency_detected': data.get('currency', ''),
            'tax_amount': self._to_float(data.get('tax', 0)),
            'reference_number': data.get('reference') or data.get('invoice_number', ''),
            'contact_name': data.get('contact_name', ''),
            'contact_phone': data.get('phone', ''),
            'contact_email': data.get('email', ''),
            'contact_address': data.get('address', ''),
            'extra_fields_display': json.dumps(extra, indent=2, ensure_ascii=False) if extra else False,
        })

        # Auto-match partner
        if not self.partner_id:
            name = data.get('vendor') or data.get('sender') or data.get('contact_name')
            if name:
                partner = self.env['res.partner'].search([('name', 'ilike', name)], limit=1)
                if partner:
                    self.partner_id = partner

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
            'receipt': 'create_invoice',
            'cv': 'create_hr_applicant',
            'contract': 'store_only',
            'form': 'create_partner',
            'general': 'store_only',
        }.get(doc_type, 'store_only')
