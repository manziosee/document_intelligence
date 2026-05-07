import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DocumentLineItem(models.Model):
    _name = 'document.intelligence.line.item'
    _description = 'Document Intelligence Line Item'
    _order = 'sequence, id'

    document_id = fields.Many2one(
        'document.intelligence.record', string='Document',
        required=True, ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    description = fields.Char(string='Description', required=True)
    quantity = fields.Float(string='Quantity', default=1.0, digits='Product Unit of Measure')
    unit_price = fields.Float(string='Unit Price', digits='Product Price')
    price_subtotal = fields.Float(
        string='Subtotal', compute='_compute_subtotal', store=True,
    )
    product_id = fields.Many2one('product.product', string='Matched Product')
    tax_ids = fields.Many2many('account.tax', 'di_line_item_tax_rel', string='Taxes')
    confidence = fields.Float(string='Confidence %', default=0.0)
    needs_review = fields.Boolean(string='Needs Review', default=False)

    @api.depends('quantity', 'unit_price')
    def _compute_subtotal(self):
        for line in self:
            line.price_subtotal = line.quantity * line.unit_price


class DocumentValidationResult(models.Model):
    _name = 'document.intelligence.validation'
    _description = 'Document Validation Result'
    _order = 'severity, id'

    document_id = fields.Many2one(
        'document.intelligence.record', string='Document',
        required=True, ondelete='cascade',
    )
    rule_code = fields.Char(string='Rule Code', required=True)
    severity = fields.Selection([
        ('info', 'Info'),
        ('warning', 'Warning'),
        ('error', 'Error'),
    ], string='Severity', required=True, default='info')
    message = fields.Text(string='Message', required=True)
    field_name = fields.Char(string='Field')
    resolved = fields.Boolean(string='Resolved', default=False)


class DocumentDuplicateCheck(models.Model):
    _name = 'document.intelligence.duplicate.check'
    _description = 'Document Duplicate Check'
    _order = 'create_date desc'

    document_id = fields.Many2one(
        'document.intelligence.record', string='Document',
        required=True, ondelete='cascade',
    )
    duplicate_of_id = fields.Many2one(
        'document.intelligence.record', string='Possible Duplicate Of',
        ondelete='set null',
    )
    match_type = fields.Selection([
        ('hash', 'File Hash Match'),
        ('reference', 'Invoice Reference Match'),
        ('amount_date', 'Amount + Date Match'),
    ], string='Match Type')
    confidence = fields.Float(string='Confidence %', default=0.0)
    resolved = fields.Boolean(string='Resolved', default=False)
    resolution = fields.Selection([
        ('confirmed_duplicate', 'Confirmed Duplicate'),
        ('false_positive', 'False Positive'),
    ], string='Resolution')


class VendorCorrectionPattern(models.Model):
    _name = 'document.intelligence.vendor.correction'
    _description = 'Vendor Correction Pattern'
    _order = 'create_date desc'

    document_id = fields.Many2one(
        'document.intelligence.record', string='Document',
        required=True, ondelete='cascade',
    )
    partner_id = fields.Many2one('res.partner', string='Vendor', required=True)
    field_name = fields.Char(string='Corrected Field', required=True)
    original_value = fields.Char(string='AI Value')
    corrected_value = fields.Char(string='Corrected Value')
    user_id = fields.Many2one('res.users', string='Corrected By', default=lambda self: self.env.user)
    applied_to_pattern = fields.Boolean(string='Applied to Pattern', default=False)


class AutoApprovalRule(models.Model):
    _name = 'document.intelligence.auto.approval.rule'
    _description = 'Auto Approval Rule'
    _order = 'sequence, name'

    name = fields.Char(string='Rule Name', required=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)

    document_type = fields.Selection([
        ('invoice', 'Invoice'),
        ('receipt', 'Receipt'),
        ('proforma', 'Proforma Invoice'),
        ('any', 'Any Document Type'),
    ], string='Document Type', default='any')

    min_confidence = fields.Float(string='Min Confidence %', default=95.0)
    max_amount = fields.Float(string='Max Amount', default=10000.0)

    partner_ids = fields.Many2many(
        'res.partner',
        'di_auto_approval_partner_rel',
        string='Trusted Vendors',
        help='Leave empty to apply to all vendors.',
    )

    action = fields.Selection([
        ('auto_create', 'Auto Create Record'),
        ('auto_post', 'Auto Create & Post'),
        ('notify', 'Notify & Wait'),
    ], string='Action on Match', default='auto_create')

    usage_count = fields.Integer(string='Times Applied', default=0, readonly=True)

    def check_applies(self, document):
        """Return True if this rule applies to the given document record."""
        self.ensure_one()
        if self.document_type != 'any' and document.detected_document_type != self.document_type:
            return False
        if document.confidence_score < self.min_confidence:
            return False
        if self.max_amount and document.total_amount > self.max_amount:
            return False
        if self.partner_ids and document.partner_id not in self.partner_ids:
            return False
        return True


class ProductMatchWizard(models.TransientModel):
    _name = 'document.intelligence.product.match.wizard'
    _description = 'Product Match Wizard'

    line_item_id = fields.Many2one(
        'document.intelligence.line.item', string='Line Item', required=True,
    )
    description = fields.Char(string='Description', readonly=True)
    candidate_product_ids = fields.Many2many('product.product', 'di_product_match_candidate_rel', string='Candidate Products')
    selected_product_id = fields.Many2one(
        'product.product', string='Select Product', required=True,
    )
    create_new = fields.Boolean(string='Create New Product', default=False)
    new_product_name = fields.Char(string='New Product Name')

    def action_apply(self):
        self.ensure_one()
        if self.create_new and self.new_product_name:
            product = self.env['product.product'].create({
                'name': self.new_product_name,
                'type': 'service',
            })
            self.line_item_id.product_id = product.id
        elif self.selected_product_id:
            self.line_item_id.product_id = self.selected_product_id.id
        return {'type': 'ir.actions.act_window_close'}
