{
    'name': 'Document Intelligence',
    'version': '17.0.2.0.0',
    'summary': 'OCR + AI data extraction from any document — invoices, proformas, CVs and more',
    'description': """
Document Intelligence — OCR Data Fetching for Odoo
===================================================

Extracts strings and structured data from images and documents so whatever is
written in an image or PDF can be fetched as a record in Odoo.

HOW CAN BUSINESSES BENEFIT?
----------------------------
* Reduces human error and typos in data entry
* Digitizes an organisation's data-entry process completely
* Retrieves a large volume of data quickly — no manual typing
* Frees up staff time from laborious, repetitive duties
* Works with local document formats (RWF invoices, mixed languages)

TWO WAYS TO USE IT
------------------
1. Upload a new file (image, PDF, DOCX) directly from the Document Intelligence module
2. Extract from documents ALREADY in Odoo:
   - Open any vendor invoice / proforma → click "Extract with AI"
   - Open any HR applicant → click "Extract CV with AI"
   - Select any existing attachment from the document form

SUPPORTED DOCUMENT TYPES
-------------------------
* Invoices & Vendor Bills
* Proforma Invoices
* Receipts
* Contracts
* CVs / Resumes
* Forms & General Documents

EXTRACTION MODES
----------------
* Auto  — AI detects type and extracts all relevant fields
* Custom — User specifies exactly which fields to extract
* Template — Reusable templates for recurring document formats

WHAT HAPPENS AFTER EXTRACTION?
-------------------------------
* Review & correct all extracted fields before saving
* One click creates: Vendor Bill, Contact, HR Applicant, or stores the document
* If extracted from an existing Odoo record — data is written back to that record
    """,
    'category': 'Productivity',
    'author': 'Manzi Osee',
    'website': '',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'mail',
        'account',
        'web',
    ],
    'external_dependencies': {
        'python': ['pytesseract', 'Pillow', 'pdfminer.six', 'PyMuPDF', 'python-docx'],
        'bin': ['tesseract'],
    },
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/document_record_views.xml',
        'views/extraction_template_views.xml',
        'views/res_config_settings_views.xml',
        'views/server_actions.xml',
        'wizard/document_review_wizard_view.xml',
        'wizard/from_odoo_wizard_view.xml',
        'views/menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'document_intelligence/static/src/scss/document_intelligence.scss',
        ],
    },
    'application': True,
    'installable': True,
    'auto_install': False,
}
