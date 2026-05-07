"""
Tests for the OCR service dispatcher.
All external I/O (pytesseract, pdfminer, docx) is mocked.
"""
import base64
import unittest
from unittest.mock import MagicMock, patch


class TestOcrService(unittest.TestCase):

    def _b64(self, data: bytes) -> str:
        return base64.b64encode(data).decode()

    # ── image dispatch ────────────────────────────────────────────────────────

    @patch('custom_addons.document_intelligence.services.ocr_service.pytesseract')
    @patch('custom_addons.document_intelligence.services.ocr_service.Image')
    def test_image_dispatch_png(self, mock_image, mock_tess):
        from custom_addons.document_intelligence.services import ocr_service
        mock_tess.image_to_string.return_value = 'hello world'
        result = ocr_service.extract_text(self._b64(b'fake_png'), 'doc.png', 'eng')
        self.assertEqual(result, 'hello world')
        mock_tess.image_to_string.assert_called_once()

    @patch('custom_addons.document_intelligence.services.ocr_service.pytesseract')
    @patch('custom_addons.document_intelligence.services.ocr_service.Image')
    def test_image_dispatch_jpg(self, mock_image, mock_tess):
        from custom_addons.document_intelligence.services import ocr_service
        mock_tess.image_to_string.return_value = 'receipt text'
        result = ocr_service.extract_text(self._b64(b'fake_jpg'), 'scan.jpg', 'fra')
        self.assertIn('receipt', result)

    # ── DOCX dispatch ─────────────────────────────────────────────────────────

    @patch('custom_addons.document_intelligence.services.ocr_service.Document')
    def test_docx_dispatch(self, mock_doc_cls):
        from custom_addons.document_intelligence.services import ocr_service
        mock_doc = MagicMock()
        mock_doc.paragraphs = [MagicMock(text='Para 1'), MagicMock(text='Para 2')]
        mock_doc_cls.return_value = mock_doc
        result = ocr_service.extract_text(self._b64(b'fake_docx'), 'cv.docx', 'eng')
        self.assertIn('Para 1', result)
        self.assertIn('Para 2', result)

    # ── empty text raises ─────────────────────────────────────────────────────

    def test_unsupported_extension_still_tries_image(self):
        from custom_addons.document_intelligence.services import ocr_service
        with patch.object(ocr_service, '_extract_image', return_value='extracted') as m:
            result = ocr_service.extract_text(self._b64(b'data'), 'file.tiff', 'eng')
            m.assert_called_once()
            self.assertEqual(result, 'extracted')


class TestAiProviderMaskKey(unittest.TestCase):

    def test_mask_short_key(self):
        from custom_addons.document_intelligence.services.ai_providers import AIProvider
        self.assertEqual(AIProvider.mask_key('abc'), '***')

    def test_mask_normal_key(self):
        from custom_addons.document_intelligence.services.ai_providers import AIProvider
        key = 'sk-proj-ABCDEFGH1234'
        masked = AIProvider.mask_key(key)
        self.assertTrue(masked.startswith('sk-p'))
        self.assertTrue(masked.endswith('1234'))
        self.assertIn('...', masked)

    def test_mask_empty_key(self):
        from custom_addons.document_intelligence.services.ai_providers import AIProvider
        self.assertEqual(AIProvider.mask_key(''), '***')
