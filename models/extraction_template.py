from odoo import models, fields, api


class ExtractionTemplate(models.Model):
    _name = 'document.intelligence.template'
    _description = 'Document Extraction Template'
    _order = 'name'

    name = fields.Char(string='Template Name', required=True)
    document_type = fields.Selection([
        ('invoice', 'Invoice'),
        ('receipt', 'Receipt'),
        ('contract', 'Contract'),
        ('cv', 'CV / Resume'),
        ('form', 'Form'),
        ('general', 'General Document'),
    ], string='Document Type', required=True)
    description = fields.Text(string='Description')

    # Fields the AI should extract — stored as a comma-separated list
    fields_to_extract = fields.Text(
        string='Fields to Extract',
        required=True,
        help='One field per line. Example:\nvendor\ndate\ntotal\nphone\naddress',
        default='vendor\ndate\ntotal',
    )

    # Custom prompt hint sent to the AI alongside the document text
    prompt_hint = fields.Text(
        string='AI Prompt Hint',
        help='Extra context given to the AI. Example: "This is a Rwandan supplier invoice, amounts are in RWF."',
    )

    usage_count = fields.Integer(string='Times Used', default=0, readonly=True)
    active = fields.Boolean(default=True)

    def get_fields_list(self):
        self.ensure_one()
        return [f.strip() for f in (self.fields_to_extract or '').splitlines() if f.strip()]
