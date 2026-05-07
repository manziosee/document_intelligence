"""
Pluggable AI provider abstraction.

To add a new provider (e.g. Cohere):
  1. Subclass AIProvider
  2. Implement extract()
  3. Register in PROVIDER_REGISTRY
  4. Add the selection entry to res.config.settings.doc_intel_ai_provider
"""
import json
import logging

_logger = logging.getLogger(__name__)


# ── Base class ────────────────────────────────────────────────────────────────


class AIProvider:
    name: str = 'base'

    def extract(self, system_prompt: str, user_message: str, model: str) -> str:
        raise NotImplementedError

    @staticmethod
    def mask_key(key: str) -> str:
        """Return a log-safe masked version of an API key."""
        if not key or len(key) < 8:
            return '***'
        return key[:4] + '...' + key[-4:]

    def _parse_json(self, text: str) -> dict:
        """Strip markdown fences and parse JSON."""
        text = text.strip()
        if text.startswith('```'):
            lines = text.splitlines()
            text = '\n'.join(lines[1:-1] if lines[-1].startswith('```') else lines[1:])
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError('Response is not a JSON object')
            return data
        except json.JSONDecodeError as e:
            _logger.error('AI response is not valid JSON: %s | Raw: %.200s', e, text)
            raise RuntimeError(f'AI returned invalid JSON: {e}. Raw: {text[:200]}')


# ── OpenAI ────────────────────────────────────────────────────────────────────


class OpenAIProvider(AIProvider):
    name = 'openai'
    DEFAULT_MODEL = 'gpt-4o-mini'

    def __init__(self, api_key: str):
        if not api_key:
            raise RuntimeError(
                'OpenAI API key is not configured. '
                'Go to Settings → Document Intelligence.'
            )
        self._api_key = api_key

    def extract(self, system_prompt: str, user_message: str, model: str) -> str:
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError('openai package not installed. Run: pip install openai')

        _logger.info('OpenAI call: model=%s key=%s', model, self.mask_key(self._api_key))
        client = OpenAI(api_key=self._api_key)
        response = client.chat.completions.create(
            model=model or self.DEFAULT_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_message},
            ],
            temperature=0.0,
            max_tokens=2000,
        )
        return response.choices[0].message.content


# ── Groq ──────────────────────────────────────────────────────────────────────


class GroqProvider(AIProvider):
    name = 'groq'
    DEFAULT_MODEL = 'llama-3.3-70b-versatile'
    BASE_URL = 'https://api.groq.com/openai/v1'

    def __init__(self, api_key: str):
        if not api_key:
            raise RuntimeError(
                'Groq API key is not configured. '
                'Go to Settings → Document Intelligence.'
            )
        self._api_key = api_key

    def extract(self, system_prompt: str, user_message: str, model: str) -> str:
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError('openai package not installed. Run: pip install openai')

        _logger.info('Groq call: model=%s key=%s', model, self.mask_key(self._api_key))
        client = OpenAI(api_key=self._api_key, base_url=self.BASE_URL)
        response = client.chat.completions.create(
            model=model or self.DEFAULT_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_message},
            ],
            temperature=0.0,
            max_tokens=2000,
        )
        return response.choices[0].message.content


# ── Anthropic (Claude) ────────────────────────────────────────────────────────


class AnthropicProvider(AIProvider):
    name = 'anthropic'
    DEFAULT_MODEL = 'claude-haiku-4-5-20251001'

    def __init__(self, api_key: str):
        if not api_key:
            raise RuntimeError(
                'Anthropic API key is not configured. '
                'Go to Settings → Document Intelligence.'
            )
        self._api_key = api_key

    def extract(self, system_prompt: str, user_message: str, model: str) -> str:
        try:
            import anthropic
        except ImportError:
            raise RuntimeError('anthropic package not installed. Run: pip install anthropic')

        _logger.info('Anthropic call: model=%s key=%s', model, self.mask_key(self._api_key))
        client = anthropic.Anthropic(api_key=self._api_key)
        message = client.messages.create(
            model=model or self.DEFAULT_MODEL,
            max_tokens=2000,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_message}],
        )
        return message.content[0].text


# ── Ollama (local, free, no API key) ─────────────────────────────────────────


class OllamaProvider(AIProvider):
    """
    Tier 2 — local AI via Ollama (https://ollama.com).
    Exposes an OpenAI-compatible API at http://localhost:11434/v1.
    No API key needed. User installs Ollama and pulls a model.
    """
    name = 'ollama'
    DEFAULT_MODEL = 'llama3'
    DEFAULT_BASE_URL = 'http://localhost:11434/v1'

    def __init__(self, base_url: str = '', model: str = ''):
        self._base_url = (base_url or self.DEFAULT_BASE_URL).rstrip('/')
        if not self._base_url.endswith('/v1'):
            self._base_url += '/v1'
        self._default_model = model or self.DEFAULT_MODEL

    def extract(self, system_prompt: str, user_message: str, model: str) -> str:
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError('openai package not installed. Run: pip install openai')

        effective_model = model or self._default_model
        _logger.info('Ollama call: base_url=%s model=%s', self._base_url, effective_model)
        client = OpenAI(api_key='ollama', base_url=self._base_url)
        response = client.chat.completions.create(
            model=effective_model,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_message},
            ],
            temperature=0.0,
            max_tokens=2000,
        )
        return response.choices[0].message.content


# ── Registry + factory ────────────────────────────────────────────────────────


PROVIDER_REGISTRY = {
    'openai': OpenAIProvider,
    'groq': GroqProvider,
    'anthropic': AnthropicProvider,
    'ollama': OllamaProvider,
}


def get_provider(provider_name: str, openai_key: str = '', groq_key: str = '',
                 anthropic_key: str = '', ollama_url: str = '',
                 ollama_model: str = '') -> AIProvider:
    """
    Return the right AIProvider instance.
    To add a new provider: register it in PROVIDER_REGISTRY and pass its key here.
    """
    cls = PROVIDER_REGISTRY.get(provider_name)
    if cls is None:
        raise RuntimeError(
            f'Unknown AI provider: {provider_name!r}. '
            f'Valid options: {list(PROVIDER_REGISTRY.keys())}'
        )

    if provider_name == 'ollama':
        return OllamaProvider(base_url=ollama_url, model=ollama_model)

    key_map = {
        'openai': openai_key,
        'groq': groq_key,
        'anthropic': anthropic_key,
    }
    return cls(key_map.get(provider_name, ''))