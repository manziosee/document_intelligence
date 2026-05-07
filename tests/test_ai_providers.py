"""
Tests for the pluggable AI provider abstraction.
All API calls are mocked — no real network traffic.
"""
import json
import unittest
from unittest.mock import MagicMock, patch


class TestAIProviderBase(unittest.TestCase):

    def _make_provider(self):
        from custom_addons.document_intelligence.services.ai_providers import AIProvider
        p = AIProvider()
        p.name = 'test'
        return p

    def test_parse_json_clean(self):
        p = self._make_provider()
        result = p._parse_json('{"key": "val"}')
        self.assertEqual(result, {'key': 'val'})

    def test_parse_json_strips_fences(self):
        p = self._make_provider()
        result = p._parse_json('```json\n{"a": 1}\n```')
        self.assertEqual(result, {'a': 1})

    def test_parse_json_invalid_raises(self):
        p = self._make_provider()
        with self.assertRaises(RuntimeError):
            p._parse_json('not json at all')

    def test_parse_json_non_dict_raises(self):
        p = self._make_provider()
        with self.assertRaises(RuntimeError):
            p._parse_json('[1, 2, 3]')


class TestGetProvider(unittest.TestCase):

    def test_unknown_provider_raises(self):
        from custom_addons.document_intelligence.services.ai_providers import get_provider
        with self.assertRaises(RuntimeError, msg='Unknown AI provider'):
            get_provider('cohere', openai_key='', groq_key='', anthropic_key='')

    def test_openai_no_key_raises(self):
        from custom_addons.document_intelligence.services.ai_providers import get_provider
        with self.assertRaises(RuntimeError):
            get_provider('openai', openai_key='', groq_key='', anthropic_key='')

    def test_groq_no_key_raises(self):
        from custom_addons.document_intelligence.services.ai_providers import get_provider
        with self.assertRaises(RuntimeError):
            get_provider('groq', openai_key='', groq_key='', anthropic_key='')

    def test_anthropic_no_key_raises(self):
        from custom_addons.document_intelligence.services.ai_providers import get_provider
        with self.assertRaises(RuntimeError):
            get_provider('anthropic', openai_key='', groq_key='', anthropic_key='')

    def test_openai_provider_returned(self):
        from custom_addons.document_intelligence.services.ai_providers import (
            get_provider, OpenAIProvider,
        )
        p = get_provider('openai', openai_key='sk-test-12345678', groq_key='', anthropic_key='')
        self.assertIsInstance(p, OpenAIProvider)

    def test_groq_provider_returned(self):
        from custom_addons.document_intelligence.services.ai_providers import (
            get_provider, GroqProvider,
        )
        p = get_provider('groq', openai_key='', groq_key='gsk_test-12345678', anthropic_key='')
        self.assertIsInstance(p, GroqProvider)

    def test_anthropic_provider_returned(self):
        from custom_addons.document_intelligence.services.ai_providers import (
            get_provider, AnthropicProvider,
        )
        p = get_provider('anthropic', openai_key='', groq_key='', anthropic_key='sk-ant-test1234')
        self.assertIsInstance(p, AnthropicProvider)


class TestOpenAIProviderExtract(unittest.TestCase):

    @patch('custom_addons.document_intelligence.services.ai_providers.OpenAI')
    def test_extract_returns_content(self, mock_openai_cls):
        from custom_addons.document_intelligence.services.ai_providers import OpenAIProvider
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"result": "ok"}'))]
        )
        provider = OpenAIProvider('sk-test-12345678')
        result = provider.extract('sys', 'user', 'gpt-4o-mini')
        self.assertEqual(result, '{"result": "ok"}')


class TestGroqProviderExtract(unittest.TestCase):

    @patch('custom_addons.document_intelligence.services.ai_providers.OpenAI')
    def test_extract_uses_groq_base_url(self, mock_openai_cls):
        from custom_addons.document_intelligence.services.ai_providers import (
            GroqProvider, GroqProvider as GP,
        )
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"ok": true}'))]
        )
        provider = GroqProvider('gsk_test-12345678')
        provider.extract('sys', 'user', 'llama-3.3-70b-versatile')
        call_kwargs = mock_openai_cls.call_args[1]
        self.assertIn('groq.com', call_kwargs.get('base_url', ''))
