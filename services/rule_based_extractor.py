"""
Tier 1 — Rule-Based Extractor
==============================
Zero dependencies beyond the Python standard library.
No API key, no internet connection, no cost — ever.

How it works
------------
1. Detect document type from title keywords
2. Split the page into a "header block" (top ≈30 lines) and "body"
3. Run each field extractor in priority order:
   - Labelled patterns first  (e.g. "Total: 1,234,000 RWF")
   - Structural patterns next (e.g. last large number on the page)
   - Positional heuristics last (e.g. first company-looking line)
4. Estimate confidence from how many key fields were found
5. Return a dict that DocumentRecord.populate_from_extracted() understands

Customisation
-------------
Users add their own regex patterns in:
  Document Intelligence → Custom Rules
Those rules are merged on top of these results by the document processor.
No Python coding required.
"""
import re
import logging
from datetime import datetime, date

_logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════════
# Pattern constants
# ════════════════════════════════════════════════════════════════════════════════

# ── Currencies ────────────────────────────────────────────────────────────────
_CURRENCY_CODES = r'(?:RWF|Frw|FRW|RFw|USD|EUR|GBP|KES|TZS|UGX|CHF|JPY|CNY|AUD|CAD|ZAR|NGN|GHS)'
_CURRENCY_SYMBOLS = r'(?:\$|€|£|¥|₦|₵)'
_CURRENCY_RE = re.compile(
    r'\b(RWF|USD|EUR|GBP|KES|TZS|UGX|CHF|JPY|CNY|AUD|CAD|ZAR|NGN|GHS)\b',
    re.IGNORECASE,
)

# ── Numbers ───────────────────────────────────────────────────────────────────
# Matches: 1,234,567  |  1 234 567  |  1.234.567  |  1234567  |  1,234.56
_NUM = r'[\d]{1,3}(?:[,\.\s]\d{3})*(?:[,\.]\d{1,2})?|\d+'

# Labelled total patterns — highest priority for amount extraction
_TOTAL_LABEL_PATTERNS = [
    # "Grand Total : RWF 1,234,000"  or  "Total Amount: 1 234 000"
    rf'(?:grand\s*total|total\s*(?:amount|due|payable|ttc|ht)?|montant\s*total|'
    rf'net\s*(?:total|payable|amount)?|amount\s*(?:due|payable)|'
    rf'balance\s*(?:due|payable)?)\s*[:\-]?\s*(?:{_CURRENCY_CODES})?\s*({_NUM})',
    # "RWF 1,234,000" alone on a line (Rwanda-specific)
    rf'(?:{_CURRENCY_CODES})\s+({_NUM})\s*$',
    # "1,234,000 RWF" alone on a line
    rf'^({_NUM})\s*(?:{_CURRENCY_CODES})\s*$',
    # Symbol then number: "$1,234.56"
    rf'(?:{_CURRENCY_SYMBOLS})\s*({_NUM})',
]

# Subtotal / net patterns — used for tax computation
_SUBTOTAL_PATTERNS = [
    rf'(?:sub\s*total|subtotal|net\s*(?:amount)?|h\.?t\.?|excl\.?\s*(?:vat|tax))\s*[:\-]?\s*(?:{_CURRENCY_CODES})?\s*({_NUM})',
]

# Tax patterns
_TAX_LABEL_PATTERNS = [
    # "VAT 18%: 123,456" or "TVA (18%): RWF 123,456"
    rf'(?:vat|tva|t\.v\.a|tax)\s*(?:@|at|de)?\s*\d{{1,2}}(?:[,\.]\d{{1,2}})?\s*%\s*[:\-]?\s*(?:{_CURRENCY_CODES})?\s*({_NUM})',
    # "VAT Amount: 123,456"
    rf'(?:vat|tva|tax)\s*(?:amount|total|montant)?\s*[:\-]?\s*(?:{_CURRENCY_CODES})?\s*({_NUM})',
]

# Tax rate
_TAX_RATE_PATTERN = re.compile(
    r'(?:vat|tva|tax|taxe)\s*(?:rate|taux|@|at)?\s*[:\-]?\s*(\d{1,2}(?:[,\.]\d{1,2})?)\s*%',
    re.IGNORECASE,
)

# ── Dates ─────────────────────────────────────────────────────────────────────
_MONTH_LONG = (
    r'January|February|March|April|May|June|July|August|'
    r'September|October|November|December|'
    r'Janvier|Février|Mars|Avril|Mai|Juin|Juillet|Août|Septembre|Octobre|Novembre|Décembre'
)
_MONTH_SHORT = r'Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec'
_MONTH_FRENCH_SHORT = r'Janv?|Févr?|Mars|Avr|Juin|Juil|Août|Sept?|Oct|Nov|Déc'

_DATE_LABELED_PATTERNS = [
    # "Invoice Date: 15/01/2024" / "Date: 2024-01-15"
    rf'(?:invoice\s*date|date\s*(?:of\s*invoice|issued|d\'?émission)?|issued\s*(?:on|date)?|'
    rf'bill\s*date|document\s*date)\s*[:\-]?\s*'
    rf'(\d{{1,2}}[/\-\.]\d{{1,2}}[/\-\.]\d{{4}}|\d{{4}}[/\-]\d{{2}}[/\-]\d{{2}}|'
    rf'\d{{1,2}}\s+(?:{_MONTH_LONG}|{_MONTH_SHORT})\s+\d{{4}})',
]

# Bare date patterns (fallback), ordered by reliability
_DATE_BARE_PATTERNS = [
    # ISO: 2024-01-15
    r'\b(\d{4}[-/]\d{1,2}[-/]\d{1,2})\b',
    # DMY with separators: 15/01/2024  15-01-2024  15.01.2024
    r'\b(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4})\b',
    # "15 January 2024" / "January 15, 2024" / "15 Jan 2024"
    rf'\b(\d{{1,2}}\s+(?:{_MONTH_LONG})\s+\d{{4}})\b',
    rf'\b((?:{_MONTH_LONG})\s+\d{{1,2}},?\s+\d{{4}})\b',
    rf'\b(\d{{1,2}}\s+(?:{_MONTH_SHORT})\.?\s+\d{{4}})\b',
]

# ── Reference / Invoice number ─────────────────────────────────────────────────
_REF_LABEL_PATTERNS = [
    # "Invoice No: INV-2024-001"
    rf'(?:invoice\s*(?:no\.?|number|#|num\.?|n°)|inv\.?\s*(?:no\.?|#|num\.?|n°)|'
    rf'ref(?:erence)?\s*(?:no\.?|#|num\.?|n°)?|bill\s*(?:no\.?|#)|'
    rf'order\s*(?:no\.?|#)|quotation\s*(?:no\.?|#)|proforma\s*(?:no\.?|#)|'
    rf'devis\s*(?:no\.?|#|n°)?|facture\s*(?:no\.?|#|n°)?|'
    rf'receipt\s*(?:no\.?|#)|n°\s*(?:facture)?)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\/\-\.]{2,29})',
    # Standalone pattern: "No. INV2024001" / "#2024/001"
    r'(?:No\.?|N°|#)\s+([A-Z0-9][A-Z0-9\/\-\.]{2,24})\b',
]

# ── Email ─────────────────────────────────────────────────────────────────────
_EMAIL_RE = re.compile(
    r'\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b',
)

# ── Phone ─────────────────────────────────────────────────────────────────────
# Rwanda: 078/079/072/073/075 XXXXXXX  or +250 7XX XXX XXX
# Generic international: +XX XXXXXXXXXX
_PHONE_PATTERNS = [
    # Labelled phone lines
    r'(?:tel(?:ephone)?|phone|mob(?:ile)?|cell(?:ulaire)?|contact|portable|tél)\s*[:\-]?\s*(\+?[\d][\d\s\-\(\)\.]{6,19}\d)',
    # Rwanda mobile: 07X XXXXXXX or +250 7XX XXX XXX
    r'\b(\+?250\s*7\d[\d\s]{7,10})\b',
    r'\b(07[2-9]\s*[\d\s]{7,8})\b',
    # International E.164
    r'\b(\+[1-9]\d{1,3}[\s\-]?\d{3,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4})\b',
]

# ── VAT / TIN ─────────────────────────────────────────────────────────────────
# Rwanda TIN: exactly 9 digits (issued by RRA)
_VAT_PATTERNS = [
    r'(?:TIN|VAT\s*(?:reg(?:istration)?\s*)?(?:no\.?|#|n°|num\.?)|'
    r'Tax\s*(?:ID|identification)|TVA\s*(?:no\.?|n°)|'
    r'Numéro?\s*(?:TVA|fiscal))\s*[:\-]?\s*([A-Z0-9\-]{5,20})',
    r'\bTIN\s*[:\-]?\s*(\d{9})\b',           # Rwanda: 9-digit TIN
    r'\b(\d{9})\s*(?:TIN|RRA)\b',            # TIN before label
]

# ── IBAN / SWIFT ──────────────────────────────────────────────────────────────
_IBAN_RE = re.compile(r'\b([A-Z]{2}\d{2}[A-Z0-9]{4,30})\b')
_SWIFT_RE = re.compile(
    r'\b(?:SWIFT|BIC|code\s*(?:banque)?)\s*[:\-]?\s*([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b',
    re.IGNORECASE,
)
# Also bare SWIFT without label (8 or 11 uppercase alphanumeric)
_SWIFT_BARE_RE = re.compile(r'\b([A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b')

# ── Document type detection ───────────────────────────────────────────────────
_DOCTYPE_KEYWORDS: dict[str, list[str]] = {
    # Proof of payment scores highest on these — must come before receipt/invoice
    'proof_of_payment': [
        'proof of payment', 'payment confirmation', 'payment confirmed',
        'transaction confirmation', 'transfer confirmation', 'bank confirmation',
        'payment advice', 'remittance advice', 'payment notification',
        'payment voucher', 'payment slip', 'acknowledgement of payment',
        'wire transfer confirmation', 'swift confirmation',
        'debit confirmation', 'credit confirmation',
        'preuve de paiement', 'confirmation de paiement',
    ],
    'proforma': [
        'proforma invoice', 'pro-forma invoice', 'pro forma invoice',
        'proforma', 'pro-forma', 'quotation', 'devis', 'estimate', 'quote',
        'offre de prix', 'price offer',
    ],
    'invoice': [
        'tax invoice', 'fiscal invoice', 'vat invoice', 'facture',
        'invoice', 'debit note', 'note de débit',
    ],
    'receipt': [
        'official receipt', 'cash receipt', 'reçu de caisse',
        'receipt', 'reçu',
    ],
    'cv': [
        'curriculum vitae', 'curriculum vitæ',
        'résumé', 'resume', 'cv ', ' cv\n', 'c.v.',
        'professional profile', 'work experience', 'education background',
    ],
    'contract': [
        'service agreement', 'supply agreement', 'memorandum of understanding',
        'mou ', 'terms and conditions', 'contrat', 'convention',
        'contract', 'agreement',
    ],
}

_DOCTYPE_ACTION = {
    'invoice':          'create_invoice',
    'proforma':         'create_invoice',
    'proof_of_payment': 'review',
    'receipt':          'create_expense_claim',
    'cv':               'create_applicant',
    'contract':         'review',
    'other':            'review',
}

# Company / legal entity suffixes common in Rwanda and globally
_COMPANY_SUFFIXES = re.compile(
    r'\b(?:ltd\.?|limited|inc\.?|incorporated|corp\.?|corporation|'
    r'sarl|s\.a\.r\.l\.?|s\.a\.?|plc\.?|llc\.?|gmbh|sas|pty\.?|'
    r'co\.?\s*ltd\.?|company|enterprise|enterprises|group|holding|'
    r'Rwanda|rw)\b',
    re.IGNORECASE,
)

# Words that disqualify a line from being a vendor name
_VENDOR_SKIP = re.compile(
    r'\b(?:invoice|receipt|proforma|pro.forma|quotation|contract|date|'
    r'bill\s+to|invoice\s+to|sold\s+to|ship\s+to|attention|attn|'
    r'tel|fax|email|phone|mob|address|p\.?o\.?\s*box|po\s+box|'
    r'ref|number|no\.|page|vat|tin|total|amount|due|payable|'
    r'thank\s+you|regards|sincerely|to\s+whom)\b',
    re.IGNORECASE,
)

# ── Address keywords ───────────────────────────────────────────────────────────
_ADDRESS_KEYWORDS = re.compile(
    r'\b(?:street|avenue|road|lane|drive|blvd|boulevard|'
    r'kg|kn|kk|kg\s*\d|kn\s*\d|km\s*\d|'           # Kigali sectors
    r'p\.?o\.?\s*box|po\s+box|bp\s+\d|b\.p\.|'      # PO Box
    r'district|sector|cell|village|'
    r'kigali|rwanda|nairobi|kampala|dar\s+es\s+salaam|'
    r'plot\s+no|house\s+no|flat\s+no|floor)\b',
    re.IGNORECASE,
)


# ════════════════════════════════════════════════════════════════════════════════
# Pure helper functions
# ════════════════════════════════════════════════════════════════════════════════

def _clean_amount(raw: str) -> float | None:
    """
    Normalise various number formats to a Python float.

    Handles:
    - Comma as thousands:  1,234,567     → 1234567.0
    - Space as thousands:  1 234 567     → 1234567.0
    - Period as thousands: 1.234.567     → 1234567.0  (only when >1 period group)
    - Decimal comma:       1.234,56      → 1234.56
    - Decimal period:      1,234.56      → 1234.56
    - Plain:               1234567       → 1234567.0
    """
    if not raw:
        return None
    s = raw.strip()
    # Remove all spaces
    s = re.sub(r'\s', '', s)

    # Detect European format: last separator is comma, and there are periods
    # e.g. "1.234.567,89"
    if re.match(r'^\d{1,3}(?:\.\d{3})+,\d{1,2}$', s):
        s = s.replace('.', '').replace(',', '.')
    # Detect format where comma is thousands sep and period is decimal
    # e.g. "1,234,567.89" or "1,234,567"
    elif re.match(r'^\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?$', s):
        s = s.replace(',', '')
    # Multiple periods as thousands sep: "1.234.567"
    elif re.match(r'^\d{1,3}(?:\.\d{3})+$', s):
        s = s.replace('.', '')
    # Single comma — could be decimal or thousands; heuristic: if ends in 2 or 3 digits after comma
    elif ',' in s and '.' not in s:
        parts = s.split(',')
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            s = parts[0] + '.' + parts[1]
        else:
            s = s.replace(',', '')
    # Otherwise strip any remaining commas
    else:
        s = s.replace(',', '')

    try:
        val = float(s)
        return val if val >= 0 else None
    except ValueError:
        return None


def _parse_date(raw: str) -> str | None:
    """
    Convert any recognised date string to ISO YYYY-MM-DD.
    Returns None if parsing fails or date is implausible.
    """
    raw = raw.strip()

    # ISO  2024-01-15 or 2024/01/15
    m = re.match(r'^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$', raw)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _validate_date(y, mo, d)

    # DMY  15/01/2024  15-01-2024  15.01.2024
    m = re.match(r'^(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})$', raw)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _validate_date(y, mo, d)

    # Named month formats
    for fmt in (
        '%d %B %Y', '%d %b %Y',
        '%B %d, %Y', '%B %d %Y',
        '%b %d, %Y', '%b %d %Y',
        '%d %B %Y', '%d %b. %Y',
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            return _validate_date(dt.year, dt.month, dt.day)
        except ValueError:
            continue

    # French month names mapped to English
    french_map = {
        'janvier': 'january', 'février': 'february', 'mars': 'march',
        'avril': 'april', 'mai': 'may', 'juin': 'june',
        'juillet': 'july', 'août': 'august', 'septembre': 'september',
        'octobre': 'october', 'novembre': 'november', 'décembre': 'december',
        'janv': 'jan', 'févr': 'feb', 'avr': 'apr', 'juil': 'jul', 'déc': 'dec',
    }
    lower = raw.lower()
    for fr, en in french_map.items():
        if fr in lower:
            lower = lower.replace(fr, en)
            break
    if lower != raw.lower():
        return _parse_date(lower.title())

    return None


def _validate_date(y: int, mo: int, d: int) -> str | None:
    """Return ISO string if date is plausible, else None."""
    try:
        dt = date(y, mo, d)
        today = date.today()
        if date(today.year - 10, 1, 1) <= dt <= date(today.year + 1, 12, 31):
            return dt.strftime('%Y-%m-%d')
    except ValueError:
        pass
    return None


def _first_match(patterns: list[str] | str, text: str) -> str | None:
    """Return first capturing group from first matching pattern."""
    if isinstance(patterns, str):
        patterns = [patterns]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1).strip()
    return None


def _all_amounts_in_text(text: str) -> list[float]:
    """Extract all plausible monetary amounts from the text."""
    amounts = []
    for m in re.finditer(
        rf'(?:{_CURRENCY_CODES}|{_CURRENCY_SYMBOLS})\s*({_NUM})|'
        rf'({_NUM})\s*(?:{_CURRENCY_CODES})',
        text, re.IGNORECASE
    ):
        raw = m.group(1) or m.group(2)
        if raw:
            val = _clean_amount(raw)
            if val and val > 0:
                amounts.append(val)
    return amounts


# ════════════════════════════════════════════════════════════════════════════════
# Field-specific extractors
# ════════════════════════════════════════════════════════════════════════════════

def _extract_amounts(text: str) -> tuple[float | None, float | None]:
    """
    Return (total_amount, tax_amount).

    Strategy:
    1. Try labelled total patterns → most reliable
    2. Try labelled tax patterns
    3. Fallback: find all currency-tagged numbers, pick the largest as total
    """
    total = None
    tax = None

    # 1. Labelled total
    for pat in _TOTAL_LABEL_PATTERNS:
        raw = _first_match(pat, text)
        if raw:
            val = _clean_amount(raw)
            if val and val > 0:
                total = val
                break

    # 2. Labelled tax
    for pat in _TAX_LABEL_PATTERNS:
        raw = _first_match(pat, text)
        if raw:
            val = _clean_amount(raw)
            if val and val > 0:
                tax = val
                break

    # 3. Fallback: all currency-tagged amounts, largest = total
    if total is None:
        tagged = _all_amounts_in_text(text)
        if tagged:
            total = max(tagged)

    # Sanity: if tax ≥ total, discard tax (OCR noise)
    if total and tax and tax >= total:
        tax = None

    return total, tax


def _extract_dates(text: str) -> str | None:
    """
    Return the most reliable date found.
    Priority: labelled invoice date > first plausible bare date.
    """
    # 1. Labelled dates
    for pat in _DATE_LABELED_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            parsed = _parse_date(m.group(1))
            if parsed:
                return parsed

    # 2. Bare dates
    for pat in _DATE_BARE_PATTERNS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            parsed = _parse_date(m.group(1))
            if parsed:
                return parsed

    return None


def _extract_reference(text: str) -> str | None:
    """Extract invoice/document reference number."""
    for pat in _REF_LABEL_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            ref = m.group(1).strip()
            # Discard if it looks like a date or pure-digit TIN
            if re.match(r'^\d{9}$', ref):
                continue
            if re.match(r'^\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4}$', ref):
                continue
            if len(ref) >= 3:
                return ref
    return None


def _extract_currency(text: str) -> str | None:
    """Return ISO currency code. RWF if text mentions Rwanda / Kigali and no other code found."""
    m = _CURRENCY_RE.search(text)
    if m:
        return m.group(1).upper()
    # Rwanda context fallback
    if re.search(r'\b(?:Rwanda|Kigali|RRA|Frw)\b', text, re.IGNORECASE):
        return 'RWF'
    return None


def _extract_email(text: str) -> str | None:
    m = _EMAIL_RE.search(text)
    return m.group(1) if m else None


def _extract_phone(text: str) -> str | None:
    """Return first plausible phone number, cleaned up."""
    for pat in _PHONE_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            phone = m.group(1).strip()
            # Normalise whitespace
            phone = re.sub(r'\s+', ' ', phone)
            # Must have at least 7 digits
            if len(re.sub(r'\D', '', phone)) >= 7:
                return phone
    return None


def _extract_vat_tin(text: str) -> str | None:
    """Extract VAT / TIN number. Prefers Rwanda 9-digit TIN."""
    for pat in _VAT_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val:
                return val
    return None


def _extract_iban(text: str) -> str | None:
    m = _IBAN_RE.search(text)
    if m:
        candidate = m.group(1)
        if len(candidate) >= 15:   # IBAN min length
            return candidate
    return None


def _extract_swift(text: str) -> str | None:
    m = _SWIFT_RE.search(text)
    if m:
        return m.group(1)
    # Bare pattern: only accept if near a bank-related keyword
    for bm in _SWIFT_BARE_RE.finditer(text):
        pos = bm.start()
        context = text[max(0, pos - 60):pos + 60].lower()
        if any(kw in context for kw in ('bank', 'swift', 'bic', 'banque', 'account')):
            return bm.group(1)
    return None


def _extract_tax_rate(text: str) -> float | None:
    m = _TAX_RATE_PATTERN.search(text)
    if m:
        return _clean_amount(m.group(1))
    # Look for "18%" anywhere in VAT context
    m2 = re.search(
        r'(?:vat|tva|tax)\b[^%]{0,40}?(\d{1,2}(?:[,\.]\d{1,2})?)\s*%',
        text, re.IGNORECASE
    )
    if m2:
        return _clean_amount(m2.group(1))
    return None


# ── Vendor / supplier name ────────────────────────────────────────────────────

def _extract_vendor_name(text: str, bill_to_block: str | None) -> str | None:
    """
    Find the supplier/vendor company name.

    Strategy (in order):
    1. "From:" / "Supplier:" / "Sold by:" explicit label
    2. First line with a recognised company suffix (Ltd, SARL, …)
    3. First ALL-CAPS multi-word line in the top section
    4. First non-trivial line in the top section not matching skip patterns
    """
    lines = [ln.strip() for ln in text.split('\n') if ln.strip()]

    # Build a set of lines that belong to the "Bill To" block — skip those
    skip_lines: set[str] = set()
    if bill_to_block:
        for ln in bill_to_block.split('\n'):
            skip_lines.add(ln.strip())

    # 1. Explicit "From / Supplier / Seller" label
    from_match = re.search(
        r'(?:from|supplier|vendeur|fournisseur|seller|sold\s+by|issued\s+by|'
        r'billed\s+by|service\s+provider)\s*[:\-]\s*(.{3,80})',
        text, re.IGNORECASE,
    )
    if from_match:
        candidate = from_match.group(1).strip().split('\n')[0].strip()
        if len(candidate) >= 3:
            return candidate

    # 2 + 3 + 4. Scan top 25 lines
    for line in lines[:25]:
        if line in skip_lines:
            continue
        if len(line) < 3 or len(line) > 100:
            continue
        # Skip lines that are mostly digits
        digit_ratio = sum(c.isdigit() for c in line) / max(len(line), 1)
        if digit_ratio > 0.45:
            continue
        # Skip lines matching document-header / contact keywords
        if _VENDOR_SKIP.search(line):
            continue
        if _EMAIL_RE.search(line):
            continue
        if re.search(r'[@\+\(\)]', line) and re.search(r'\d{5,}', line):
            continue

        # Priority: company suffix match
        if _COMPANY_SUFFIXES.search(line):
            return line

    # 3. First ALL-CAPS multi-word line (≥2 words, common on East African invoices)
    for line in lines[:20]:
        if line in skip_lines:
            continue
        words = line.split()
        if (
            len(words) >= 2
            and line == line.upper()
            and all(re.match(r'^[A-Z0-9&\-\.\,\'\s]+$', w) for w in words)
            and not _VENDOR_SKIP.search(line)
            and not _EMAIL_RE.search(line)
        ):
            return line

    # 4. First non-trivial line
    for line in lines[:10]:
        if line in skip_lines:
            continue
        if (
            len(line) >= 4
            and not re.match(r'^\d', line)
            and not _VENDOR_SKIP.search(line)
            and not _EMAIL_RE.search(line)
            and any(c.isalpha() for c in line)
        ):
            return line

    return None


# ── Bill-to / recipient block ─────────────────────────────────────────────────

def _extract_bill_to_block(text: str) -> str | None:
    """
    Extract the "Bill To" / "Invoice To" / "Sold To" block.
    Returns the raw multi-line block or None.
    """
    m = re.search(
        r'(?:bill\s+to|invoice\s+to|sold\s+to|ship\s+to|attention|attn|'
        r'client|customer|to\s*:)\s*[:\-]?\s*\n?((?:.+\n?){1,5})',
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    return None


def _extract_contact_name(text: str) -> str | None:
    """For CV documents: find full name in top lines."""
    lines = [ln.strip() for ln in text.split('\n') if ln.strip()]
    for line in lines[:10]:
        words = line.split()
        if (
            2 <= len(words) <= 4
            and all(len(w) >= 2 and w[0].isupper() and re.match(r'^[A-Za-zÀ-ÿ\-\']+$', w) for w in words)
        ):
            return line
    return None


# ── Address ───────────────────────────────────────────────────────────────────

def _extract_address(text: str, label_context: str | None = None) -> str | None:
    """
    Extract a physical address.
    If label_context is given (e.g. 'bill to' block), search within it first.
    """
    search_in = label_context or text
    # Look for lines with address keywords, collect 1-3 consecutive lines
    lines = search_in.split('\n')
    for i, line in enumerate(lines):
        if _ADDRESS_KEYWORDS.search(line):
            block = '\n'.join(ln.strip() for ln in lines[max(0, i - 1):i + 3] if ln.strip())
            if len(block) >= 5:
                return block
    return None


# ── Line items ────────────────────────────────────────────────────────────────

def _extract_line_items(text: str) -> list[dict]:
    """
    Parse invoice line-item rows from text.

    Supports two table layouts:
    A) 4-column: Description | Qty | Unit Price | Total
    B) 3-column: Description | Unit Price | Total
    C) 2-column: Description | Amount

    We filter out rows that look like headers, totals or taxes.
    """
    items: list[dict] = []
    # Find the body — skip header (top 5 lines) and look for table rows
    body_lines = text.split('\n')

    _HEADER_SKIP = re.compile(
        r'(?:description|qty|quantity|unit\s*price|amount|total|'
        r'item|service|product|subtotal|vat|tax|discount)',
        re.IGNORECASE,
    )

    # Pattern: line has description text + 2-4 numbers at the end
    # e.g. "Web Design Services      1      500,000      500,000"
    _ITEM_RE_4 = re.compile(
        r'^(.{4,60}?)\s{2,}'                       # description (min 2 spaces gap)
        r'(\d[\d,\.\s]*)\s{2,}'                    # qty
        r'(\d[\d,\.\s]*)\s{2,}'                    # unit price
        r'(\d[\d,\.\s]*)\s*$',                     # total
        re.MULTILINE,
    )
    _ITEM_RE_3 = re.compile(
        r'^(.{4,60}?)\s{2,}'                       # description
        r'(\d[\d,\.\s]*)\s{2,}'                    # unit price
        r'(\d[\d,\.\s]*)\s*$',                     # total
        re.MULTILINE,
    )
    _ITEM_RE_2 = re.compile(
        r'^(.{4,60}?)\s{2,}'                       # description
        r'(\d[\d,\.]{3,})\s*$',                    # amount (at least 4 digits)
        re.MULTILINE,
    )

    body_text = '\n'.join(body_lines)

    # Try 4-column first
    for m in _ITEM_RE_4.finditer(body_text):
        desc = m.group(1).strip()
        if _HEADER_SKIP.match(desc):
            continue
        qty = _clean_amount(m.group(2))
        unit_price = _clean_amount(m.group(3))
        total_cell = _clean_amount(m.group(4))
        if qty is None or unit_price is None:
            continue
        if not (0 < qty <= 100_000):
            continue
        items.append({
            'description': desc,
            'quantity': qty,
            'unit_price': unit_price or (total_cell / qty if total_cell and qty else 0),
        })

    # If we got nothing, try 3-column
    if not items:
        for m in _ITEM_RE_3.finditer(body_text):
            desc = m.group(1).strip()
            if _HEADER_SKIP.match(desc):
                continue
            unit_price = _clean_amount(m.group(2))
            total_cell = _clean_amount(m.group(3))
            if unit_price is None or total_cell is None:
                continue
            # Guess qty = total / unit_price when both nonzero
            if unit_price > 0 and total_cell >= unit_price:
                qty = round(total_cell / unit_price, 2)
            else:
                qty = 1.0
            items.append({
                'description': desc,
                'quantity': qty,
                'unit_price': unit_price,
            })

    # If still nothing, try 2-column
    if not items:
        for m in _ITEM_RE_2.finditer(body_text):
            desc = m.group(1).strip()
            if _HEADER_SKIP.match(desc):
                continue
            # Skip lines that look like grand totals
            if re.search(r'\b(?:total|subtotal|vat|tax|balance)\b', desc, re.IGNORECASE):
                continue
            amount = _clean_amount(m.group(2))
            if amount and amount > 0:
                items.append({
                    'description': desc,
                    'quantity': 1.0,
                    'unit_price': amount,
                })

    return items[:50]  # cap at 50 lines


def _extract_line_items_multipage(text: str) -> list[dict]:
    """
    Split text by '=== PAGE N ===' markers emitted by ocr_service, then run
    _extract_line_items on each page independently.  This preserves table
    structure across long documents where joining all pages loses row alignment.

    Falls back to extracting from the whole text if no page markers exist.
    """
    _PAGE_MARKER = re.compile(r'^=== PAGE \d+ ===\s*$', re.MULTILINE)
    pages = _PAGE_MARKER.split(text)

    if len(pages) <= 1:
        # No page markers — single page or non-PDF; extract from full text
        return _extract_line_items(text)

    all_items: list[dict] = []
    seen: set[str] = set()
    for page_text in pages:
        if not page_text.strip():
            continue
        for item in _extract_line_items(page_text):
            key = (item.get('description', '')[:40], round(item.get('unit_price', 0), 2))
            if key not in seen:
                seen.add(key)
                all_items.append(item)

    return all_items[:50]


# ── Document type detection ───────────────────────────────────────────────────

def _detect_doc_type(text: str) -> str:
    """
    Detect document type from keyword scoring.
    Each keyword that appears adds to that type's score.
    Type with highest score wins. Ties broken by order in _DOCTYPE_KEYWORDS.
    """
    lower = text.lower()
    scores: dict[str, int] = {}
    for dtype, keywords in _DOCTYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in lower)
        if score:
            scores[dtype] = score

    if not scores:
        return 'other'

    # Return type with the highest score; proforma beats invoice if tied
    return max(scores, key=lambda k: (scores[k], list(_DOCTYPE_KEYWORDS).index(k) == 0))


# ════════════════════════════════════════════════════════════════════════════════
# Main extractor class
# ════════════════════════════════════════════════════════════════════════════════

class RuleBasedExtractor:
    """
    Extracts structured invoice/document data using only Python regex.

    Usage:
        data = RuleBasedExtractor().extract(raw_ocr_text)
        # data is a dict compatible with DocumentRecord.populate_from_extracted()
    """

    def extract(self, raw_text: str) -> dict:
        text = raw_text or ''
        result: dict = {}

        if not text.strip():
            result['document_type'] = 'other'
            result['confidence'] = 0.1
            result['notes'] = 'Empty document — nothing to extract.'
            result['suggested_action'] = 'review'
            return result

        # ── 1. Document type ───────────────────────────────────────────────
        doc_type = _detect_doc_type(text)
        result['document_type'] = doc_type

        # ── 2. Recipient block (needed to exclude it from vendor search) ───
        bill_to_block = _extract_bill_to_block(text)

        # ── 3. Vendor / supplier name ──────────────────────────────────────
        vendor = _extract_vendor_name(text, bill_to_block)
        if vendor:
            result['vendor_name'] = vendor

        # ── 4. Document reference / invoice number ─────────────────────────
        ref = _extract_reference(text)
        if ref:
            result['reference_number'] = ref

        # ── 5. Date ────────────────────────────────────────────────────────
        doc_date = _extract_dates(text)
        if doc_date:
            result['document_date'] = doc_date

        # ── 6. Amounts (total + tax) ───────────────────────────────────────
        total_amount, tax_amount = _extract_amounts(text)
        if total_amount:
            result['total_amount'] = total_amount
        if tax_amount:
            result['tax_amount'] = tax_amount

        # ── 7. Tax rate ────────────────────────────────────────────────────
        tax_rate = _extract_tax_rate(text)
        if tax_rate:
            result['tax_rate'] = tax_rate

        # ── 8. Currency ────────────────────────────────────────────────────
        currency = _extract_currency(text)
        if currency:
            result['currency'] = currency

        # ── 9. Contact fields ──────────────────────────────────────────────
        email = _extract_email(text)
        if email:
            result['contact_email'] = email

        phone = _extract_phone(text)
        if phone:
            result['contact_phone'] = phone

        # Address: prefer bill-to block for contact address
        address = _extract_address(text, bill_to_block)
        if address:
            result['contact_address'] = address

        # Contact name: from bill-to block if present
        if bill_to_block:
            lines = [ln.strip() for ln in bill_to_block.split('\n') if ln.strip()]
            if lines:
                result['contact_name'] = lines[0]

        # For CVs: candidate's name
        if doc_type == 'cv':
            name = _extract_contact_name(text)
            if name:
                result['contact_name'] = name

        # ── 10. VAT / TIN ──────────────────────────────────────────────────
        vat = _extract_vat_tin(text)
        if vat:
            result['vat_number'] = vat

        # ── 11. Banking ────────────────────────────────────────────────────
        iban = _extract_iban(text)
        if iban:
            result['iban'] = iban

        swift = _extract_swift(text)
        if swift:
            result['swift'] = swift

        # ── 12. Line items (invoice / proforma / receipt) — per-page ──────
        if doc_type in ('invoice', 'proforma', 'receipt'):
            items = _extract_line_items_multipage(text)
            if items:
                result['line_items'] = items

        # ── 13. Suggested action & confidence ──────────────────────────────
        result['suggested_action'] = _DOCTYPE_ACTION.get(doc_type, 'review')
        result['confidence'] = _estimate_confidence(result, doc_type)
        result['notes'] = (
            f'Extracted with rule-based patterns (Tier 1 — no AI required). '
            f'Fields found: {", ".join(k for k in result if k not in ("confidence","notes","suggested_action","document_type"))}.'
        )

        _logger.info(
            'Rule-based extraction: type=%s fields=%d confidence=%.0f%%',
            doc_type,
            len([k for k in result if result[k]]),
            result['confidence'] * 100,
        )
        return result


# ── Confidence scoring ────────────────────────────────────────────────────────

def _estimate_confidence(result: dict, doc_type: str) -> float:
    """
    Score 0.0-1.0 based on which fields were found.

    Key fields by document type:
    - invoice/proforma: vendor, date, total, reference    → 4 pts
    - receipt:          vendor, date, total               → 3 pts
    - cv:               contact_name, email, phone        → 3 pts
    - contract:         vendor, date, reference           → 3 pts
    """
    key_map = {
        'invoice':  ['vendor_name', 'document_date', 'total_amount', 'reference_number'],
        'proforma': ['vendor_name', 'document_date', 'total_amount', 'reference_number'],
        'receipt':  ['vendor_name', 'document_date', 'total_amount'],
        'cv':       ['contact_name', 'contact_email', 'contact_phone'],
        'contract': ['vendor_name', 'document_date', 'reference_number'],
        'other':    ['vendor_name', 'document_date', 'total_amount'],
    }
    bonus_map = {
        'invoice':  ['vat_number', 'tax_amount', 'contact_email', 'line_items'],
        'proforma': ['line_items', 'contact_email', 'contact_phone'],
        'receipt':  ['contact_email', 'vat_number'],
        'cv':       ['contact_address', 'vendor_name'],
        'contract': ['contact_name', 'total_amount'],
        'other':    ['reference_number'],
    }

    keys = key_map.get(doc_type, key_map['other'])
    bonus = bonus_map.get(doc_type, [])

    found_keys = sum(1 for k in keys if result.get(k))
    found_bonus = sum(1 for k in bonus if result.get(k))

    base = found_keys / max(len(keys), 1)           # 0.0 – 1.0
    boost = min(found_bonus * 0.05, 0.20)           # up to +0.20
    raw = base * 0.80 + boost                        # max ≈ 1.0
    return round(min(raw, 1.0), 2)
