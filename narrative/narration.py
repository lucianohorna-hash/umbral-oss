"""Generation orchestration, separate from model providers and canonical rules."""
from threading import Lock
from uuid import NAMESPACE_URL, uuid5
from sqlalchemy import select
from .db import Turn
from .schemas import TurnInput
from .service import DomainError, serialize, transcript_fingerprint


class NarrationCoordinator:
    def __init__(self, engine, provider):
        self.engine, self.provider = engine, provider
        self.guard = Lock()
        self.locks = {}

    def generate(self, campaign_id, request):
        with self.guard:
            lock = self.locks.setdefault(campaign_id, Lock())
        with lock:
            if request.request_id:
                identity = str(uuid5(NAMESPACE_URL, f'umbral/http/turn/{campaign_id}/{request.request_id}'))
                with self.engine.db.session() as session:
                    prior = session.get(Turn, identity)
                    if prior:
                        basis = prior.provenance.get('generation', {})
                        if (prior.role != 'narrator' or not basis or
                            prior.canon_version != request.expected_version or
                            basis.get('requested_last_turn_id') != request.expected_last_turn_id):
                            raise DomainError(409, 'Este identificador ya corresponde a otra intervención o petición de narración.')
                        return serialize(prior)
            context = self.engine.context(campaign_id, 'narrator')
            if context['version'] != request.expected_version:
                raise DomainError(409, 'El canon cambió. Recarga antes de pedir una narración.')
            turns = self.engine.turns(campaign_id)['turns']
            last = turns[-1]['id'] if turns else None
            if 'expected_last_turn_id' in request.model_fields_set and request.expected_last_turn_id != last:
                raise DomainError(409, 'Hay nuevas intervenciones. Recarga antes de pedir una narración.')
            basis = dict(input_fingerprint=transcript_fingerprint(turns),
                requested_last_turn_id=request.expected_last_turn_id, last_turn_id=last,
                provider=self.provider.name)
            model_turns, recall, review_fingerprint = self.engine.narration_memory(campaign_id, turns)
            basis['review_fingerprint'] = review_fingerprint
            context['archival_recall'] = recall
            basis['recalled_turn_ids'] = [item['turn_id'] for item in recall['items']]
            try:
                content = self.provider.narrate(context, model_turns)
            except ValueError:
                raise
            except Exception:
                raise DomainError(502, 'El proveedor no respondió con una narración válida. La campaña sigue guardada.') from None
            return self.engine.add_turn(campaign_id, TurnInput(role='narrator', content=content,
                request_id=request.request_id), expected_version=request.expected_version,
                generation_basis=basis)
