"""
Pluggable AI provider abstraction — Tier 2 (Ollama) + Tier 3 (Cloud AI).

Architecture
------------
  AIProvider          — abstract base: extract(), ping(), _parse_json()
    OllamaProvider    — Tier 2: local, free, private (http://localhost:11434)
    GroqProvider      — Tier 3: cloud, free tier, fast
    OpenAIProvider    — Tier 3: cloud, highest accuracy
    AnthropicProvider — Tier 3: cloud, excellent on complex docs

Adding a new provider
---------------------
  1. Subclass AIProvider, implement extract() and optionally ping()
  2. Register in PROVIDER_REGISTRY
  3. Add selection entry to res.config.settings.doc_intel_ai_provider
"""
import json
import logging
import re
import time

_logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════════
# Exceptions
# ════════════════════════════════════════════════════════════════════════════════

class OllamaNotAvailable(RuntimeError):
    """Raised when Ollama server cannot be reached — processor falls back to Tier 1."""


class ProviderAuthError(RuntimeError):
    """API key is missing, invalid, or revoked."""


class ProviderRateLimitError(RuntimeError):
    """Request rate limit hit — caller should retry after a delay."""


class ProviderQuotaError(RuntimeError):
    """Account quota exhausted — no retrying will help."""


# ════════════════════════════════════════════════════════════════════════════════
# Retry helper
# ════════════════════════════════════════════════════════════════════════════════

def _retry(fn, *, max_attempts: int = 3, base_delay: float = 2.0,
           retryable: tuple = ()):
    """
    Call fn() up to max_attempts times with exponential back-off.

    retryable: tuple of exception types that should trigger a retry.
    All other exceptions propagate immediately.
    """
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except retryable as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))   # 2s, 4s, 8s …
            _logger.warning(
                'Retryable error (attempt %d/%d): %s — retrying in %.0fs',
                attempt, max_attempts, exc, delay,
            )
            time.sleep(delay)
        except Exception:
            raise  # non-retryable: propagate immediately
    raise last_exc


# ════════════════════════════════════════════════════════════════════════════════
# Base class
# ════════════════════════════════════════════════════════════════════════════════

class AIProvider:
    name: str = 'base'

    def extract(self, system_prompt: str, user_message: str, model: str) -> str:
        raise NotImplementedError

    def ping(self) -> dict:
        """
        Test connectivity and return a status dict.
        Override in providers that can check their own health.

        Returns: {'ok': bool, 'message': str, 'models': list[str]}
        """
        return {'ok': True, 'message': 'No health check available.', 'models': []}

    @staticmethod
    def mask_key(key: str) -> str:
        if not key or len(key) < 8:
            return '***'
        return key[:4] + '...' + key[-4:]

    def _parse_json(self, text: str) -> dict:
        """
        Robustly extract a JSON object from AI response text.

        Handles:
        - Clean JSON
        - ```json … ``` fences
        - ``` … ``` fences (no language tag)
        - Inline `{…}` backtick
        - Trailing commas (common in LLM output)
        - Response text before/after the JSON object
        """
        text = text.strip()

        # 1. Strip markdown fences (```json … ``` or ``` … ```)
        fence_match = re.match(
            r'^```(?:json|JSON)?\s*\n?([\s\S]*?)\n?```\s*$', text, re.MULTILINE
        )
        if fence_match:
            text = fence_match.group(1).strip()

        # 2. Strip single-backtick inline fences
        if text.startswith('`') and text.endswith('`'):
            text = text[1:-1].strip()

        # 3. Extract the first JSON object if there's surrounding prose
        obj_match = re.search(r'\{[\s\S]*\}', text)
        if obj_match:
            text = obj_match.group(0)

        # 4. Remove trailing commas before } or ] (common LLM mistake)
        text = re.sub(r',\s*([}\]])', r'\1', text)

        # 5. Parse
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError('Response is not a JSON object')
            return data
        except json.JSONDecodeError as e:
            _logger.error('AI response is not valid JSON: %s\nRaw (first 500): %.500s', e, text)
            raise RuntimeError(
                f'AI returned invalid JSON: {e}. '
                f'This can happen with smaller models — try a larger model. '
                f'Raw (first 200 chars): {text[:200]}'
            )


# ════════════════════════════════════════════════════════════════════════════════
# Tier 2 — Ollama (local, free, private)
# ════════════════════════════════════════════════════════════════════════════════

class OllamaProvider(AIProvider):
    """
    Runs AI extraction on a local Ollama server.

    Setup:
      1. Install Ollama from https://ollama.com
      2. ollama pull llama3          (or mistral, qwen2, gemma2, …)
      3. Ollama auto-starts on port 11434

    Why local AI?
      - Completely free, forever
      - Documents never leave your server (banks, hospitals, government)
      - Works fully offline
      - Accuracy close to cloud AI on structured invoices
    """
    name = 'ollama'
    DEFAULT_MODEL = 'llama3'
    DEFAULT_BASE_URL = 'http://localhost:11434'
    DEFAULT_TIMEOUT = 120   # seconds — local inference can be slow on CPU

    # Models known to work well for document extraction (sorted best → fastest)
    RECOMMENDED_MODELS = [
        ('llama3.1:70b',  'Llama 3.1 70B — best accuracy (needs ~45GB RAM)'),
        ('llama3.1:8b',   'Llama 3.1 8B — great accuracy, fast (needs ~6GB RAM)'),
        ('llama3',        'Llama 3 8B — recommended default'),
        ('mistral',       'Mistral 7B — good on structured documents'),
        ('qwen2',         'Qwen 2 7B — good multilingual & French support'),
        ('gemma2',        'Gemma 2 9B — Google model, solid accuracy'),
        ('phi3',          'Phi 3 mini — fastest, lowest RAM (4GB)'),
    ]

    def __init__(self, base_url: str = '', model: str = '',
                 timeout: int = DEFAULT_TIMEOUT):
        raw_url = (base_url or self.DEFAULT_BASE_URL).rstrip('/')
        # Store the Ollama root URL (without /v1) for ping/list_models
        self._root_url = raw_url
        # OpenAI-compat endpoint always needs /v1
        self._api_url = raw_url + '/v1' if not raw_url.endswith('/v1') else raw_url
        self._default_model = model or self.DEFAULT_MODEL
        self._timeout = timeout

    # ── Health check ──────────────────────────────────────────────────────────

    def ping(self) -> dict:
        """
        Check if Ollama is running and return the list of pulled models.
        Used by the setup wizard Test Connection button.
        """
        try:
            import requests as req
        except ImportError:
            return {
                'ok': False,
                'message': (
                    'requests library not installed. '
                    'Run: pip install requests'
                ),
                'models': [],
            }

        try:
            resp = req.get(f'{self._root_url}/api/tags', timeout=5)
            resp.raise_for_status()
            data = resp.json()
            models = [m['name'] for m in data.get('models', [])]

            if not models:
                return {
                    'ok': True,
                    'message': (
                        'Ollama is running but no models are pulled yet. '
                        'Run: ollama pull llama3'
                    ),
                    'models': [],
                }

            return {
                'ok': True,
                'message': f'Ollama is running. {len(models)} model(s) available.',
                'models': models,
            }

        except Exception as exc:
            err = str(exc)
            if 'Connection refused' in err or 'Failed to establish' in err:
                hint = (
                    'Ollama is not running. '
                    'Start it with: ollama serve  (or install from ollama.com)'
                )
            else:
                hint = f'Cannot reach Ollama at {self._root_url}: {exc}'

            return {'ok': False, 'message': hint, 'models': []}

    def list_models(self) -> list[str]:
        """Return names of models available on this Ollama instance."""
        result = self.ping()
        return result.get('models', [])

    # ── Extraction ────────────────────────────────────────────────────────────

    def extract(self, system_prompt: str, user_message: str, model: str) -> str:
        try:
            from openai import OpenAI, APIConnectionError, APIStatusError
        except ImportError:
            raise RuntimeError('openai package not installed. Run: pip install openai')

        effective_model = model or self._default_model
        _logger.info(
            'Ollama call: url=%s model=%s timeout=%ds',
            self._api_url, effective_model, self._timeout,
        )

        client = OpenAI(
            api_key='ollama',      # Ollama ignores the key but the SDK requires one
            base_url=self._api_url,
            timeout=float(self._timeout),
            max_retries=0,         # we handle retries ourselves
        )

        def _call():
            try:
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
            except APIConnectionError as exc:
                raise OllamaNotAvailable(
                    f'Cannot connect to Ollama at {self._root_url}. '
                    f'Make sure Ollama is running: ollama serve\n'
                    f'Install from: https://ollama.com\n'
                    f'Detail: {exc}'
                )
            except APIStatusError as exc:
                if exc.status_code == 404:
                    raise OllamaNotAvailable(
                        f'Model "{effective_model}" is not pulled. '
                        f'Run: ollama pull {effective_model}\n'
                        f'Available models: ollama list'
                    )
                raise OllamaNotAvailable(
                    f'Ollama returned HTTP {exc.status_code}: {exc.message}'
                )

        return _retry(_call, max_attempts=2, base_delay=3.0,
                      retryable=(OllamaNotAvailable,))


# ════════════════════════════════════════════════════════════════════════════════
# Tier 3 — Groq (cloud, free tier, fast)
# ════════════════════════════════════════════════════════════════════════════════

class GroqProvider(AIProvider):
    """
    Cloud AI via Groq — OpenAI-compatible endpoint.

    Free tier: generous daily limits, great for most document workflows.
    Get a key in 2 minutes at https://console.groq.com/keys

    Best models for document extraction:
      llama-3.3-70b-versatile  — best accuracy on the free tier
      llama-3.1-8b-instant     — fastest, good for high-volume processing
      gemma2-9b-it             — good on multilingual documents
    """
    name = 'groq'
    DEFAULT_MODEL = 'llama-3.3-70b-versatile'
    BASE_URL = 'https://api.groq.com/openai/v1'
    DEFAULT_TIMEOUT = 60

    # Models available on Groq as of 2025 (update as Groq adds new ones)
    AVAILABLE_MODELS = [
        ('llama-3.3-70b-versatile',  'Llama 3.3 70B Versatile — best accuracy (recommended)'),
        ('llama-3.1-70b-versatile',  'Llama 3.1 70B Versatile'),
        ('llama-3.1-8b-instant',     'Llama 3.1 8B Instant — fastest'),
        ('gemma2-9b-it',             'Gemma 2 9B — good multilingual'),
        ('mixtral-8x7b-32768',       'Mixtral 8x7B — long documents (32K context)'),
    ]

    def __init__(self, api_key: str):
        if not api_key:
            raise ProviderAuthError(
                'Groq API key is not configured. '
                'Get a free key at https://console.groq.com/keys '
                'then go to Settings → Document Intelligence.'
            )
        self._api_key = api_key

    def ping(self) -> dict:
        """Verify the Groq key by listing models."""
        try:
            from openai import OpenAI, AuthenticationError
        except ImportError:
            return {'ok': False, 'message': 'openai package not installed.', 'models': []}

        try:
            client = OpenAI(api_key=self._api_key, base_url=self.BASE_URL, timeout=10)
            models_page = client.models.list()
            names = [m.id for m in models_page.data]
            return {
                'ok': True,
                'message': 'Groq API key is valid.',
                'models': names,
            }
        except Exception as exc:
            msg = self._classify_error(exc)
            return {'ok': False, 'message': msg, 'models': []}

    def extract(self, system_prompt: str, user_message: str, model: str) -> str:
        try:
            from openai import OpenAI, RateLimitError, AuthenticationError, APIStatusError
        except ImportError:
            raise RuntimeError('openai package not installed. Run: pip install openai')

        effective_model = model or self.DEFAULT_MODEL
        _logger.info('Groq call: model=%s key=%s', effective_model, self.mask_key(self._api_key))

        client = OpenAI(
            api_key=self._api_key,
            base_url=self.BASE_URL,
            timeout=float(self.DEFAULT_TIMEOUT),
            max_retries=0,
        )

        def _call():
            try:
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
            except RateLimitError as exc:
                raise ProviderRateLimitError(
                    f'Groq rate limit hit. '
                    f'The free tier allows ~30 requests/minute and ~14,400/day. '
                    f'Wait a moment and try again, or upgrade at console.groq.com. '
                    f'Detail: {exc}'
                )
            except AuthenticationError:
                raise ProviderAuthError(
                    'Groq API key is invalid or revoked. '
                    'Go to Settings → Document Intelligence and update your Groq key.'
                )
            except APIStatusError as exc:
                if exc.status_code == 429:
                    raise ProviderRateLimitError(str(exc))
                raise RuntimeError(f'Groq API error {exc.status_code}: {exc.message}')

        return _retry(
            _call,
            max_attempts=3,
            base_delay=5.0,
            retryable=(ProviderRateLimitError,),
        )

    @staticmethod
    def _classify_error(exc) -> str:
        msg = str(exc)
        if 'AuthenticationError' in type(exc).__name__ or '401' in msg:
            return 'Invalid Groq API key. Check Settings → Document Intelligence.'
        if 'RateLimitError' in type(exc).__name__ or '429' in msg:
            return 'Groq rate limit hit. Wait a moment and try again.'
        return f'Groq error: {exc}'


# ════════════════════════════════════════════════════════════════════════════════
# Tier 3 — OpenAI
# ════════════════════════════════════════════════════════════════════════════════

class OpenAIProvider(AIProvider):
    """
    Cloud AI via OpenAI.

    Best models for document extraction:
      gpt-4o-mini  — cheapest, ~$0.01 per document, 80% of gpt-4o quality
      gpt-4o       — best accuracy, ~$0.10 per document
    """
    name = 'openai'
    DEFAULT_MODEL = 'gpt-4o-mini'
    DEFAULT_TIMEOUT = 60

    def __init__(self, api_key: str):
        if not api_key:
            raise ProviderAuthError(
                'OpenAI API key is not configured. '
                'Get one at https://platform.openai.com/api-keys '
                'then go to Settings → Document Intelligence.'
            )
        self._api_key = api_key

    def ping(self) -> dict:
        try:
            from openai import OpenAI
        except ImportError:
            return {'ok': False, 'message': 'openai package not installed.', 'models': []}

        try:
            client = OpenAI(api_key=self._api_key, timeout=10)
            models_page = client.models.list()
            # Filter to GPT models only for display
            names = [m.id for m in models_page.data if 'gpt' in m.id.lower()][:10]
            return {'ok': True, 'message': 'OpenAI API key is valid.', 'models': names}
        except Exception as exc:
            msg = self._classify_error(exc)
            return {'ok': False, 'message': msg, 'models': []}

    def extract(self, system_prompt: str, user_message: str, model: str) -> str:
        try:
            from openai import OpenAI, RateLimitError, AuthenticationError, APIStatusError
        except ImportError:
            raise RuntimeError('openai package not installed. Run: pip install openai')

        effective_model = model or self.DEFAULT_MODEL
        _logger.info('OpenAI call: model=%s key=%s', effective_model, self.mask_key(self._api_key))

        client = OpenAI(
            api_key=self._api_key,
            timeout=float(self.DEFAULT_TIMEOUT),
            max_retries=0,
        )

        def _call():
            try:
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
            except RateLimitError as exc:
                raise ProviderRateLimitError(
                    f'OpenAI rate limit or quota exceeded. '
                    f'Check your usage at platform.openai.com/usage. '
                    f'Detail: {exc}'
                )
            except AuthenticationError:
                raise ProviderAuthError(
                    'OpenAI API key is invalid or has no credits. '
                    'Check https://platform.openai.com/api-keys'
                )
            except APIStatusError as exc:
                if exc.status_code == 429:
                    raise ProviderRateLimitError(str(exc))
                if exc.status_code == 402:
                    raise ProviderQuotaError(
                        'OpenAI account has no credits. '
                        'Add a payment method at platform.openai.com/billing'
                    )
                raise RuntimeError(f'OpenAI API error {exc.status_code}: {exc.message}')

        return _retry(
            _call,
            max_attempts=3,
            base_delay=5.0,
            retryable=(ProviderRateLimitError,),
        )

    @staticmethod
    def _classify_error(exc) -> str:
        msg = str(exc)
        if 'AuthenticationError' in type(exc).__name__ or '401' in msg:
            return 'Invalid OpenAI API key.'
        if '402' in msg or 'quota' in msg.lower():
            return 'OpenAI account has no credits. Add billing at platform.openai.com.'
        if 'RateLimitError' in type(exc).__name__ or '429' in msg:
            return 'OpenAI rate limit hit. Try again in a moment.'
        return f'OpenAI error: {exc}'


# ════════════════════════════════════════════════════════════════════════════════
# Tier 3 — Anthropic (Claude)
# ════════════════════════════════════════════════════════════════════════════════

class AnthropicProvider(AIProvider):
    """
    Cloud AI via Anthropic.

    Best models for document extraction:
      claude-haiku-4-5-20251001  — cheapest, fast, great for invoices
      claude-sonnet-4-6          — balanced quality/cost
      claude-opus-4-7            — highest accuracy on complex docs
    """
    name = 'anthropic'
    DEFAULT_MODEL = 'claude-haiku-4-5-20251001'
    DEFAULT_TIMEOUT = 60

    def __init__(self, api_key: str):
        if not api_key:
            raise ProviderAuthError(
                'Anthropic API key is not configured. '
                'Get one at https://console.anthropic.com '
                'then go to Settings → Document Intelligence.'
            )
        self._api_key = api_key

    def ping(self) -> dict:
        try:
            import anthropic
        except ImportError:
            return {'ok': False, 'message': 'anthropic package not installed. Run: pip install anthropic', 'models': []}

        try:
            client = anthropic.Anthropic(api_key=self._api_key)
            # Minimal test — count_tokens is a cheap API call
            client.messages.count_tokens(
                model=self.DEFAULT_MODEL,
                messages=[{'role': 'user', 'content': 'ping'}],
            )
            return {
                'ok': True,
                'message': 'Anthropic API key is valid.',
                'models': [
                    'claude-haiku-4-5-20251001',
                    'claude-sonnet-4-6',
                    'claude-opus-4-7',
                ],
            }
        except Exception as exc:
            msg = self._classify_error(exc)
            return {'ok': False, 'message': msg, 'models': []}

    def extract(self, system_prompt: str, user_message: str, model: str) -> str:
        try:
            import anthropic
        except ImportError:
            raise RuntimeError('anthropic package not installed. Run: pip install anthropic')

        effective_model = model or self.DEFAULT_MODEL
        _logger.info('Anthropic call: model=%s key=%s', effective_model, self.mask_key(self._api_key))

        client = anthropic.Anthropic(
            api_key=self._api_key,
            timeout=float(self.DEFAULT_TIMEOUT),
            max_retries=0,
        )

        def _call():
            try:
                message = client.messages.create(
                    model=effective_model,
                    max_tokens=2000,
                    system=system_prompt,
                    messages=[{'role': 'user', 'content': user_message}],
                )
                return message.content[0].text
            except anthropic.RateLimitError as exc:
                raise ProviderRateLimitError(
                    f'Anthropic rate limit hit. '
                    f'Check usage at console.anthropic.com. Detail: {exc}'
                )
            except anthropic.AuthenticationError:
                raise ProviderAuthError(
                    'Anthropic API key is invalid. '
                    'Check Settings → Document Intelligence.'
                )
            except anthropic.APIStatusError as exc:
                if exc.status_code in (529, 529):   # Anthropic overload
                    raise ProviderRateLimitError(f'Anthropic overloaded: {exc}')
                raise RuntimeError(f'Anthropic API error {exc.status_code}: {exc.message}')

        return _retry(
            _call,
            max_attempts=3,
            base_delay=5.0,
            retryable=(ProviderRateLimitError,),
        )

    @staticmethod
    def _classify_error(exc) -> str:
        msg = str(exc)
        if 'AuthenticationError' in type(exc).__name__ or '401' in msg:
            return 'Invalid Anthropic API key.'
        if 'RateLimitError' in type(exc).__name__ or '429' in msg or '529' in msg:
            return 'Anthropic rate limit or overload. Try again in a moment.'
        return f'Anthropic error: {exc}'


# ════════════════════════════════════════════════════════════════════════════════
# Registry + factory
# ════════════════════════════════════════════════════════════════════════════════

PROVIDER_REGISTRY: dict[str, type[AIProvider]] = {
    'ollama':    OllamaProvider,
    'groq':      GroqProvider,
    'openai':    OpenAIProvider,
    'anthropic': AnthropicProvider,
}


def get_provider(
    provider_name: str,
    openai_key: str = '',
    groq_key: str = '',
    anthropic_key: str = '',
    ollama_url: str = '',
    ollama_model: str = '',
) -> AIProvider:
    """
    Return the right AIProvider instance for the given provider name.

    Raises RuntimeError for unknown provider names.
    Individual providers raise ProviderAuthError when their key is missing.
    OllamaProvider raises OllamaNotAvailable when the server is unreachable
    (the document processor catches this and falls back to Tier 1).
    """
    cls = PROVIDER_REGISTRY.get(provider_name)
    if cls is None:
        raise RuntimeError(
            f'Unknown AI provider: {provider_name!r}. '
            f'Valid options: {sorted(PROVIDER_REGISTRY.keys())}'
        )

    if provider_name == 'ollama':
        return OllamaProvider(base_url=ollama_url, model=ollama_model)

    key_map = {
        'openai':    openai_key,
        'groq':      groq_key,
        'anthropic': anthropic_key,
    }
    return cls(key_map[provider_name])
