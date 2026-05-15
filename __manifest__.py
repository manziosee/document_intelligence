{
    'name': 'Document Intelligence',
    'version': '4.0.0',
    'summary': 'OCR + AI data extraction — works without any API key. Upgrade for higher accuracy.',
    'description': """
Document Intelligence — OCR Data Fetching for Odoo
===================================================

Extracts strings and structured data from images and documents so whatever is
written in an image or PDF can be fetched as a record in Odoo.

THREE-TIER EXTRACTION — WORKS WITHOUT ANY API KEY
--------------------------------------------------
* Tier 1 — Rule-Based  : Free, zero setup, zero cost. ~70-80% accuracy on structured invoices.
* Tier 2 — Local AI    : Ollama (open source). Free, private, runs on your own server.
* Tier 3 — Cloud AI    : Groq (free tier), OpenAI, Anthropic. Highest accuracy.

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
* Auto  — detects document type and extracts all relevant fields
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
    # No hard external_dependencies — all optional packages are lazy-imported inside
    # functions and fail gracefully with actionable error messages.
    # The module installs and runs with ZERO extra pip installs.
    #
    # Optional installs unlock more file types:
    #   pip install pdfminer.six           # best PDF text extraction (recommended)
    #   pip install pypdf                   # PDF fallback (pure Python, no binary)
    #   pip install Pillow pytesseract      # scanned images + image OCR
    #   pip install PyMuPDF                 # scanned PDF → image → OCR pipeline
    #   pip install python-docx             # richer DOCX extraction (stdlib fallback built-in)
    #   pip install openai anthropic        # Tier 3 cloud AI providers
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/document_record_views.xml',
        'views/extraction_template_views.xml',
        'views/quota_log_views.xml',
        'views/error_log_views.xml',
        'views/res_config_settings_views.xml',
        'views/enhanced_features_views.xml',
        'views/server_actions.xml',
        'wizard/document_review_wizard_view.xml',
        'wizard/from_odoo_wizard_view.xml',
        'wizard/setup_wizard_view.xml',
        'views/menu.xml',
        'data/scheduled_actions.xml',
        'data/sample_templates.xml',
    ],
    # Works on Odoo 17, 18, 19 — version compatibility via services/compat.py
    'assets': {
        'web.assets_backend': [
            'document_intelligence/static/src/scss/document_intelligence.scss',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'application': True,
    'installable': True,
    'auto_install': False,
}
