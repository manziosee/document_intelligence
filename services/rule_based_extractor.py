"""
Tier 1 — Rule-Based Extractor
==============================
Zero dependencies beyond the Python standard library.
No API key, no internet connection, no cost — ever.

How it works
------------
1. Detect document type from keyword scoring
2. Run field extractors in priority order:
   - Labelled patterns first  (e.g. "Total: 1,234,000 RWF")
   - Structural patterns next (e.g. largest number on the page)
   - Positional heuristics last (e.g. first company-looking line)
3. Estimate confidence from how many key fields were found
4. Return a dict that DocumentRecord.populate_from_extracted() understands
"""
import re
import logging
from datetime import datetime, date

_logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════════
# Number pattern — matches ALL common formats
# ════════════════════════════════════════════════════════════════════════════════
# Examples:
#   1,234,567   1 234 567   1.234.567   1234567   1,234.56   500   99   0.50
# The \d+ fallback at the end is essential — it catches plain integers of any
# size including small amounts like 500, 99, etc. without thousands separators.
_NUM = r'\d{1,3}(?:[,\.\s]\d{3})+(?:[,\.]\d{1,2})?|\d+'

# ── Currency codes ─────────────────────────────────────────────────────────────
_CURRENCY_CODES = r'(?:RWF|Rwf|FRW|Frw|RFw|USD|EUR|GBP|KES|TZS|UGX|CHF|JPY|CNY|AUD|CAD|ZAR|NGN|GHS)'
_CURRENCY_SYMBOLS = r'(?:\$|€|£|¥|₦|₵)'
_CURRENCY_RE = re.compile(
    r'\b(RWF|USD|EUR|GBP|KES|TZS|UGX|CHF|JPY|CNY|AUD|CAD|ZAR|NGN|GHS|Frw|FRW)\b',
    re.IGNORECASE,
)

# ── Labelled total patterns — highest priority ─────────────────────────────────
_TOTAL_LABEL_PATTERNS = [
    # "Grand Total: RWF 1,234,000" / "Total Amount: 1 234 000" / "Total TTC: 500"
    # (?<!\w) prevents matching "total" inside "Subtotal"
    rf'(?<!\w)(?:grand\s*total|total\s*(?:amount|due|payable|ttc|ht|net)?|'
    rf'net\s*(?:total|payable|amount)?|amount\s*(?:due|payable|to\s*pay)?|'
    rf'balance\s*(?:due|payable)?|montant\s*total|total\s*facture|'
    rf'total\s*à\s*payer|somme\s*totale)\s*[:\-]?\s*'
    rf'(?:{_CURRENCY_CODES})?\s*({_NUM})',

    # "RWF 1,234,000" — currency before amount (common on Rwandan docs)
    rf'(?:{_CURRENCY_CODES})\s+({_NUM})',

    # "1,234,000 RWF" — currency after amount
    rf'({_NUM})\s*(?:{_CURRENCY_CODES})',

    # Symbol + amount: "$1,234.56"
    rf'(?:{_CURRENCY_SYMBOLS})\s*({_NUM})',
]

# ── Subtotal / net patterns ────────────────────────────────────────────────────
_SUBTOTAL_PATTERNS = [
    rf'(?:sub\s*total|subtotal|net\s*(?:amount)?|h\.?t\.?|'
    rf'excl\.?\s*(?:vat|tax)|before\s*(?:vat|tax))\s*[:\-]?\s*'
    rf'(?:{_CURRENCY_CODES})?\s*({_NUM})',
]

# ── Tax patterns ───────────────────────────────────────────────────────────────
_TAX_LABEL_PATTERNS = [
    # "VAT 18%: 123,456" / "TVA (18%): RWF 123,456" / "VAT Amount: 18,000"
    rf'(?:vat|tva|t\.v\.a\.?|tax)\s*(?:@|at|de|:)?\s*\d{{1,2}}(?:[,\.]\d{{1,2}})?\s*%'
    rf'\s*[:\-]?\s*(?:{_CURRENCY_CODES})?\s*({_NUM})',
    rf'(?:vat|tva|tax)\s*(?:amount|total|montant|charge)?\s*[:\-]?\s*'
    rf'(?:{_CURRENCY_CODES})?\s*({_NUM})',
]

_TAX_RATE_PATTERN = re.compile(
    r'(?:vat|tva|tax(?:e)?)\s*(?:rate|taux|@|at)?\s*[:\-]?\s*(\d{1,2}(?:[,\.]\d{1,2})?)\s*%',
    re.IGNORECASE,
)

# ── Date patterns ──────────────────────────────────────────────────────────────
_MONTH_LONG = (
    r'January|February|March|April|May|June|July|August|'
    r'September|October|November|December|'
    r'Janvier|Février|Fevrier|Mars|Avril|Mai|Juin|Juillet|Août|Aout|'
    r'Septembre|Octobre|Novembre|Décembre|Decembre'
)
_MONTH_SHORT = r'Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec'

_DATE_LABELED_PATTERNS = [
    rf'(?:invoice\s*date|bill\s*date|document\s*date|date\s*(?:of\s*invoice|'
    rf'issued|d\'?émission|d\'?emission)?|issued\s*(?:on|date)?|'
    rf'date\s*[:\-])\s*'
    rf'(\d{{1,2}}[/\-\.]\d{{1,2}}[/\-\.]\d{{4}}|\d{{4}}[/\-]\d{{2}}[/\-]\d{{2}}|'
    rf'\d{{1,2}}\s+(?:{_MONTH_LONG}|{_MONTH_SHORT})\.?\s+\d{{4}})',
]

_DATE_BARE_PATTERNS = [
    r'\b(\d{4}[-/]\d{1,2}[-/]\d{1,2})\b',                        # ISO: 2024-01-15
    r'\b(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4})\b',                  # DMY: 15/01/2024
    r'\b(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2})\b',                  # DMY short: 15/01/24
    rf'\b(\d{{1,2}}\s+(?:{_MONTH_LONG})\s+\d{{4}})\b',            # 15 January 2024
    rf'\b((?:{_MONTH_LONG})\s+\d{{1,2}},?\s+\d{{4}})\b',          # January 15, 2024
    rf'\b(\d{{1,2}}\s+(?:{_MONTH_SHORT})\.?\s+\d{{4}})\b',        # 15 Jan 2024
]

# ── Reference / Invoice number ─────────────────────────────────────────────────
_REF_LABEL_PATTERNS = [
    # Labelled: "Invoice No: INV-2024-001" / "Ref: REF/001" / "N°: 12345"
    rf'(?:invoice\s*(?:no\.?|number|#|num\.?|n°|numéro)|'
    rf'inv\.?\s*(?:no\.?|#|num\.?)|'
    rf'ref(?:erence)?\s*(?:no\.?|#|num\.?|n°)?|'
    rf'bill\s*(?:no\.?|#|number)|'
    rf'receipt\s*(?:no\.?|#|number)|'
    rf'order\s*(?:no\.?|#|number)|'
    rf'quotation\s*(?:no\.?|#|number)|'
    rf'devis\s*(?:no\.?|#|n°)?|'
    rf'facture\s*(?:no\.?|#|n°)?|'
    rf'n°\s*(?:facture)?|'
    rf'doc(?:ument)?\s*(?:no\.?|#|number))\s*[:\-]?\s*'
    rf'([A-Za-z0-9][A-Za-z0-9\/\-\._]{{2,39}})',

    # Standalone: "No. INV2024001" / "#2024/001"
    r'(?:No\.?|N°|#)\s+([A-Za-z0-9][A-Za-z0-9\/\-\._]{2,29})\b',
]

# ── Email ──────────────────────────────────────────────────────────────────────
_EMAIL_RE = re.compile(
    r'\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b',
)

# ── Phone ──────────────────────────────────────────────────────────────────────
_PHONE_PATTERNS = [
    r'(?:tel(?:ephone)?|phone|mob(?:ile)?|cell(?:ulaire)?|'
    r'contact|portable|tél|gsm)\s*[:\-]?\s*(\+?[\d][\d\s\-\(\)\.]{6,19}\d)',
    r'\b(\+?250\s*7\d[\d\s]{7,10})\b',       # Rwanda +250 7XX XXX XXX
    r'\b(07[2-9][\d\s\-]{7,9})\b',           # Rwanda 07X XXXXXXX
    r'\b(\+[1-9]\d{1,3}[\s\-]?\d{3,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4})\b',  # International
]

# ── VAT / TIN ──────────────────────────────────────────────────────────────────
_VAT_PATTERNS = [
    r'(?:TIN|VAT\s*(?:reg(?:istration)?\s*)?(?:no\.?|#|n°|num\.?)|'
    r'Tax\s*(?:ID|identification|reg(?:istration)?)|TVA\s*(?:no\.?|n°)|'
    r'Numéro?\s*(?:TVA|fiscal)|tax\s*number)\s*[:\-]?\s*([A-Z0-9\-]{5,20})',
    r'\bTIN\s*[:\-]?\s*(\d{9})\b',
    r'\b(\d{9})\s*(?:TIN|RRA)\b',
]

# ── IBAN / SWIFT ───────────────────────────────────────────────────────────────
_IBAN_RE = re.compile(r'\b([A-Z]{2}\d{2}[A-Z0-9]{4,30})\b')
_SWIFT_RE = re.compile(
    r'\b(?:SWIFT|BIC|code\s*(?:banque)?)\s*[:\-]?\s*([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b',
    re.IGNORECASE,
)
_SWIFT_BARE_RE = re.compile(r'\b([A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b')

# ── Document type ──────────────────────────────────────────────────────────────
_DOCTYPE_KEYWORDS: dict[str, list[str]] = {
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

# ── Company suffixes ───────────────────────────────────────────────────────────
_COMPANY_SUFFIXES = re.compile(
    r'\b(?:ltd\.?|limited|inc\.?|incorporated|corp\.?|corporation|'
    r'sarl|s\.a\.r\.l\.?|s\.a\.?|plc\.?|llc\.?|gmbh|sas|pty\.?|'
    r'co\.?\s*ltd\.?|company|enterprise|enterprises|group|holding)\b',
    re.IGNORECASE,
)

# Words that disqualify a line as a vendor name
_VENDOR_SKIP = re.compile(
    r'^\s*(?:invoice|receipt|proforma|pro.forma|quotation|quote|contract|'
    r'bill\s+to|invoice\s+to|sold\s+to|ship\s+to|attention|attn|'
    r'tel(?:ephone)?|phone|mob(?:ile)?|fax|email|'
    r'p\.?o\.?\s*box|po\s+box|'
    r'ref(?:erence)?(?:\s*(?:no\.?|#|num))?|'
    r'invoice\s*(?:no\.?|#|number|date)?|'
    r'receipt\s*(?:no\.?|#|number|date)?|'
    r'bill\s*(?:no\.?|#|number|date)?|'
    r'number|no\.|page|vat|tin|date|'
    r'total|sub\s*total|amount|due|payable|balance|'
    r'thank\s+you|regards|sincerely)\b',
    re.IGNORECASE,
)

# "From:", "Supplier:", etc. — explicit vendor labels to strip the prefix
_VENDOR_LABEL_RE = re.compile(
    r'^(?:from|supplier|vendeur|fournisseur|seller|sold\s+by|'
    r'issued\s+by|billed\s+by|service\s+provider|vendor|by)\s*[:\-]\s*',
    re.IGNORECASE,
)

# ── Address keywords ───────────────────────────────────────────────────────────
_ADDRESS_KEYWORDS = re.compile(
    r'\b(?:street|avenue|road|lane|drive|blvd|boulevard|'
    r'kg|kn|kk|kg\s*\d|kn\s*\d|km\s*\d|'
    r'p\.?o\.?\s*box|po\s+box|bp\s+\d|b\.p\.|'
    r'district|sector|cell|village|'
    r'kigali|rwanda|nairobi|kampala|dar\s+es\s+salaam|'
    r'plot\s+no|house\s+no|flat\s+no|floor)\b',
    re.IGNORECASE,
)


# ════════════════════════════════════════════════════════════════════════════════
# Number parsing helpers
# ════════════════════════════════════════════════════════════════════════════════

def _clean_amount(raw: str) -> float | None:
    """
    Normalise various number formats to a Python float.

    Handles all common formats:
      1,234,567     → 1234567.0    (comma thousands)
      1 234 567     → 1234567.0    (space thousands)
      1.234.567     → 1234567.0    (dot thousands)
      1,234.56      → 1234.56      (comma thousands, dot decimal)
      1.234,56      → 1234.56      (dot thousands, comma decimal)
      1234567       → 1234567.0    (bare integer)
      500           → 500.0        (small bare integer)
    """
    if not raw:
        return None
    s = str(raw).strip()
    # Remove all spaces used as thousands separators
    s = re.sub(r'\s', '', s)

    # European format "1.234.567,89"
    if re.match(r'^\d{1,3}(?:\.\d{3})+,\d{1,2}$', s):
        s = s.replace('.', '').replace(',', '.')
    # Anglo format "1,234,567.89" or "1,234,567"
    elif re.match(r'^\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?$', s):
        s = s.replace(',', '')
    # Multiple dots as thousands "1.234.567"
    elif re.match(r'^\d{1,3}(?:\.\d{3})+$', s):
        s = s.replace('.', '')
    # Single comma — decimal or thousands?
    elif ',' in s and '.' not in s:
        parts = s.split(',')
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            s = parts[0] + '.' + parts[1]   # "1,23" → 1.23
        else:
            s = s.replace(',', '')           # "1,234" → 1234
    else:
        s = s.replace(',', '')

    try:
        val = float(s)
        return val if val >= 0 else None
    except ValueError:
        return None


def _parse_date(raw: str) -> str | None:
    """Convert any recognised date string to ISO YYYY-MM-DD. Returns None on failure."""
    raw = raw.strip()

    # ISO 2024-01-15
    m = re.match(r'^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$', raw)
    if m:
        return _validate_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # DMY 15/01/2024
    m = re.match(r'^(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})$', raw)
    if m:
        return _validate_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))

    # DMY short 15/01/24
    m = re.match(r'^(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{2})$', raw)
    if m:
        y = int(m.group(3))
        full_year = 2000 + y if y <= 30 else 1900 + y
        return _validate_date(full_year, int(m.group(2)), int(m.group(1)))

    # Named month formats
    for fmt in (
        '%d %B %Y', '%d %b %Y', '%B %d, %Y', '%B %d %Y',
        '%b %d, %Y', '%b %d %Y', '%d %B %Y', '%d %b. %Y',
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            return _validate_date(dt.year, dt.month, dt.day)
        except ValueError:
            continue

    # French month names
    french_map = {
        'janvier': 'january', 'février': 'february', 'fevrier': 'february',
        'mars': 'march', 'avril': 'april', 'mai': 'may', 'juin': 'june',
        'juillet': 'july', 'août': 'august', 'aout': 'august',
        'septembre': 'september', 'octobre': 'october',
        'novembre': 'november', 'décembre': 'december', 'decembre': 'december',
        'janv': 'jan', 'févr': 'feb', 'avr': 'apr', 'juil': 'jul', 'déc': 'dec',
    }
    lower = raw.lower()
    for fr, en in french_map.items():
        if fr in lower:
            lower = lower.replace(fr, en)
            break
    if lower != raw.lower():
        result = _parse_date(lower.title())
        if result:
            return result

    return None


def _validate_date(y: int, mo: int, d: int) -> str | None:
    try:
        dt = date(y, mo, d)
        today = date.today()
        if date(today.year - 15, 1, 1) <= dt <= date(today.year + 2, 12, 31):
            return dt.strftime('%Y-%m-%d')
    except ValueError:
        pass
    return None


def _first_match(patterns, text: str) -> str | None:
    """Return first capturing group from first matching pattern."""
    if isinstance(patterns, str):
        patterns = [patterns]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1).strip()
    return None


def _all_amounts_in_text(text: str) -> list[float]:
    """Extract all plausible monetary amounts — those tagged with a currency sign."""
    amounts = []
    for m in re.finditer(
        rf'(?:{_CURRENCY_CODES}|{_CURRENCY_SYMBOLS})\s*({_NUM})|'
        rf'({_NUM})\s*(?:{_CURRENCY_CODES})',
        text, re.IGNORECASE,
    ):
        raw = m.group(1) or m.group(2)
        if raw:
            val = _clean_amount(raw)
            if val and val > 0:
                amounts.append(val)
    return amounts


# ════════════════════════════════════════════════════════════════════════════════
# Field extractors
# ════════════════════════════════════════════════════════════════════════════════

def _extract_amounts(text: str) -> tuple[float | None, float | None]:
    """
    Return (total_amount, tax_amount).

    Strategy:
    1. Labelled total patterns — most reliable
    2. Labelled tax patterns
    3. Fallback: currency-tagged numbers, pick largest as total
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
            # If we got a total from fallback, check subtotals for tax estimate
            if tax is None:
                for pat in _SUBTOTAL_PATTERNS:
                    raw = _first_match(pat, text)
                    if raw:
                        sub = _clean_amount(raw)
                        if sub and sub > 0 and sub < total:
                            # tax ≈ total - subtotal
                            diff = round(total - sub, 2)
                            if diff > 0:
                                tax = diff
                            break

    # Sanity: tax must be less than total
    if total and tax and tax >= total:
        tax = None

    return total, tax


def _extract_dates(text: str) -> str | None:
    """Return the most reliable date found."""
    # 1. Labelled dates first
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
            # Reject if it looks like a plain date
            if re.match(r'^\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}$', ref):
                continue
            if re.match(r'^\d{4}[-/]\d{2}[-/]\d{2}$', ref):
                continue
            # Reject bare 9-digit Rwanda TIN
            if re.match(r'^\d{9}$', ref):
                continue
            if len(ref) >= 2:
                return ref
    return None


def _extract_currency(text: str) -> str | None:
    """Return ISO currency code."""
    m = _CURRENCY_RE.search(text)
    if m:
        code = m.group(1).upper()
        return 'RWF' if code in ('FRW', 'FRw', 'Frw') else code
    if re.search(r'\b(?:Rwanda|Kigali|RRA|Frw|FRW)\b', text, re.IGNORECASE):
        return 'RWF'
    return None


def _extract_email(text: str) -> str | None:
    m = _EMAIL_RE.search(text)
    return m.group(1).lower() if m else None


def _extract_phone(text: str) -> str | None:
    for pat in _PHONE_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            phone = m.group(1).strip()
            phone = re.sub(r'\s+', ' ', phone)
            if len(re.sub(r'\D', '', phone)) >= 7:
                return phone
    return None


def _extract_vat_tin(text: str) -> str | None:
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
        if len(candidate) >= 15:
            return candidate
    return None


def _extract_swift(text: str) -> str | None:
    m = _SWIFT_RE.search(text)
    if m:
        return m.group(1)
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
    m2 = re.search(
        r'(?:vat|tva|tax)\b[^%]{0,40}?(\d{1,2}(?:[,\.]\d{1,2})?)\s*%',
        text, re.IGNORECASE,
    )
    if m2:
        return _clean_amount(m2.group(1))
    return None


# ── Vendor / supplier name ────────────────────────────────────────────────────

def _extract_vendor_name(text: str, bill_to_block: str | None) -> str | None:
    """
    Find the supplier/vendor company name.

    Priority:
    1. Explicit "From:" / "Supplier:" label — strip the label, return the name
    2. First line with a recognised company suffix (Ltd, SARL, …)
    3. First ALL-CAPS multi-word line in the top section
    4. First non-trivial line that doesn't look like a header/label
    """
    lines = [ln.strip() for ln in text.split('\n') if ln.strip()]

    skip_lines: set[str] = set()
    if bill_to_block:
        for ln in bill_to_block.split('\n'):
            skip_lines.add(ln.strip())

    # 1. Explicit vendor label — strip the label prefix
    m = re.search(
        r'(?:from|supplier|vendeur|fournisseur|seller|sold\s+by|issued\s+by|'
        r'billed\s+by|service\s+provider|vendor|by)\s*[:\-]\s*(.{3,80})',
        text, re.IGNORECASE,
    )
    if m:
        candidate = m.group(1).strip().split('\n')[0].strip()
        if len(candidate) >= 3:
            return candidate

    # 2. Scan top 30 lines for company suffix — filter noise first
    for line in lines[:30]:
        if line in skip_lines:
            continue
        if len(line) < 3 or len(line) > 120:
            continue
        # Skip lines starting with a label keyword
        if _VENDOR_SKIP.match(line):
            continue
        # Skip lines that are mostly digits
        if sum(c.isdigit() for c in line) / max(len(line), 1) > 0.45:
            continue
        # Skip email lines
        if _EMAIL_RE.search(line):
            continue
        # Skip lines with phone-number-like patterns
        if re.search(r'\+?\d[\d\s\-\(\)\.]{7,}', line):
            continue
        # Skip lines that look like addresses
        if _ADDRESS_KEYWORDS.search(line):
            continue
        # Match company suffix
        if _COMPANY_SUFFIXES.search(line):
            # Strip any leading label like "Company: " or "Vendor: "
            clean = _VENDOR_LABEL_RE.sub('', line).strip()
            return clean or line

    # 3. First ALL-CAPS multi-word line
    for line in lines[:20]:
        if line in skip_lines:
            continue
        if _VENDOR_SKIP.match(line):
            continue
        words = line.split()
        if (
            len(words) >= 2
            and line.upper() == line
            and all(re.match(r'^[A-Z0-9&\-\.\,\'\(\)\s]+$', w) for w in words)
            and not _EMAIL_RE.search(line)
            and not re.search(r'\+?\d[\d\s\-\.]{7,}', line)
        ):
            return line

    # 4. First non-trivial line (liberal fallback)
    for line in lines[:15]:
        if line in skip_lines:
            continue
        if _VENDOR_SKIP.match(line):
            continue
        if (
            len(line) >= 4
            and not re.match(r'^\d', line)
            and not _EMAIL_RE.search(line)
            and any(c.isalpha() for c in line)
            and not re.search(r'^\s*\d{1,2}[/\-\.]\d{1,2}[/\-\.]', line)  # not a date
        ):
            clean = _VENDOR_LABEL_RE.sub('', line).strip()
            return clean or line

    return None


# ── Bill-to block ─────────────────────────────────────────────────────────────

def _extract_bill_to_block(text: str) -> str | None:
    m = re.search(
        r'(?:bill\s+to|invoice\s+to|sold\s+to|ship\s+to|attention|attn|'
        r'client|customer|to\s*:)\s*[:\-]?\s*\n?((?:.+\n?){1,5})',
        text, re.IGNORECASE,
    )
    return m.group(1).strip() if m else None


def _extract_contact_name(text: str) -> str | None:
    """For CV documents: find full name in top lines."""
    lines = [ln.strip() for ln in text.split('\n') if ln.strip()]
    for line in lines[:10]:
        words = line.split()
        if (
            2 <= len(words) <= 4
            and all(
                len(w) >= 2 and w[0].isupper() and re.match(r'^[A-Za-zÀ-ÿ\-\']+$', w)
                for w in words
            )
        ):
            return line
    return None


def _extract_address(text: str, label_context: str | None = None) -> str | None:
    search_in = label_context or text
    lines = search_in.split('\n')
    for i, line in enumerate(lines):
        if _ADDRESS_KEYWORDS.search(line):
            block = '\n'.join(
                ln.strip() for ln in lines[max(0, i - 1):i + 3] if ln.strip()
            )
            if len(block) >= 5:
                return block
    return None


# ── Line items ────────────────────────────────────────────────────────────────

def _extract_line_items(text: str) -> list[dict]:
    """
    Parse invoice line-item rows.

    Tries four layouts in order:
    A) Tab-delimited  (DOCX tables, some exported PDFs)
    B) 4-column space-delimited
    C) 3-column space-delimited
    D) 2-column space-delimited
    """
    _SKIP_ROW = re.compile(
        r'^\s*(?:description|item\s*(?:no\.?)?|service|product|particulars|'
        r'qty|quantity|unit\s*price|unit\s*cost|rate|'
        r'amount|total|sub\s*total|vat|tax|discount|s\.?no\.?|#|page)\s*$',
        re.IGNORECASE,
    )
    _TOTAL_ROW = re.compile(
        r'\b(?:grand\s*total|sub\s*total|subtotal|net\s*total|'
        r'total\s*(?:amount|due|payable)?|balance\s*due|vat|tax|discount)\b',
        re.IGNORECASE,
    )

    def _valid_desc(desc: str) -> bool:
        if not desc or len(desc) < 2:
            return False
        if _SKIP_ROW.match(desc.strip()):
            return False
        if _TOTAL_ROW.search(desc):
            return False
        digit_ratio = sum(c.isdigit() for c in desc) / max(len(desc), 1)
        return digit_ratio < 0.65

    items: list[dict] = []

    # ── Layout A: tab-delimited ───────────────────────────────────────────────
    for line in text.splitlines():
        if '\t' not in line:
            continue
        cols = [c.strip() for c in line.split('\t') if c.strip()]
        # Deduplicate adjacent identical cells (merged cells in python-docx)
        deduped = []
        for c in cols:
            if not deduped or c != deduped[-1]:
                deduped.append(c)
        cols = deduped
        if len(cols) < 2:
            continue
        desc = cols[0]
        if not _valid_desc(desc):
            continue
        nums = [_clean_amount(c) for c in cols[1:]]
        nums = [n for n in nums if n is not None and n >= 0]
        if not nums:
            continue
        if len(nums) >= 3:
            qty = nums[0] if 0 < nums[0] <= 100_000 else 1.0
            unit_price = nums[1]
        elif len(nums) == 2:
            total_c = nums[1]
            unit_price = nums[0]
            qty = round(total_c / unit_price, 2) if unit_price > 0 else 1.0
        else:
            unit_price = nums[0]
            qty = 1.0
        items.append({'description': desc, 'quantity': qty, 'unit_price': unit_price})

    if items:
        return items[:50]

    # ── Layouts B/C/D: space-delimited ────────────────────────────────────────
    _SEP = r'(?:\t|\s{2,})'  # accept tab OR 2+ spaces as column separator

    _ROW4 = re.compile(
        r'^(.{3,70}?)' + _SEP + r'(\d[\d,\.\s]{0,15})' + _SEP +
        r'(\d[\d,\.\s]{0,15})' + _SEP + r'(\d[\d,\.\s]{0,15})\s*$',
        re.MULTILINE,
    )
    _ROW3 = re.compile(
        r'^(.{3,70}?)' + _SEP + r'(\d[\d,\.\s]{0,15})' + _SEP +
        r'(\d[\d,\.\s]{0,15})\s*$',
        re.MULTILINE,
    )
    _ROW2 = re.compile(
        r'^(.{3,70}?)' + _SEP + r'(\d[\d,\.]{3,})\s*$',
        re.MULTILINE,
    )

    for m in _ROW4.finditer(text):
        desc = m.group(1).strip()
        if not _valid_desc(desc):
            continue
        qty = _clean_amount(m.group(2))
        unit_price = _clean_amount(m.group(3))
        if not qty or not unit_price:
            continue
        qty = qty if 0 < qty <= 100_000 else 1.0
        items.append({'description': desc, 'quantity': qty, 'unit_price': unit_price})

    if not items:
        for m in _ROW3.finditer(text):
            desc = m.group(1).strip()
            if not _valid_desc(desc):
                continue
            up = _clean_amount(m.group(2))
            tot = _clean_amount(m.group(3))
            if not up or not tot:
                continue
            qty = round(tot / up, 2) if up > 0 and tot >= up else 1.0
            items.append({'description': desc, 'quantity': qty, 'unit_price': up})

    if not items:
        for m in _ROW2.finditer(text):
            desc = m.group(1).strip()
            if not _valid_desc(desc):
                continue
            amt = _clean_amount(m.group(2))
            if amt and amt > 0:
                items.append({'description': desc, 'quantity': 1.0, 'unit_price': amt})

    return items[:50]


def _extract_line_items_multipage(text: str) -> list[dict]:
    """
    Split text by '=== PAGE N ===' markers, run _extract_line_items per page.
    Falls back to full-text extraction when no markers.
    """
    _PAGE_MARKER = re.compile(r'^=== PAGE \d+ ===\s*$', re.MULTILINE)
    pages = _PAGE_MARKER.split(text)

    if len(pages) <= 1:
        return _extract_line_items(text)

    all_items: list[dict] = []
    seen: set[tuple] = set()
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
    lower = text.lower()
    scores: dict[str, int] = {}
    for dtype, keywords in _DOCTYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in lower)
        if score:
            scores[dtype] = score

    if not scores:
        return 'other'

    return max(scores, key=lambda k: (scores[k], list(_DOCTYPE_KEYWORDS).index(k) == 0))


# ════════════════════════════════════════════════════════════════════════════════
# Main extractor class
# ════════════════════════════════════════════════════════════════════════════════

class RuleBasedExtractor:
    """
    Extracts structured document data using only Python regex.

    Usage:
        data = RuleBasedExtractor().extract(raw_ocr_text)
    """

    def extract(self, raw_text: str) -> dict:
        text = raw_text or ''
        result: dict = {}

        if not text.strip():
            return {
                'document_type': 'other',
                'confidence': 0.1,
                'notes': 'Empty document — no text to extract.',
                'suggested_action': 'review',
            }

        # 1. Document type
        doc_type = _detect_doc_type(text)
        result['document_type'] = doc_type

        # 2. Recipient block (needed to exclude it from vendor search)
        bill_to_block = _extract_bill_to_block(text)

        # 3. Vendor / supplier name
        vendor = _extract_vendor_name(text, bill_to_block)
        if vendor:
            result['vendor_name'] = vendor

        # 4. Reference / invoice number
        ref = _extract_reference(text)
        if ref:
            result['reference_number'] = ref

        # 5. Date
        doc_date = _extract_dates(text)
        if doc_date:
            result['document_date'] = doc_date

        # 6. Amounts (total + tax)
        total_amount, tax_amount = _extract_amounts(text)
        if total_amount and total_amount > 0:
            result['total_amount'] = total_amount
        if tax_amount and tax_amount > 0:
            result['tax_amount'] = tax_amount

        # 7. Tax rate
        tax_rate = _extract_tax_rate(text)
        if tax_rate:
            result['tax_rate'] = tax_rate

        # 8. Currency
        currency = _extract_currency(text)
        if currency:
            result['currency'] = currency

        # 9. Contact fields
        email = _extract_email(text)
        if email:
            result['contact_email'] = email

        phone = _extract_phone(text)
        if phone:
            result['contact_phone'] = phone

        address = _extract_address(text, bill_to_block)
        if address:
            result['contact_address'] = address

        if bill_to_block:
            lines = [ln.strip() for ln in bill_to_block.split('\n') if ln.strip()]
            if lines:
                result['contact_name'] = lines[0]

        if doc_type == 'cv':
            name = _extract_contact_name(text)
            if name:
                result['contact_name'] = name

        # 10. VAT / TIN
        vat = _extract_vat_tin(text)
        if vat:
            result['vat_number'] = vat

        # 11. Banking
        iban = _extract_iban(text)
        if iban:
            result['iban'] = iban

        swift = _extract_swift(text)
        if swift:
            result['swift'] = swift

        # 12. Line items
        if doc_type in ('invoice', 'proforma', 'receipt'):
            items = _extract_line_items_multipage(text)
            if items:
                result['line_items'] = items
                # Derive total from line items if not already found
                if not result.get('total_amount'):
                    computed = sum(
                        (i.get('unit_price', 0) or 0) * (i.get('quantity', 1) or 1)
                        for i in items
                    )
                    if computed > 0:
                        result['total_amount'] = round(computed, 2)

        # 13. Suggested action and confidence
        result['suggested_action'] = _DOCTYPE_ACTION.get(doc_type, 'review')
        result['confidence'] = _estimate_confidence(result, doc_type)
        found_fields = [k for k in result if k not in ('confidence', 'notes', 'suggested_action', 'document_type')]
        result['notes'] = (
            f'Rule-based extraction (Tier 1 — no AI). '
            f'Fields found: {", ".join(found_fields) if found_fields else "none"}.'
        )

        _logger.info(
            'Rule-based: type=%s fields=%d confidence=%.0f%%',
            doc_type, len(found_fields), result['confidence'] * 100,
        )
        return result


# ── Confidence scoring ────────────────────────────────────────────────────────

def _estimate_confidence(result: dict, doc_type: str) -> float:
    key_map = {
        'invoice':          ['vendor_name', 'document_date', 'total_amount', 'reference_number'],
        'proforma':         ['vendor_name', 'document_date', 'total_amount', 'reference_number'],
        'receipt':          ['vendor_name', 'document_date', 'total_amount'],
        'cv':               ['contact_name', 'contact_email', 'contact_phone'],
        'contract':         ['vendor_name', 'document_date', 'reference_number'],
        'proof_of_payment': ['vendor_name', 'document_date', 'total_amount'],
        'other':            ['vendor_name', 'document_date', 'total_amount'],
    }
    bonus_map = {
        'invoice':          ['vat_number', 'tax_amount', 'contact_email', 'line_items'],
        'proforma':         ['line_items', 'contact_email', 'contact_phone'],
        'receipt':          ['contact_email', 'vat_number'],
        'cv':               ['contact_address', 'vendor_name'],
        'contract':         ['contact_name', 'total_amount'],
        'proof_of_payment': ['reference_number', 'contact_email'],
        'other':            ['reference_number'],
    }

    keys = key_map.get(doc_type, key_map['other'])
    bonus = bonus_map.get(doc_type, [])

    found_keys = sum(1 for k in keys if result.get(k))
    found_bonus = sum(1 for k in bonus if result.get(k))

    base = found_keys / max(len(keys), 1)
    boost = min(found_bonus * 0.05, 0.20)
    return round(min(base * 0.80 + boost, 1.0), 2)
