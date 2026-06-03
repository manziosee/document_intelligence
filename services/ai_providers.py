"""
Pluggable AI provider abstraction — Tier 2 (Ollama) + Tier 3 (Cloud AI).

ZERO HARD DEPENDENCIES — works with Python stdlib alone.

Every provider has two code paths:
  1. openai / anthropic package (if installed) — richer error messages
  2. urllib fallback (always available) — works on a fresh Python install

Users never need to run pip install to use Groq, OpenAI, or Ollama.
The `openai` package is used automatically if present, otherwise urllib
makes the same HTTP calls directly.
"""
import json
import logging
import re
import ssl
import time
import urllib.error
import urllib.request

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
# Shared stdlib HTTP helper — used by all providers when openai pkg is absent
# ════════════════════════════════════════════════════════════════════════════════

def _http_post(url: str, payload: dict, headers: dict, timeout: int = 60) -> dict:
    """
    POST JSON to url, return parsed JSON response dict.

    Raises:
      ProviderAuthError     on HTTP 401/403
      ProviderRateLimitError on HTTP 429
      ProviderQuotaError    on HTTP 402
      RuntimeError          for other HTTP errors or network failures
    """
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=body, headers=headers, method='POST')

    # Build SSL context — verified by default, falls back gracefully on old OS
    try:
        ctx = ssl.create_default_context()
    except Exception:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode('utf-8', errors='replace')
            err_data = json.loads(err_body)
            err_msg = (
                err_data.get('error', {}).get('message')
                or err_data.get('message')
                or err_body[:300]
            )
        except Exception:
            err_msg = str(exc)

        if exc.code in (401, 403):
            raise ProviderAuthError(f'API key is invalid or revoked: {err_msg}')
        if exc.code == 402:
            raise ProviderQuotaError(f'Account has no credits: {err_msg}')
        if exc.code == 429:
            raise ProviderRateLimitError(f'Rate limit hit: {err_msg}')
        raise RuntimeError(f'HTTP {exc.code}: {err_msg}')
    except urllib.error.URLError as exc:
        raise RuntimeError(f'Network error: {exc.reason}')
    except Exception as exc:
        raise RuntimeError(f'Request failed: {exc}')


# ════════════════════════════════════════════════════════════════════════════════
# Retry helper
# ════════════════════════════════════════════════════════════════════════════════

def _retry(fn, *, max_attempts: int = 3, base_delay: float = 2.0,
           retryable: tuple = ()):
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except retryable as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            _logger.warning(
                'Retryable error (attempt %d/%d): %s — retrying in %.0fs',
                attempt, max_attempts, exc, delay,
            )
            time.sleep(delay)
        except Exception:
            raise
    raise last_exc


# ════════════════════════════════════════════════════════════════════════════════
# Base class
# ════════════════════════════════════════════════════════════════════════════════

class AIProvider:
    name: str = 'base'

    def extract(self, system_prompt: str, user_message: str, model: str) -> str:
        raise NotImplementedError

    def ping(self) -> dict:
        return {'ok': True, 'message': 'No health check available.', 'models': []}

    @staticmethod
    def mask_key(key: str) -> str:
        if not key or len(key) < 8:
            return '***'
        return key[:4] + '...' + key[-4:]

    def _parse_json(self, text: str) -> dict:
        """
        Robustly extract a JSON object from AI response text.
        Handles markdown fences, trailing commas, surrounding prose.
        """
        text = text.strip()

        fence = re.match(r'^```(?:json|JSON)?\s*\n?([\s\S]*?)\n?```\s*$', text, re.MULTILINE)
        if fence:
            text = fence.group(1).strip()

        if text.startswith('`') and text.endswith('`'):
            text = text[1:-1].strip()

        obj = re.search(r'\{[\s\S]*\}', text)
        if obj:
            text = obj.group(0)

        text = re.sub(r',\s*([}\]])', r'\1', text)

        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError('Response is not a JSON object')
            return data
        except json.JSONDecodeError as e:
            _logger.error('AI response not valid JSON: %s\nRaw (500): %.500s', e, text)
            raise RuntimeError(
                f'AI returned invalid JSON: {e}. '
                f'Try a different model. Raw (200): {text[:200]}'
            )


# ════════════════════════════════════════════════════════════════════════════════
# Tier 2 — Ollama (local, free, private)
# ════════════════════════════════════════════════════════════════════════════════

class OllamaProvider(AIProvider):
    name = 'ollama'
    DEFAULT_MODEL = 'llama3'
    DEFAULT_BASE_URL = 'http://localhost:11434'
    DEFAULT_TIMEOUT = 120

    RECOMMENDED_MODELS = [
        ('llama3.1:70b',  'Llama 3.1 70B — best accuracy (needs ~45GB RAM)'),
        ('llama3.1:8b',   'Llama 3.1 8B — great accuracy, fast (needs ~6GB RAM)'),
        ('llama3',        'Llama 3 8B — recommended default'),
        ('mistral',       'Mistral 7B — good on structured documents'),
        ('qwen2',         'Qwen 2 7B — good multilingual & French support'),
        ('gemma2',        'Gemma 2 9B — Google model, solid accuracy'),
        ('phi3',          'Phi 3 mini — fastest, lowest RAM (4GB)'),
    ]

    def __init__(self, base_url: str = '', model: str = '', timeout: int = DEFAULT_TIMEOUT):
        raw_url = (base_url or self.DEFAULT_BASE_URL).rstrip('/')
        self._root_url = raw_url
        self._api_url = raw_url + '/v1' if not raw_url.endswith('/v1') else raw_url
        self._default_model = model or self.DEFAULT_MODEL
        self._timeout = timeout

    def ping(self) -> dict:
        try:
            req = urllib.request.Request(f'{self._root_url}/api/tags', method='GET')
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            models = [m['name'] for m in data.get('models', [])]
            if not models:
                return {
                    'ok': True,
                    'message': 'Ollama is running but no models pulled yet. Run: ollama pull llama3',
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
                hint = 'Ollama is not running. Start it with: ollama serve'
            else:
                hint = f'Cannot reach Ollama at {self._root_url}: {exc}'
            return {'ok': False, 'message': hint, 'models': []}

    def list_models(self) -> list:
        return self.ping().get('models', [])

    def extract(self, system_prompt: str, user_message: str, model: str) -> str:
        effective_model = model or self._default_model
        _logger.info('Ollama call: url=%s model=%s', self._api_url, effective_model)

        # Try openai SDK first, fall back to urllib
        try:
            from openai import OpenAI, APIConnectionError, APIStatusError
            return self._extract_openai_sdk(
                OpenAI, APIConnectionError, APIStatusError,
                system_prompt, user_message, effective_model,
            )
        except ImportError:
            pass

        return self._extract_urllib(system_prompt, user_message, effective_model)

    def _extract_openai_sdk(self, OpenAI, APIConnectionError, APIStatusError,
                             system_prompt, user_message, model):
        client = OpenAI(
            api_key='ollama',
            base_url=self._api_url,
            timeout=float(self._timeout),
            max_retries=0,
        )

        def _call():
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {'role': 'system', 'content': system_prompt},
                        {'role': 'user', 'content': user_message},
                    ],
                    temperature=0.0,
                    max_tokens=4096,
                )
                return resp.choices[0].message.content
            except APIConnectionError as exc:
                raise OllamaNotAvailable(
                    f'Cannot connect to Ollama at {self._root_url}. '
                    f'Make sure it is running: ollama serve\n'
                    f'Install from: https://ollama.com\nDetail: {exc}'
                )
            except APIStatusError as exc:
                if exc.status_code == 404:
                    raise OllamaNotAvailable(
                        f'Model "{model}" is not pulled. Run: ollama pull {model}'
                    )
                raise OllamaNotAvailable(f'Ollama HTTP {exc.status_code}: {exc.message}')

        return _retry(_call, max_attempts=2, base_delay=3.0, retryable=(OllamaNotAvailable,))

    def _extract_urllib(self, system_prompt: str, user_message: str, model: str) -> str:
        url = f'{self._api_url}/chat/completions'
        payload = {
            'model': model,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_message},
            ],
            'temperature': 0.0,
            'max_tokens': 4096,
        }
        headers = {
            'Authorization': 'Bearer ollama',
            'Content-Type': 'application/json',
        }

        def _call():
            try:
                data = _http_post(url, payload, headers, timeout=self._timeout)
                return data['choices'][0]['message']['content']
            except (ProviderAuthError, ProviderRateLimitError, RuntimeError) as exc:
                msg = str(exc)
                if 'Connection refused' in msg or 'Network error' in msg:
                    raise OllamaNotAvailable(
                        f'Cannot connect to Ollama at {self._root_url}. '
                        f'Make sure it is running: ollama serve'
                    )
                raise OllamaNotAvailable(f'Ollama error: {exc}')

        return _retry(_call, max_attempts=2, base_delay=3.0, retryable=(OllamaNotAvailable,))


# ════════════════════════════════════════════════════════════════════════════════
# Tier 3 — Groq (cloud, free tier, fast) — ZERO extra packages required
# ════════════════════════════════════════════════════════════════════════════════

class GroqProvider(AIProvider):
    """
    Cloud AI via Groq — free tier, fast, works with ZERO pip installs.

    Get a free key at https://console.groq.com/keys
    then add it in Settings → Document Intelligence → Groq API Key.
    """
    name = 'groq'
    DEFAULT_MODEL = 'llama-3.3-70b-versatile'
    BASE_URL = 'https://api.groq.com/openai/v1'
    DEFAULT_TIMEOUT = 60

    AVAILABLE_MODELS = [
        ('llama-3.3-70b-versatile',  'Llama 3.3 70B Versatile — best accuracy (recommended)'),
        ('llama-3.1-70b-versatile',  'Llama 3.1 70B Versatile'),
        ('llama-3.1-8b-instant',     'Llama 3.1 8B Instant — fastest'),
        ('gemma2-9b-it',             'Gemma 2 9B — good multilingual'),
        ('mixtral-8x7b-32768',       'Mixtral 8x7B — long documents (32K context)'),
    ]

    def __init__(self, api_key: str):
        # Strip immediately — copy-paste from console commonly adds trailing spaces/newlines
        cleaned = (api_key or '').strip()
        if not cleaned:
            raise ProviderAuthError(
                'Groq API key is not configured. '
                'Get a free key at https://console.groq.com/keys '
                'then go to Settings → Document Intelligence.'
            )
        self._api_key = cleaned

    def ping(self) -> dict:
        """
        Test the Groq API key with a real minimal completions call.

        Uses urllib — no pip install needed.
        A real call is more reliable than /models because some key types
        can list models but are still rate-limited or restricted.
        """
        url = f'{self.BASE_URL}/chat/completions'
        payload = {
            'model': self.DEFAULT_MODEL,
            'messages': [{'role': 'user', 'content': 'Hi'}],
            'max_tokens': 1,
            'temperature': 0.0,
        }
        headers = {
            'Authorization': f'Bearer {self._api_key}',
            'Content-Type': 'application/json',
        }
        key_hint = self._api_key[:7] + '...' if len(self._api_key) >= 7 else '(short key)'

        try:
            _http_post(url, payload, headers, timeout=20)
            return {
                'ok': True,
                'message': f'Groq API key is valid and working. (key: {key_hint})',
                'models': [m[0] for m in self.AVAILABLE_MODELS],
            }
        except ProviderAuthError:
            return {
                'ok': False,
                'message': (
                    f'Groq rejected the key (HTTP 401).\n\n'
                    f'Key received: {key_hint}\n\n'
                    f'How to fix:\n'
                    f'1. Go to https://console.groq.com/keys\n'
                    f'2. Click "Create API Key" → copy the FULL key\n'
                    f'3. Paste it in Settings → Document Intelligence → Groq API Key\n'
                    f'4. Click Save FIRST, then Test Connection\n\n'
                    f'Note: Groq keys start with "gsk_" and are ~56 characters long.\n'
                    f'Make sure you copied the entire key — no extra spaces.'
                ),
                'models': [],
            }
        except ProviderRateLimitError:
            # Rate limit means the key IS valid — Groq accepted it
            return {
                'ok': True,
                'message': f'Groq key is valid (rate limited but working). (key: {key_hint})',
                'models': [m[0] for m in self.AVAILABLE_MODELS],
            }
        except Exception as exc:
            return {
                'ok': False,
                'message': (
                    f'Cannot reach Groq: {exc}\n\n'
                    f'Check your internet connection and try again.\n'
                    f'Key hint: {key_hint}'
                ),
                'models': [],
            }

    def extract(self, system_prompt: str, user_message: str, model: str) -> str:
        effective_model = model or self.DEFAULT_MODEL
        _logger.info('Groq call: model=%s key=%s', effective_model, self.mask_key(self._api_key))

        # Try openai SDK first (richer error info), fall back to urllib
        try:
            from openai import OpenAI, RateLimitError, AuthenticationError, APIStatusError
            return self._extract_openai_sdk(
                OpenAI, RateLimitError, AuthenticationError, APIStatusError,
                system_prompt, user_message, effective_model,
            )
        except ImportError:
            _logger.debug('openai package not installed — using urllib for Groq (zero-dependency path)')

        return self._extract_urllib(system_prompt, user_message, effective_model)

    def _extract_openai_sdk(self, OpenAI, RateLimitError, AuthenticationError, APIStatusError,
                             system_prompt, user_message, model):
        client = OpenAI(
            api_key=self._api_key,
            base_url=self.BASE_URL,
            timeout=float(self.DEFAULT_TIMEOUT),
            max_retries=0,
        )

        def _call():
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {'role': 'system', 'content': system_prompt},
                        {'role': 'user', 'content': user_message},
                    ],
                    temperature=0.0,
                    max_tokens=4096,
                    response_format={'type': 'json_object'},
                )
                return resp.choices[0].message.content
            except RateLimitError as exc:
                raise ProviderRateLimitError(
                    f'Groq rate limit hit. Wait a moment and try again. Detail: {exc}'
                )
            except AuthenticationError:
                raise ProviderAuthError(
                    'Groq API key is invalid. Go to Settings → Document Intelligence.'
                )
            except APIStatusError as exc:
                if exc.status_code == 429:
                    raise ProviderRateLimitError(str(exc))
                raise RuntimeError(f'Groq API error {exc.status_code}: {exc.message}')

        return _retry(_call, max_attempts=3, base_delay=5.0, retryable=(ProviderRateLimitError,))

    def _extract_urllib(self, system_prompt: str, user_message: str, model: str) -> str:
        """Call Groq using only Python stdlib — no pip install required."""
        url = f'{self.BASE_URL}/chat/completions'
        payload = {
            'model': model,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_message},
            ],
            'temperature': 0.0,
            'max_tokens': 4096,
            'response_format': {'type': 'json_object'},
        }
        headers = {
            'Authorization': f'Bearer {self._api_key}',
            'Content-Type': 'application/json',
        }

        def _call():
            data = _http_post(url, payload, headers, timeout=self.DEFAULT_TIMEOUT)
            try:
                return data['choices'][0]['message']['content']
            except (KeyError, IndexError) as exc:
                raise RuntimeError(f'Unexpected Groq response format: {exc}\n{data}')

        return _retry(_call, max_attempts=3, base_delay=5.0, retryable=(ProviderRateLimitError,))

    @staticmethod
    def _classify_error(exc) -> str:
        msg = str(exc)
        if '401' in msg or 'AuthenticationError' in type(exc).__name__:
            return 'Invalid Groq API key. Check Settings → Document Intelligence.'
        if '429' in msg or 'RateLimitError' in type(exc).__name__:
            return 'Groq rate limit hit. Wait a moment and try again.'
        return f'Groq error: {exc}'


# ════════════════════════════════════════════════════════════════════════════════
# Tier 3 — OpenAI — ZERO extra packages required
# ════════════════════════════════════════════════════════════════════════════════

class OpenAIProvider(AIProvider):
    name = 'openai'
    DEFAULT_MODEL = 'gpt-4o-mini'
    BASE_URL = 'https://api.openai.com/v1'
    DEFAULT_TIMEOUT = 60

    def __init__(self, api_key: str):
        cleaned = (api_key or '').strip()
        if not cleaned:
            raise ProviderAuthError(
                'OpenAI API key is not configured. '
                'Get one at https://platform.openai.com/api-keys '
                'then go to Settings → Document Intelligence.'
            )
        self._api_key = cleaned

    def ping(self) -> dict:
        url = f'{self.BASE_URL}/models'
        req = urllib.request.Request(
            url,
            headers={'Authorization': f'Bearer {self._api_key}'},
            method='GET',
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            names = [m['id'] for m in data.get('data', []) if 'gpt' in m['id']][:10]
            return {'ok': True, 'message': 'OpenAI API key is valid.', 'models': names}
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return {'ok': False, 'message': 'Invalid OpenAI API key.', 'models': []}
            if exc.code == 402:
                return {'ok': False, 'message': 'OpenAI account has no credits.', 'models': []}
            return {'ok': False, 'message': f'OpenAI error: HTTP {exc.code}', 'models': []}
        except Exception as exc:
            return {'ok': False, 'message': f'Cannot reach OpenAI: {exc}', 'models': []}

    def extract(self, system_prompt: str, user_message: str, model: str) -> str:
        effective_model = model or self.DEFAULT_MODEL
        _logger.info('OpenAI call: model=%s key=%s', effective_model, self.mask_key(self._api_key))

        try:
            from openai import OpenAI, RateLimitError, AuthenticationError, APIStatusError
            return self._extract_openai_sdk(
                OpenAI, RateLimitError, AuthenticationError, APIStatusError,
                system_prompt, user_message, effective_model,
            )
        except ImportError:
            _logger.debug('openai package not installed — using urllib for OpenAI')

        return self._extract_urllib(system_prompt, user_message, effective_model)

    def _extract_openai_sdk(self, OpenAI, RateLimitError, AuthenticationError, APIStatusError,
                             system_prompt, user_message, model):
        client = OpenAI(
            api_key=self._api_key,
            timeout=float(self.DEFAULT_TIMEOUT),
            max_retries=0,
        )

        def _call():
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {'role': 'system', 'content': system_prompt},
                        {'role': 'user', 'content': user_message},
                    ],
                    temperature=0.0,
                    max_tokens=4096,
                    response_format={'type': 'json_object'},
                )
                return resp.choices[0].message.content
            except RateLimitError as exc:
                raise ProviderRateLimitError(f'OpenAI rate limit: {exc}')
            except AuthenticationError:
                raise ProviderAuthError('OpenAI API key is invalid.')
            except APIStatusError as exc:
                if exc.status_code == 429:
                    raise ProviderRateLimitError(str(exc))
                if exc.status_code == 402:
                    raise ProviderQuotaError('OpenAI account has no credits.')
                raise RuntimeError(f'OpenAI error {exc.status_code}: {exc.message}')

        return _retry(_call, max_attempts=3, base_delay=5.0, retryable=(ProviderRateLimitError,))

    def _extract_urllib(self, system_prompt: str, user_message: str, model: str) -> str:
        """Call OpenAI using only Python stdlib — no pip install required."""
        url = f'{self.BASE_URL}/chat/completions'
        payload = {
            'model': model,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_message},
            ],
            'temperature': 0.0,
            'max_tokens': 4096,
            'response_format': {'type': 'json_object'},
        }
        headers = {
            'Authorization': f'Bearer {self._api_key}',
            'Content-Type': 'application/json',
        }

        def _call():
            data = _http_post(url, payload, headers, timeout=self.DEFAULT_TIMEOUT)
            try:
                return data['choices'][0]['message']['content']
            except (KeyError, IndexError) as exc:
                raise RuntimeError(f'Unexpected OpenAI response format: {exc}')

        return _retry(_call, max_attempts=3, base_delay=5.0, retryable=(ProviderRateLimitError,))

    @staticmethod
    def _classify_error(exc) -> str:
        msg = str(exc)
        if '401' in msg:
            return 'Invalid OpenAI API key.'
        if '402' in msg or 'quota' in msg.lower():
            return 'OpenAI account has no credits.'
        if '429' in msg:
            return 'OpenAI rate limit hit. Try again in a moment.'
        return f'OpenAI error: {exc}'


# ════════════════════════════════════════════════════════════════════════════════
# Tier 3 — Anthropic (Claude) — requires anthropic package
# ════════════════════════════════════════════════════════════════════════════════

class AnthropicProvider(AIProvider):
    name = 'anthropic'
    DEFAULT_MODEL = 'claude-haiku-4-5-20251001'
    BASE_URL = 'https://api.anthropic.com/v1'
    DEFAULT_TIMEOUT = 60

    def __init__(self, api_key: str):
        cleaned = (api_key or '').strip()
        if not cleaned:
            raise ProviderAuthError(
                'Anthropic API key is not configured. '
                'Get one at https://console.anthropic.com '
                'then go to Settings → Document Intelligence.'
            )
        self._api_key = cleaned

    def ping(self) -> dict:
        # Use the anthropic SDK if available; otherwise try a lightweight token-count call
        try:
            import anthropic
        except ImportError:
            return {
                'ok': False,
                'message': (
                    'anthropic package not installed. '
                    'Run: pip install anthropic\n'
                    'Or switch to Groq — same quality, zero pip installs.'
                ),
                'models': [],
            }
        try:
            client = anthropic.Anthropic(api_key=self._api_key)
            client.messages.count_tokens(
                model=self.DEFAULT_MODEL,
                messages=[{'role': 'user', 'content': 'ping'}],
            )
            return {
                'ok': True,
                'message': 'Anthropic API key is valid.',
                'models': ['claude-haiku-4-5-20251001', 'claude-sonnet-4-6', 'claude-opus-4-7'],
            }
        except Exception as exc:
            return {'ok': False, 'message': self._classify_error(exc), 'models': []}

    def extract(self, system_prompt: str, user_message: str, model: str) -> str:
        try:
            import anthropic
        except ImportError:
            raise ProviderAuthError(
                'The Anthropic provider requires the anthropic Python package.\n'
                'Run:  pip install anthropic\n\n'
                'Tip: Switch to Groq in Settings → Document Intelligence — '
                'it provides the same AI quality with ZERO extra packages.'
            )

        effective_model = model or self.DEFAULT_MODEL
        _logger.info('Anthropic call: model=%s key=%s', effective_model, self.mask_key(self._api_key))

        client = anthropic.Anthropic(api_key=self._api_key, timeout=float(self.DEFAULT_TIMEOUT), max_retries=0)

        def _call():
            try:
                msg = client.messages.create(
                    model=effective_model,
                    max_tokens=4096,
                    system=system_prompt,
                    messages=[{'role': 'user', 'content': user_message}],
                )
                return msg.content[0].text
            except anthropic.RateLimitError as exc:
                raise ProviderRateLimitError(f'Anthropic rate limit: {exc}')
            except anthropic.AuthenticationError:
                raise ProviderAuthError('Anthropic API key is invalid.')
            except anthropic.APIStatusError as exc:
                if exc.status_code in (429, 529):
                    raise ProviderRateLimitError(str(exc))
                raise RuntimeError(f'Anthropic error {exc.status_code}: {exc.message}')

        return _retry(_call, max_attempts=3, base_delay=5.0, retryable=(ProviderRateLimitError,))

    @staticmethod
    def _classify_error(exc) -> str:
        msg = str(exc)
        if '401' in msg or 'AuthenticationError' in type(exc).__name__:
            return 'Invalid Anthropic API key.'
        if '429' in msg or '529' in msg:
            return 'Anthropic rate limit or overload. Try again in a moment.'
        return f'Anthropic error: {exc}'


# ════════════════════════════════════════════════════════════════════════════════
# Registry + factory
# ════════════════════════════════════════════════════════════════════════════════

PROVIDER_REGISTRY: dict = {
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
