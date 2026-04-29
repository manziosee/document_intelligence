"""
REST endpoint: POST /document_intelligence/upload

Accepts a multipart file upload and creates a DocumentRecord.
Returns JSON with the new record id so external tools / the frontend can trigger extraction.
"""
import base64
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class DocumentIntelligenceController(http.Controller):

    @http.route(
        '/document_intelligence/upload',
        type='http',
        auth='user',
        methods=['POST'],
        csrf=False,
    )
    def upload_document(self, **kwargs):
        """
        Accepts multipart/form-data with:
          - file      : the uploaded file
          - name      : (optional) document name
          - mode      : (optional) auto | custom | template
          - fields    : (optional) comma-separated fields for custom mode
          - template_id: (optional) int — template id for template mode
          - extra_prompt: (optional) extra AI instructions
        """
        try:
            uploaded_file = kwargs.get('file')
            if not uploaded_file:
                return self._json_error('No file provided', 400)

            file_data = base64.b64encode(uploaded_file.read())
            file_name = uploaded_file.filename or 'document'
            name = kwargs.get('name') or file_name
            mode = kwargs.get('mode', 'auto')
            template_id = kwargs.get('template_id')

            vals = {
                'name': name,
                'file_data': file_data,
                'file_name': file_name,
                'extraction_mode': mode,
                'extra_prompt': kwargs.get('extra_prompt', ''),
            }
            if kwargs.get('fields'):
                vals['custom_fields_input'] = kwargs['fields']
            if template_id:
                vals['template_id'] = int(template_id)

            record = request.env['document.intelligence.record'].create(vals)
            return self._json_ok({'id': record.id, 'name': record.name})

        except Exception as e:
            _logger.exception('Document upload failed')
            return self._json_error(str(e), 500)

    @http.route(
        '/document_intelligence/extract/<int:record_id>',
        type='http',
        auth='user',
        methods=['POST'],
        csrf=False,
    )
    def trigger_extraction(self, record_id, **kwargs):
        """Trigger extraction for an existing DocumentRecord."""
        try:
            record = request.env['document.intelligence.record'].browse(record_id)
            if not record.exists():
                return self._json_error('Record not found', 404)
            record.action_extract()
            return self._json_ok({
                'id': record.id,
                'state': record.state,
                'detected_type': record.detected_document_type,
                'confidence': record.confidence_score,
            })
        except Exception as e:
            _logger.exception('Extraction trigger failed for record %s', record_id)
            return self._json_error(str(e), 500)

    @http.route(
        '/document_intelligence/status/<int:record_id>',
        type='http',
        auth='user',
        methods=['GET'],
        csrf=False,
    )
    def get_status(self, record_id, **kwargs):
        """Poll status of a DocumentRecord."""
        try:
            record = request.env['document.intelligence.record'].browse(record_id)
            if not record.exists():
                return self._json_error('Record not found', 404)
            return self._json_ok({
                'id': record.id,
                'state': record.state,
                'detected_type': record.detected_document_type,
                'confidence': record.confidence_score,
                'vendor': record.vendor_name,
                'date': str(record.document_date) if record.document_date else None,
                'total': record.total_amount,
                'currency': record.currency_detected,
                'suggested_action': record.suggested_action,
                'notes': record.processing_notes,
            })
        except Exception as e:
            return self._json_error(str(e), 500)

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _json_ok(data: dict):
        return request.make_response(
            json.dumps({'status': 'ok', **data}),
            headers=[('Content-Type', 'application/json')],
        )

    @staticmethod
    def _json_error(message: str, code: int = 400):
        return request.make_response(
            json.dumps({'status': 'error', 'message': message}),
            status=code,
            headers=[('Content-Type', 'application/json')],
        )
