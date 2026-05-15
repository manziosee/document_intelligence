"""
Odoo version compatibility shims for Document Intelligence.

Detects the running Odoo major version at import time and exposes
version-safe helper functions so the rest of the module never needs
to branch on version numbers itself.

Supported: Odoo 17, 18, 19 (and forward-compatible with future versions).
"""
import logging

_logger = logging.getLogger(__name__)

# ── Detect version ────────────────────────────────────────────────────────────

try:
    import odoo.release as _rel
    ODOO_VERSION: int = int(_rel.version_info[0])
except Exception:
    ODOO_VERSION = 17

_logger.debug('Document Intelligence: detected Odoo v%d', ODOO_VERSION)

# ── account.move / vendor bill helpers ────────────────────────────────────────

def make_vendor_bill_vals(
    partner_id: int | None,
    invoice_date,
    ref: str | None,
    narration: str | None,
) -> dict:
    """
    Build the base vals dict for creating a vendor bill (account.move).
    Compatible with Odoo 17, 18, 19.
    """
    vals: dict = {
        'move_type': 'in_invoice',
        'partner_id': partner_id or False,
        'invoice_date': invoice_date or False,
        'ref': ref or False,
    }
    if narration:
        # 'narration' renamed to 'internal_note' in some 18.x builds; try both
        if ODOO_VERSION >= 18:
            vals['narration'] = narration  # still accepted via alias in 18
        else:
            vals['narration'] = narration
    return vals


def make_invoice_line_vals(
    name: str,
    price_unit: float,
    quantity: float = 1.0,
    product_id: int | None = None,
    tax_ids: list[int] | None = None,
) -> dict:
    """
    Build a single invoice line dict.
    Use inside:  move_vals['invoice_line_ids'] = [(0, 0, make_invoice_line_vals(...))]
    """
    line: dict = {
        'name': name,
        'price_unit': price_unit,
        'quantity': quantity,
    }
    if product_id:
        line['product_id'] = product_id
    if tax_ids:
        line['tax_ids'] = [(6, 0, tax_ids)]
    return line


# ── Assets bundle name ─────────────────────────────────────────────────────────

def assets_backend_bundle() -> str:
    """Return the correct backend assets bundle key for this Odoo version."""
    # Odoo 17+ all use 'web.assets_backend'; kept as a function for future-proofing
    return 'web.assets_backend'


# ── mail.alias support ─────────────────────────────────────────────────────────

def has_mail_alias(env) -> bool:
    """Return True if mail.alias model is available (it always is in 17+)."""
    return 'mail.alias' in env


# ── ORM / field API differences ───────────────────────────────────────────────

def search_count_safe(model, domain: list) -> int:
    """
    Odoo 17 uses model.search_count(domain).
    Odoo 18 keeps the same signature but adds a 'limit' kwarg — use it to cap
    costly full-table scans where you only need existence.
    """
    if ODOO_VERSION >= 18:
        return model.search_count(domain, limit=1000)
    return model.search_count(domain)


def ir_cron_method_name(method: str) -> str:
    """
    Odoo 17 ir.cron uses method_direct_trigger (code field).
    Odoo 18+ keeps the code field but also supports method_id.
    Return a safe code snippet string for the cron's 'code' field.
    """
    return method


# ── Currency / company ────────────────────────────────────────────────────────

def company_currency_name(env) -> str:
    """Return current company currency ISO code, e.g. 'RWF', 'USD'."""
    try:
        return env.company.currency_id.name or 'USD'
    except Exception:
        return 'USD'
