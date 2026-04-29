"""
AI extraction layer — sends raw text to OpenAI and gets structured JSON back.
"""
import json
import logging

_logger = logging.getLogger(__name__)

# ── Prompt templates ──────────────────────────────────────────────────────────

AUTO_SYSTEM_PROMPT = """You are a document intelligence engine.
Your job is to analyze raw text extracted from a business document and return structured data as JSON.

Always return a JSON object with these keys (use null when a value is not found):
{
  "document_type": "<invoice|receipt|contract|cv|form|general>",
  "suggested_action": "<create_invoice|create_partner|create_hr_applicant|store_only>",
  "vendor": null,
  "date": null,
  "total": null,
  "currency": null,
  "tax": null,
  "reference": null,
  "contact_name": null,
  "phone": null,
  "email": null,
  "address": null,
  "confidence": <0.0-1.0>,
  "notes": "<any caveats or issues noticed>"
}

Rules:
- dates must be in YYYY-MM-DD format
- totals must be numeric (no currency symbols)
- confidence is your estimate of extraction accuracy
- If additional fields exist that are relevant, add them to the JSON
- Return ONLY valid JSON, no markdown, no explanation
"""

CUSTOM_SYSTEM_PROMPT = """You are a document intelligence engine.
Extract ONLY the following fields from the document text: {fields}

Return a JSON object with those field names as keys and extracted values as values.
Use null when a value is not found.
Add a "confidence" key (0.0-1.0) and a "notes" key for any caveats.
Return ONLY valid JSON, no markdown, no explanation.
"""

TEMPLATE_SYSTEM_PROMPT = """You are a document intelligence engine.
This document is a {doc_type}.
{hint}

Extract the following fields: {fields}

Return a JSON object with those field names as keys.
Use null when a value is not found.
Add "confidence" (0.0-1.0), "document_type", "suggested_action", and "notes" keys.
Return ONLY valid JSON, no markdown, no explanation.
"""


# ── Main function ─────────────────────────────────────────────────────────────


def extract_with_ai(
    raw_text: str,
    api_key: str,
    model: str = 'gpt-4o-mini',
    mode: str = 'auto',
    fields: list = None,
    template=None,
    extra_prompt: str = '',
    provider: str = 'openai',
    groq_api_key: str = '',
) -> dict:
    """
    :param raw_text:     text from OCR / parser
    :param api_key:      OpenAI API key
    :param model:        model name
    :param mode:         'auto' | 'custom' | 'template'
    :param fields:       list of field names (for custom / template mode)
    :param template:     ExtractionTemplate record (for template mode)
    :param extra_prompt: extra instructions from the user
    :param provider:     'openai' or 'groq'
    :param groq_api_key: Groq API key (used when provider='groq')
    :return:             dict with extracted data
    """
    if provider == 'groq':
        if not groq_api_key:
            raise RuntimeError(
                'Groq API key is not configured. '
                'Go to Settings → Document Intelligence to add your Groq key.'
            )
    else:
        if not api_key:
            raise RuntimeError(
                'OpenAI API key is not configured. '
                'Go to Settings → Document Intelligence to add your key.'
            )

    if not raw_text or not raw_text.strip():
        raise RuntimeError('No text was extracted from the document.')

    system_prompt = _build_system_prompt(mode, fields, template)
    user_message = raw_text[:12000]
    if extra_prompt:
        user_message = f'Additional context: {extra_prompt}\n\n---\n\n{user_message}'

    try:
        if provider == 'groq':
            response_text = _call_groq(groq_api_key, model, system_prompt, user_message)
        else:
            response_text = _call_openai(api_key, model, system_prompt, user_message)
    except RuntimeError as e:
        # If OpenAI quota exceeded, automatically fall back to Groq if possible
        if 'insufficient_quota' in str(e) and provider != 'groq':
            _logger.warning('OpenAI quota exhausted, falling back to Groq')
            if not groq_api_key:
                raise RuntimeError('Groq API key not configured for fallback.')
            # Use Groq default model when falling back
            fallback_model = 'llama-3.3-70b-versatile'
            response_text = _call_groq(groq_api_key, fallback_model, system_prompt, user_message)
        else:
            raise

    return _parse_response(response_text)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_system_prompt(mode, fields, template):
    if mode == 'auto':
        return AUTO_SYSTEM_PROMPT
    elif mode == 'custom':
        field_str = ', '.join(fields or [])
        return CUSTOM_SYSTEM_PROMPT.format(fields=field_str)
    elif mode == 'template' and template:
        field_list = template.get_fields_list()
        field_str = ', '.join(field_list)
        hint = template.prompt_hint or ''
        return TEMPLATE_SYSTEM_PROMPT.format(
            doc_type=template.document_type,
            hint=hint,
            fields=field_str,
        )
    return AUTO_SYSTEM_PROMPT


def _call_openai(api_key: str, model: str, system: str, user: str) -> str:
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError(
            'openai Python package is not installed. Run: pip install openai'
        )

    client = OpenAI(api_key=api_key)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': user},
            ],
            temperature=0.0,
            max_tokens=2000,
        )
        return response.choices[0].message.content
    except Exception as e:
        # Propagate OpenAI errors as RuntimeError for fallback handling
        raise RuntimeError(str(e))


def _call_groq(api_key: str, model: str, system: str, user: str) -> str:
    """
    Groq uses an OpenAI-compatible API.
    Default fast model: llama-3.3-70b-versatile
    """
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError(
            'openai Python package is not installed. Run: pip install openai'
        )

    # Groq's default model if the user hasn't set one
    groq_model = model if model else 'llama-3.3-70b-versatile'

    client = OpenAI(
        api_key=api_key,
        base_url='https://api.groq.com/openai/v1',
    )
    response = client.chat.completions.create(
        model=groq_model,
        messages=[
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ],
        temperature=0.0,
        max_tokens=2000,
    )
    return response.choices[0].message.content


def _parse_response(text: str) -> dict:
    """Parse JSON from model output, tolerating markdown code fences."""
    text = text.strip()
    # Strip markdown fences if present
    if text.startswith('```'):
        lines = text.splitlines()
        text = '\n'.join(lines[1:-1] if lines[-1].startswith('```') else lines[1:])

    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError('Response is not a JSON object')
        return data
    except json.JSONDecodeError as e:
        _logger.error('AI response is not valid JSON: %s\nRaw: %s', e, text[:500])
        raise RuntimeError(
            f'AI returned invalid JSON: {e}. Raw response: {text[:200]}'
        )
