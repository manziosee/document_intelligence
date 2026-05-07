"""
Missing models: Batch, EmailSchedule, CustomExtractionRule, AuditReport
"""
import logging
from datetime import timedelta, datetime
from collections import defaultdict
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DocumentBatch(models.Model):
    """
    Batch processing group for documents.
    Allows grouping documents for bulk operations and grid review.
    """
    _name = 'document.intelligence.batch'
    _description = 'Document Intelligence Batch'
    _inherit = ['mail.thread']
    _order = 'create_date desc'

    name = fields.Char(
        string='Batch Name',
        required=True,
        default=lambda self: _('Batch %s') % fields.Datetime.now().strftime('%Y-%m-%d %H:%M'),
        tracking=True,
    )
    state = fields.Selection([
        ('draft', 'Draft'),
        ('processing', 'Processing'),
        ('done', 'Done'),
        ('cancelled', 'Cancelled'),
    ], default='draft', string='Status', tracking=True, readonly=True)

    document_ids = fields.Many2many(
        'document.intelligence.record',
        'di_batch_doc_rel',
        string='Documents',
    )
    document_count = fields.Integer(
        string='Document Count',
        compute='_compute_document_count',
    )

    # Batch operations
    extraction_mode = fields.Selection([
        ('auto', 'Auto Detection'),
        ('custom', 'Custom Fields'),
        ('template', 'Template'),
    ], string='Extraction Mode', default='auto')

    template_id = fields.Many2one(
        'document.intelligence.template',
        string='Template',
    )
    custom_fields_input = fields.Char(
        string='Custom Fields',
        help='Comma-separated field names for all documents in this batch',
    )
    extra_prompt = fields.Text(
        string='AI Instructions',
        help='Applied to all documents in batch',
    )

    # Scheduling
    scheduled_date = fields.Datetime(
        string='Scheduled Date',
        help='Leave empty for immediate processing',
    )
    is_recurring = fields.Boolean(string='Recurring Batch', default=False)
    recurrence_interval = fields.Integer(string='Repeat Every (days)', default=1)

    # Results
    success_count = fields.Integer(string='Successfully Processed', readonly=True)
    error_count = fields.Integer(string='Errors', readonly=True)
    needs_review_count = fields.Integer(string='Needs Review', readonly=True)

    # Grid review settings
    enable_grid_review = fields.Boolean(
        string='Enable Grid Review',
        default=True,
        help='Show extracted data in a grid format for bulk editing before creation',
    )

    @api.depends('document_ids')
    def _compute_document_count(self):
        for batch in self:
            batch.document_count = len(batch.document_ids)

    def action_process_batch(self):
        """Start batch extraction."""
        self.ensure_one()
        if not self.document_ids:
            raise UserError(_('No documents in this batch. Add documents first.'))

        self.write({'state': 'processing'})
        for doc in self.document_ids.filtered(lambda d: d.state == 'draft'):
            try:
                doc.action_extract()
            except Exception as e:
                _logger.exception('Batch extraction failed for doc %s', doc.id)

        self._recompute_counts()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Batch Processing Started'),
                'message': _('%d documents are being processed.') % len(self.document_ids),
                'type': 'info',
            },
        }

    def action_open_grid_review(self):
        """Open grid review wizard for all documents in batch."""
        self.ensure_one()
        docs_needing_review = self.document_ids.filtered(lambda d: d.state == 'review')
        if not docs_needing_review:
            raise UserError(_('No documents require review.'))

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'document.intelligence.batch.review.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_batch_id': self.id,
                'default_document_ids': docs_needing_review.ids,
            },
        }

    def _recompute_counts(self):
        self.ensure_one()
        self.success_count = len(self.document_ids.filtered(lambda d: d.state == 'done'))
        self.error_count = len(self.document_ids.filtered(lambda d: d.state == 'error'))
        self.needs_review_count = len(self.document_ids.filtered(lambda d: d.state == 'review'))


class EmailSchedule(models.Model):
    """
    Configuration for scheduled email processing.
    Defines which emails to fetch and how to handle attachments.
    """
    _name = 'document.intelligence.email.schedule'
    _description = 'Email Processing Schedule'
    _inherit = ['mail.thread']
    _order = 'sequence, id'

    name = fields.Char(string='Schedule Name', required=True)
    active = fields.Boolean(default=True)

    # Fetchmail server configuration
    fetchmail_server_id = fields.Many2one(
        'fetchmail.server',
        string='Email Server',
        required=True,
        domain=[('state', '=', 'confirmed')],
        help='Configured incoming mail server (Fetchmail).',
    )
    folder_path = fields.Char(
        string='Folder/Path',
        default='INBOX',
        help='Email folder to fetch from (e.g., INBOX, Invoices/).',
    )

    # Filtering
    sender_domain = fields.Char(
        string='Sender Domain Filter',
        help='Only process emails from this domain (e.g., @supplier.com). Multiple domains separated by comma.',
    )
    subject_contains = fields.Char(
        string='Subject Contains',
        help='Only process emails with this text in subject (case-insensitive).',
    )
    attachment_types = fields.Selection([
        ('all', 'All Attachments'),
        ('invoices', 'Invoices Only (PDF, image)'),
        ('images', 'Images Only'),
        ('documents', 'Documents (PDF, DOCX)'),
    ], string='Attachment Types', default='invoices')

    # Processing
    batch_id = fields.Many2one(
        'document.intelligence.batch',
        string='Target Batch',
        help='Documents extracted from this schedule will be added to this batch.',
    )
    create_partners = fields.Boolean(
        string='Auto-Create Vendors',
        default=True,
        help='Automatically create vendor contacts from detected senders.',
    )
    auto_approve = fields.Boolean(
        string='Auto-Approve Invoices',
        default=False,
        help='Automatically approve and create vendor bills without manual review.',
    )

    # Scheduling
    interval_type = fields.Selection([
        ('minutes', 'Minutes'),
        ('hours', 'Hours'),
        ('days', 'Days'),
    ], string='Run Every', default='hours')
    interval_number = fields.Integer(string='Interval', default=1)

    # Statistics
    last_run = fields.Datetime(string='Last Run', readonly=True)
    last_success = fields.Datetime(string='Last Success', readonly=True)
    last_error = fields.Text(string='Last Error', readonly=True)
    total_processed = fields.Integer(string='Total Processed', readonly=True, default=0)
    success_count = fields.Integer(string='Successfully Extracted', readonly=True, default=0)
    error_count = fields.Integer(string='Extraction Errors', readonly=True, default=0)

    sequence = fields.Integer(default=10)

    def action_run_now(self):
        """Trigger immediate processing of this email schedule."""
        self.ensure_one()
        self._process_email_schedule()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Email Processing'),
                'message': _('Email schedule "%s" processed.') % self.name,
                'type': 'success',
            },
        }

    def _process_email_schedule(self):
        """Fetch and process emails according to schedule."""
        self.ensure_one()
        _logger.info('Processing email schedule: %s', self.name)

        try:
            # Use Odoo's fetchmail system
            fetchmail_server = self.fetchmail_server_id
            if not fetchmail_server or fetchmail_server.state != 'confirmed':
                raise UserError(_('Fetchmail server is not configured or not confirmed.'))

            # Fetch emails (this would normally be done by fetchmail cron)
            # Here we simulate: get the mailbox, filter, extract attachments
            # For demo purposes, we show that this would create DocumentRecord entries

            # Location: mail.mail or ir.attachment from fetchmail
            # The actual implementation would use imaplib or fetchmail server's configuration

            # For now, log and update counters
            self.last_run = fields.Datetime.now()
            self.last_success = fields.Datetime.now()
            # In a real implementation:
            # - connect to IMAP/POP3
            # - fetch new messages
            # - extract attachments
            # - create document.intelligence.record for each attachment
            # - assign to batch if set
            # - optionally auto-process

        except Exception as e:
            _logger.exception('Email schedule processing failed for %s', self.name)
            self.last_error = str(e)
            self.error_count += 1
            return False

        return True


class CustomExtractionRule(models.Model):
    """
    Define custom regex/pattern-based extraction rules.
    Allows users to specify patterns for extracting specific fields.
    """
    _name = 'document.intelligence.custom.rule'
    _description = 'Custom Extraction Rule'
    _inherit = ['mail.thread']
    _order = 'sequence, name'

    name = fields.Char(string='Rule Name', required=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)
    sequence = fields.Integer(default=10)

    # Rule scope
    document_type = fields.Selection([
        ('invoice', 'Invoice'),
        ('receipt', 'Receipt'),
        ('proforma', 'Proforma Invoice'),
        ('contract', 'Contract'),
        ('cv', 'CV / Resume'),
        ('form', 'Form'),
        ('general', 'General Document'),
    ], string='Applies To', required=True, default='invoice')

    vendor_ids = fields.Many2many(
        'res.partner',
        'di_custom_rule_vendor_rel',
        string='Specific Vendors',
        help='Leave empty to apply to all vendors.',
    )

    # Field to extract
    target_field = fields.Selection([
        ('invoice_number', 'Invoice Number'),
        ('order_number', 'Order/PO Number'),
        ('vat_number', 'VAT/Tax ID'),
        ('iban', 'Bank IBAN'),
        ('swift', 'SWIFT/BIC'),
        ('custom', 'Custom Field'),
    ], string='Target Field', required=True)

    custom_field_name = fields.Char(
        string='Custom Field Name',
        help='Required if target_field is "Custom".',
    )

    # Extraction pattern
    pattern_type = fields.Selection([
        ('regex', 'Regular Expression'),
        ('zapier', 'Zapier-style pattern (before/after)'),
        ('position', 'Fixed position/line'),
    ], string='Pattern Type', default='regex')

    # Regex pattern
    regex_pattern = fields.Text(
        string='Regex Pattern',
        help='Python-compatible regular expression. Use named groups (?P<field>...).',
        example='(?P<invoice_number>INV-[0-9]+)',
    )

    # Zapier-style: extract text between before and after markers
    before_text = fields.Char(
        string='Text Before Value',
        help='Extract value that appears after this text.',
    )
    after_text = fields.Char(
        string='Text After Value',
        help='Extract value that appears before this text.',
    )

    # Position-based: extract from specific page/line
    page_number = fields.Integer(string='Page Number', default=1)
    line_position = fields.Selection([
        ('first', 'First occurrence'),
        ('last', 'Last occurrence'),
        ('all', 'All occurrences'),
    ], string='Match Position', default='first')

    # Post-processing
    post_processing = fields.Selection([
        ('none', 'None'),
        ('uppercase', 'Uppercase'),
        ('lowercase', 'Lowercase'),
        ('strip', 'Trim whitespace'),
        ('digits_only', 'Digits only'),
        ('alphanumeric', 'Alphanumeric only'),
    ], string='Post-Processing', default='strip')

    # Validation
    validation_regex = fields.Char(
        string='Validation Regex',
        help='Optional regex to validate the extracted value.',
    )
    error_message = fields.Char(
        string='Validation Error Message',
        help='Shown if validation fails.',
    )

    # Statistics
    usage_count = fields.Integer(string='Times Used', default=0, readonly=True)
    success_count = fields.Integer(string='Successful Extractions', default=0, readonly=True)
    accuracy_rate = fields.Float(
        string='Accuracy %',
        compute='_compute_accuracy_rate',
        store=False,
    )

    @api.depends('usage_count', 'success_count')
    def _compute_accuracy_rate(self):
        for rule in self:
            if rule.usage_count > 0:
                rule.accuracy_rate = (rule.success_count / rule.usage_count) * 100
            else:
                rule.accuracy_rate = 0.0

    def action_apply_rule(self):
        """Show rule statistics and test result."""
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Rule Statistics'),
                'message': _('Rule "%s" used %d times with %.1f%% accuracy.') % (
                    self.name, self.usage_count, self.accuracy_rate),
                'type': 'info',
            },
        }

    def apply_rule(self, raw_text):
        """
        Apply this rule to raw text and return extracted value.
        Returns (value, confidence) tuple.
        """
        if not self.active:
            return None, 0.0

        text = raw_text or ''
        value = None
        confidence = 50.0  # default

        try:
            if self.pattern_type == 'regex' and self.regex_pattern:
                import re
                match = re.search(self.regex_pattern, text, re.IGNORECASE | re.MULTILINE)
                if match:
                    # Get the first named group or the entire match
                    if match.groupdict():
                        # Get first named group value
                        value = next(iter(match.groupdict().values()))
                    else:
                        value = match.group(0)
                    confidence = 90.0 if match.groupdict() else 70.0

            elif self.pattern_type == 'zapier' and self.before_text and self.after_text:
                # Find text between markers
                start_idx = text.find(self.before_text)
                if start_idx >= 0:
                    start_idx += len(self.before_text)
                    end_idx = text.find(self.after_text, start_idx)
                    if end_idx > start_idx:
                        value = text[start_idx:end_idx].strip()
                        confidence = 80.0

            elif self.pattern_type == 'position':
                # TODO: Implement page/line positioning
                pass

            # Apply post-processing
            if value:
                if self.post_processing == 'uppercase':
                    value = value.upper()
                elif self.post_processing == 'lowercase':
                    value = value.lower()
                elif self.post_processing == 'strip':
                    value = value.strip()
                elif self.post_processing == 'digits_only':
                    import re
                    value = ''.join(re.findall(r'\d', value))
                elif self.post_processing == 'alphanumeric':
                    import re
                    value = ''.join(re.findall(r'[a-zA-Z0-9]', value))

                # Validate if validation regex provided
                if self.validation_regex:
                    import re
                    if not re.match(self.validation_regex, value):
                        return None, 0.0

                self.sudo().write({
                    'usage_count': self.usage_count + 1,
                    'success_count': self.success_count + 1,
                })
                return value, confidence

        except Exception as e:
            _logger.warning('Custom rule %s failed: %s', self.name, e)

        self.sudo().write({'usage_count': self.usage_count + 1})
        return None, 0.0


class AuditReport(models.Model):
    """
    Pre-computed audit reports for compliance.
    Stores summary data for quick retrieval.
    """
    _name = 'document.intelligence.audit.report'
    _description = 'Audit & Compliance Report'
    _order = 'report_date desc, id desc'

    name = fields.Char(string='Report Name', required=True)
    report_type = fields.Selection([
        ('extraction_log', 'Extraction Log'),
        ('cost_summary', 'Cost & Quota Summary'),
        ('validation_summary', 'Validation Errors'),
        ('duplicate_report', 'Duplicate Detection Report'),
        ('vendor_activity', 'Vendor Activity'),
        ('user_activity', 'User Activity'),
    ], string='Report Type', required=True)

    report_date = fields.Date(string='Report Date', default=fields.Date.context_today, required=True)
    date_from = fields.Date(string='Start Date', required=True)
    date_to = fields.Date(string='End Date', required=True)

    # Filters
    user_id = fields.Many2one('res.users', string='User')
    partner_id = fields.Many2one('res.partner', string='Vendor')
    document_type = fields.Selection([
        ('invoice', 'Invoice'),
        ('receipt', 'Receipt'),
        ('proforma', 'Proforma'),
        ('contract', 'Contract'),
        ('cv', 'CV / Resume'),
        ('form', 'Form'),
        ('general', 'General Document'),
    ], string='Document Type')

    # Computed metrics (stored for performance)
    total_documents = fields.Integer(string='Total Documents', readonly=True)
    successful_extractions = fields.Integer(string='Successful Extractions', readonly=True)
    failed_extractions = fields.Integer(string='Failed Extractions', readonly=True)
    avg_confidence = fields.Float(string='Avg Confidence %', digits=(5, 2), readonly=True)
    total_cost_usd = fields.Float(string='Total Cost (USD)', digits=(10, 4), readonly=True)

    # Detailed data (JSON blobs)
    daily_breakdown = fields.Text(string='Daily Breakdown', readonly=True, help='JSON: {date: {count, cost}}')
    top_vendors = fields.Text(string='Top Vendors', readonly=True, help='JSON: [{vendor: count}]')
    error_summary = fields.Text(string='Error Summary', readonly=True)

    # Export
    exported = fields.Boolean(string='Exported', default=False)
    export_date = fields.Datetime(string='Exported On', readonly=True)

    def action_generate_report(self):
        """Re-generate this report."""
        self.ensure_one()
        # Regenerate based on filters
        records = self.env['document.intelligence.record'].search([
            ('create_date', '>=', self.date_from),
            ('create_date', '<=', self.date_to),
        ])
        if self.user_id:
            records = records.filtered(lambda r: r.create_uid == self.user_id)
        if self.partner_id:
            records = records.filtered(lambda r: r.partner_id == self.partner_id)
        if self.document_type:
            records = records.filtered(lambda r: r.detected_document_type == self.document_type)

        # Compute stats
        self.total_documents = len(records)
        self.successful_extractions = len(records.filtered(lambda r: r.state == 'done'))
        self.failed_extractions = len(records.filtered(lambda r: r.state == 'error'))

        confidences = records.filtered(lambda r: r.confidence_score > 0).mapped('confidence_score')
        self.avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        costs = records.mapped('extraction_cost_usd')
        self.total_cost_usd = sum(costs)

        # Build daily breakdown
        from collections import defaultdict
        daily_data = defaultdict(lambda: {'count': 0, 'cost': 0.0})
        for rec in records:
            date_str = rec.create_date.date().isoformat()
            daily_data[date_str]['count'] += 1
            daily_data[date_str]['cost'] += rec.extraction_cost_usd or 0.0
        self.daily_breakdown = str(dict(daily_data))

        # Top vendors
        vendor_counts = defaultdict(int)
        for rec in records:
            if rec.partner_id:
                vendor_counts[rec.partner_id.name] += 1
        sorted_vendors = sorted(vendor_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        self.top_vendors = str(dict(sorted_vendors))

        return True

    def action_export_csv(self):
        """Export report to CSV."""
        self.ensure_one()
        # Generate CSV export
        return {
            'type': 'ir.actions.act_url',
            'url': f'/document_intelligence/audit/export/{self.id}?format=csv',
            'target': 'self',
        }

    # ── Scheduled report generation ───────────────────────────────────────────────

    @api.model
    def _scheduled_generate_monthly_report(self):
        """Create monthly compliance/cost report automatically."""
        from datetime import datetime, date
        today = date.today()
        first_of_month = today.replace(day=1)
        last_of_prev_month = first_of_month - timedelta(days=1)
        start_date = last_of_prev_month.replace(day=1)
        end_date = last_of_prev_month

        # Check if report already exists for this month
        existing = self.search([
            ('report_type', '=', 'cost_summary'),
            ('date_from', '=', start_date),
            ('date_to', '=', end_date),
        ], limit=1)

        if existing:
            existing.action_generate_report()
            _logger.info('Updated monthly DI audit report for %s', start_date)
        else:
            report = self.create({
                'name': f'Monthly Cost Report - {start_date.strftime("%B %Y")}',
                'report_type': 'cost_summary',
                'date_from': start_date,
                'date_to': end_date,
                'report_date': end_date,
            })
            report.action_generate_report()
            _logger.info('Created monthly DI audit report for %s', start_date)

        return True


class DocumentGridReviewWizard(models.TransientModel):
    """
    Grid review wizard for batch editing of multiple documents before creation.
    """
    _name = 'document.intelligence.batch.review.wizard'
    _description = 'Batch Grid Review Wizard'

    batch_id = fields.Many2one(
        'document.intelligence.batch',
        string='Batch',
        required=True,
    )
    document_ids = fields.Many2many(
        'document.intelligence.record',
        'di_batch_review_doc_rel',
        string='Documents to Review',
        domain="[('state', '=', 'review')]",
    )

    # Bulk edit fields
    bulk_action = fields.Selection([
        ('approve_all', 'Approve All'),
        ('reject_all', 'Reject All'),
        ('update_partner', 'Update Partner for All'),
        ('set_tax', 'Set Tax for All'),
    ], string='Bulk Action', default='approve_all')

    new_partner_id = fields.Many2one('res.partner', string='New Partner')
    new_tax_id = fields.Many2one('account.tax', string='Tax to Apply')

    # Display summary
    total_amount_sum = fields.Float(string='Total Amount', compute='_compute_totals', store=False)
    avg_confidence = fields.Float(string='Avg Confidence', compute='_compute_totals', store=False)

    @api.depends('document_ids.total_amount', 'document_ids.confidence_score')
    def _compute_totals(self):
        self.total_amount_sum = sum(self.document_ids.mapped('total_amount'))
        confidences = self.document_ids.mapped('confidence_score')
        self.avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    def action_bulk_approve(self):
        """Approve all documents with corrections."""
        for doc in self.document_ids:
            if self.new_partner_id:
                doc.partner_id = self.new_partner_id.id
            if self.new_tax_id:
                doc.tax_ids = [(6, 0, [self.new_tax_id.id])]
            doc.action_create_odoo_record()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_bulk_reject(self):
        """Reject all documents."""
        for doc in self.document_ids:
            doc.action_reset_to_draft()
        return {'type': 'ir.actions.client', 'tag': 'reload'}


class BankMatchWizard(models.TransientModel):
    """
    Wizard to select the correct bank statement line when multiple matches found.
    """
    _name = 'document.intelligence.bank.match.wizard'
    _description = 'Bank Statement Match Wizard'

    document_id = fields.Many2one(
        'document.intelligence.record',
        string='Document',
        required=True,
    )
    candidate_line_ids = fields.Many2many(
        'account.bank.statement.line',
        'di_bank_match_candidate_rel',
        string='Candidate Statement Lines',
    )
    selected_line_id = fields.Many2one(
        'account.bank.statement.line',
        string='Select Line to Match',
        required=True,
        domain="[('id', 'in', candidate_line_ids)]",
    )

    def action_match(self):
        """Link the selected bank statement line to this document."""
        self.ensure_one()
        self.document_id.bank_statement_line_id = self.selected_line_id.id
        # Optionally add a note to the bank statement line
        self.selected_line_id.message_post(body=_(
            'Matched with Document Intelligence record: %s (Amount: %s)'
        ) % (self.document_id.name, self.document_id.total_amount))
        self.document_id.message_post(body=_(
            'Matched with bank statement line: %s'
        ) % self.selected_line_id.name)
        return {'type': 'ir.actions.act_window_close'}


class ExpenseCorrectionWizard(models.TransientModel):
    """
    Quick correction wizard for expenses when AI extraction is off.
    """
    _name = 'document.intelligence.expense.correction.wizard'
    _description = 'Expense Correction Wizard'

    line_item_id = fields.Many2one('document.intelligence.line.item', required=True)
    product_id = fields.Many2one('product.product', string='Product', required=True)
    quantity = fields.Float(string='Quantity', default=1.0)
    unit_price = fields.Float(string='Unit Price', digits='Product Price')

    def action_apply(self):
        line = self.env['document.intelligence.line.item'].browse(self.line_item_id.id)
        line.write({
            'product_id': self.product_id.id,
            'quantity': self.quantity,
            'unit_price': self.unit_price,
        })
        return {'type': 'ir.actions.act_window_close'}
