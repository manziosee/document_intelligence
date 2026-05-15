"""
Document Intelligence REST API
================================
All endpoints accept JSON or multipart/form-data.

Authentication
--------------
Two modes (configured in DI Settings → API):
  1. Session auth  (auth='user')  — normal Odoo browser session cookie.
  2. API-key auth  (header X-DI-API-Key) — set the key in DI Settings.
     This mode is designed for external integrations (scanners, mobile apps, ERPs).

Endpoints
---------
  POST   /api/di/upload          — upload + create DocumentRecord
  POST   /api/di/<id>/extract    — trigger synchronous extraction
  POST   /api/di/<id>/extract_async — queue for background extraction
  GET    /api/di/<id>/status     — poll extraction state + results
  POST   /api/di/<id>/correct_vendor — save vendor name correction
  GET    /api/di/health          — service health check (no auth required)
"""
import base64
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

_ALLOWED_EXTS = {'.pdf', '.png', '.jpg', '.jpeg', '.tiff', '.tif', '.docx', '.doc', '.bmp', '.webp'}


def _check_api_key():
    """
    Validate X-DI-API-Key header against the stored key.
    Returns (env, None) on success, or (None, error_response) on failure.
    Skips key check if the request is already authenticated via session.
    """
    api_key_header = request.httprequest.headers.get('X-DI-API-Key', '').strip()
    if not api_key_header:
        return request.env, None  # fall through to session auth

    ICP = request.env['ir.config_parameter'].sudo()
    stored_key = (ICP.get_param('document_intelligence.api_key', '') or '').strip()

    if not stored_key:
        return None, _json_error('API key authentication not configured on this server.', 403)
    if api_key_header != stored_key:
        return None, _json_error('Invalid API key.', 401)

    # Elevate to sudo for API-key callers so they are not blocked by record rules
    return request.env.sudo(), None


def _json_ok(data: dict, status: int = 200):
    return request.make_response(
        json.dumps({'status': 'ok', **data}, default=str),
        status=status,
        headers=[('Content-Type', 'application/json')],
    )


def _json_error(message: str, code: int = 400):
    return request.make_response(
        json.dumps({'status': 'error', 'message': message}),
        status=code,
        headers=[('Content-Type', 'application/json')],
    )


class DocumentIntelligenceAPI(http.Controller):

    # ── Health check (public) ─────────────────────────────────────────────────

    @http.route('/api/di/health', type='http', auth='public', methods=['GET'], csrf=False)
    def health(self, **_kw):
        return _json_ok({'service': 'document_intelligence', 'state': 'ok'})

    # ── Upload ────────────────────────────────────────────────────────────────

    @http.route('/api/di/upload', type='http', auth='user', methods=['POST'], csrf=False)
    def upload(self, **kwargs):
        """
        Create a DocumentRecord from a file upload.

        Form fields:
          file        — (required) the file binary
          name        — document name (default: filename)
          mode        — auto | custom | template  (default: auto)
          fields      — comma-separated fields for custom mode
          template_id — int for template mode
          extra_prompt— extra AI instructions
          async       — '1' to queue for background extraction instead of blocking
        """
        env, err = _check_api_key()
        if err:
            return err
        try:
            uploaded = kwargs.get('file')
            if not uploaded:
                return _json_error('No file provided.')

            fname = getattr(uploaded, 'filename', None) or 'document'
            import os
            ext = os.path.splitext(fname)[1].lower()
            if ext not in _ALLOWED_EXTS:
                return _json_error(
                    f'File type "{ext}" not supported. Allowed: {", ".join(sorted(_ALLOWED_EXTS))}'
                )

            raw = uploaded.read() if hasattr(uploaded, 'read') else uploaded
            file_b64 = base64.b64encode(raw)

            do_async = kwargs.get('async', '0') in ('1', 'true', 'True')

            vals = {
                'name': kwargs.get('name') or fname,
                'file_data': file_b64,
                'file_name': fname,
                'extraction_mode': kwargs.get('mode', 'auto'),
                'extra_prompt': kwargs.get('extra_prompt', ''),
            }
            if kwargs.get('fields'):
                vals['custom_fields_input'] = kwargs['fields']
            if kwargs.get('template_id'):
                vals['template_id'] = int(kwargs['template_id'])
            if do_async:
                from odoo import fields as ofields
                vals.update({
                    'async_extraction': True,
                    'async_queued_at': ofields.Datetime.now(),
                    'state': 'processing',
                })

            record = env['document.intelligence.record'].create(vals)
            _logger.info('API upload: created record %s (%s)', record.id, fname)
            return _json_ok({'id': record.id, 'name': record.name, 'async': do_async}, 201)

        except Exception as exc:
            _logger.exception('API upload failed')
            return _json_error(str(exc), 500)

    # ── Synchronous extraction ────────────────────────────────────────────────

    @http.route('/api/di/<int:record_id>/extract', type='http', auth='user', methods=['POST'], csrf=False)
    def extract(self, record_id, **_kw):
        """Trigger synchronous extraction and return full result."""
        env, err = _check_api_key()
        if err:
            return err
        try:
            rec = env['document.intelligence.record'].browse(record_id)
            if not rec.exists():
                return _json_error('Record not found.', 404)

            from ..services.document_processor import DocumentProcessor
            rec.write({'state': 'processing'})
            DocumentProcessor(rec).run()

            return _json_ok(self._record_payload(rec))
        except Exception as exc:
            _logger.exception('API extract failed for record %s', record_id)
            return _json_error(str(exc), 500)

    # ── Async extraction trigger ──────────────────────────────────────────────

    @http.route('/api/di/<int:record_id>/extract_async', type='http', auth='user', methods=['POST'], csrf=False)
    def extract_async(self, record_id, **_kw):
        """Queue record for background extraction; returns immediately."""
        env, err = _check_api_key()
        if err:
            return err
        try:
            rec = env['document.intelligence.record'].browse(record_id)
            if not rec.exists():
                return _json_error('Record not found.', 404)
            from odoo import fields as ofields
            rec.write({
                'async_extraction': True,
                'async_queued_at': ofields.Datetime.now(),
                'state': 'processing',
            })
            return _json_ok({'id': rec.id, 'queued': True})
        except Exception as exc:
            return _json_error(str(exc), 500)

    # ── Status / result ───────────────────────────────────────────────────────

    @http.route('/api/di/<int:record_id>/status', type='http', auth='user', methods=['GET'], csrf=False)
    def status(self, record_id, **_kw):
        """Return current state and all extracted fields."""
        env, err = _check_api_key()
        if err:
            return err
        try:
            rec = env['document.intelligence.record'].browse(record_id)
            if not rec.exists():
                return _json_error('Record not found.', 404)
            return _json_ok(self._record_payload(rec))
        except Exception as exc:
            return _json_error(str(exc), 500)

    # ── Vendor correction ─────────────────────────────────────────────────────

    @http.route('/api/di/<int:record_id>/correct_vendor', type='http', auth='user', methods=['POST'], csrf=False)
    def correct_vendor(self, record_id, **kwargs):
        """
        Save a vendor name correction so future documents auto-correct.
        Body field: corrected_name (string)
        """
        env, err = _check_api_key()
        if err:
            return err
        try:
            rec = env['document.intelligence.record'].browse(record_id)
            if not rec.exists():
                return _json_error('Record not found.', 404)
            corrected = (kwargs.get('corrected_name') or '').strip()
            if not corrected:
                return _json_error('corrected_name is required.')
            rec.vendor_name = corrected
            rec.action_save_vendor_correction()
            return _json_ok({'saved': True, 'corrected_name': corrected})
        except Exception as exc:
            return _json_error(str(exc), 500)

    # ── Legacy routes (kept for backwards compat) ─────────────────────────────

    @http.route('/document_intelligence/upload', type='http', auth='user', methods=['POST'], csrf=False)
    def upload_legacy(self, **kwargs):
        return self.upload(**kwargs)

    @http.route('/document_intelligence/extract/<int:record_id>', type='http', auth='user', methods=['POST'], csrf=False)
    def extract_legacy(self, record_id, **kwargs):
        return self.extract(record_id, **kwargs)

    @http.route('/document_intelligence/status/<int:record_id>', type='http', auth='user', methods=['GET'], csrf=False)
    def status_legacy(self, record_id, **kwargs):
        return self.status(record_id, **kwargs)

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _record_payload(rec) -> dict:
        """Serialize a DocumentRecord to a JSON-safe dict."""
        return {
            'id': rec.id,
            'name': rec.name,
            'state': rec.state,
            'detected_type': rec.detected_document_type,
            'confidence': rec.confidence_score,
            'vendor': rec.vendor_name,
            'reference': rec.reference_number,
            'date': str(rec.document_date) if rec.document_date else None,
            'total': rec.total_amount,
            'tax': rec.tax_amount,
            'currency': rec.currency_detected,
            'email': rec.contact_email,
            'phone': rec.contact_phone,
            'address': rec.contact_address,
            'vat_number': rec.vat_number,
            'iban': rec.iban,
            'swift': rec.swift,
            'suggested_action': rec.suggested_action,
            'notes': rec.processing_notes,
            'is_duplicate': rec.is_duplicate,
            'line_items': [
                {
                    'description': li.description,
                    'quantity': li.quantity,
                    'unit_price': li.unit_price,
                    'subtotal': li.price_subtotal,
                }
                for li in rec.line_item_ids
            ],
        }
