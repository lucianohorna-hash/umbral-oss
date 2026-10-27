"""Optional model adapter. Providers can propose prose, never write canon."""
from typing import Protocol
import os
import json
import httpx
from .narrator_policy import NARRATOR_INSTRUCTIONS


class NarratorProvider(Protocol):
    name: str
    def narrate(self, context: dict, turns: list[dict]) -> str: ...


class ManualNarrator:
    name = 'manual'
    def narrate(self, context, turns):
        raise ValueError('Modo local manual: escribe o pega la intervención del narrador. No hace falta una API para guardar y continuar la campaña.')


class CompatibleHTTPNarrator:
    """Chat Completions compatible endpoint, including local model servers.

    No provider SDK or provider identifier is part of the domain model.
    The user configures a base URL ending in /v1 and a model identifier.
    """
    name = 'http-compatible'

    def __init__(self, base_url, model, api_key=''):
        self.base_url, self.model, self.api_key = base_url.rstrip('/'), model, api_key

    def narrate(self, context, turns):
        instructions = NARRATOR_INSTRUCTIONS
        messages = [dict(role='system', content=instructions), dict(role='system', content='Estado canónico del narrador (contiene secretos que no debes revelar sin descubrimiento):\n' + json.dumps(context, ensure_ascii=False))]
        # External attribution is not a human decision or narrator utterance.
        trusted_roles = [turn for turn in turns if turn['role'] in ('user', 'narrator')]
        for turn in trusted_roles[-30:]:
            messages.append(dict(role='user' if turn['role'] == 'user' else 'assistant', content=turn['content']))
        headers = {'Authorization': 'Bearer ' + self.api_key} if self.api_key else {}
        with httpx.Client(timeout=60, follow_redirects=False) as client:
            response = client.post(self.base_url + '/chat/completions', headers=headers, json=dict(model=self.model, messages=messages, temperature=0.6, max_tokens=1800))
            response.raise_for_status()
            content = response.json()['choices'][0]['message']['content']
        if not isinstance(content, str) or not content.strip() or len(content) > 50000:
            raise ValueError('El proveedor no devolvió una narración válida.')
        return content


def configured_provider():
    base, model = os.getenv('NARRATIVE_AI_BASE_URL'), os.getenv('NARRATIVE_AI_MODEL')
    if base and model:
        return CompatibleHTTPNarrator(base, model, os.getenv('NARRATIVE_AI_API_KEY', ''))
    return ManualNarrator()
