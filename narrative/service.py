"""Application use cases. Every canonical mutation is a single audited transaction."""
from copy import deepcopy
import hashlib
import json
from uuid import NAMESPACE_URL, uuid5
from sqlalchemy import select
from .db import Universe, Record, Source, Proposal, Revision, Turn, uid, now
from .schemas import RecordInput, ProposalInput, Wizard, Acceptance, TurnInput, Advance
from .continuity import check
from .simulation import preview
from .identity import narrator_anchors
from .memory import character_projection, entry, public_entry


class DomainError(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail
        super().__init__(str(detail))


def serialize(row):
    return {column.name: deepcopy(getattr(row, column.name)) for column in row.__table__.columns}


def transcript_fingerprint(turns):
    rows = [[t['id'], t['role'], t['content'], t['protagonist_id']] for t in turns]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


class Engine:
    def __init__(self, db):
        self.db = db

    def _universe(self, session, identity):
        result = session.get(Universe, identity)
        if result is None:
            raise DomainError(404, 'Universo inexistente.')
        return result

    def _record(self, session, identity, kind=None):
        result = session.get(Record, identity)
        if result is None or result.deleted or (kind and result.kind != kind):
            raise DomainError(404, 'Registro inexistente o tipo incompatible.')
        return result

    def _records(self, session, universe_id):
        return {r.id: serialize(r) for r in session.scalars(select(Record).where(Record.universe_id == universe_id, Record.deleted == False))}

    def _source(self, session, universe_id, title, content):
        source = Source(id=uid(), universe_id=universe_id, title=title, content=content, sha256=hashlib.sha256(content.encode('utf-8')).hexdigest())
        session.add(source)
        session.flush()
        return source

    def _revision(self, session, universe, before, after, summary, proposal_id=None):
        universe.version += 1
        revision = Revision(id=uid(), universe_id=universe.id, version=universe.version, summary=summary, before=before, after=after, proposal_id=proposal_id)
        session.add(revision)
        session.flush()
        return serialize(revision)

    def _version(self, universe, expected):
        if universe.version != expected:
            raise DomainError(409, f'El canon cambió: versión actual {universe.version}. Recarga y revisa el borrador.')

    def universes(self):
        with self.db.session() as session:
            return [serialize(r) for r in session.scalars(select(Universe).order_by(Universe.created_at))]

    def create_universe(self, name, description='', is_demo=False):
        if not name.strip():
            raise DomainError(422, 'Escribe un nombre para el universo.')
        with self.db.session(True) as session:
            universe = Universe(id=uid(), name=name, description=description, is_demo=is_demo)
            session.add(universe)
            session.flush()
            return serialize(universe)

    def wizard(self, request: Wizard):
        with self.db.session(True) as session:
            if request.universe_id:
                universe = self._universe(session, request.universe_id)
            else:
                if not request.universe_name.strip():
                    raise DomainError(422, 'Escribe un nombre para el universo.')
                universe = Universe(id=uid(), name=request.universe_name, description='Creado por el usuario desde cero.', is_demo=False)
                session.add(universe)
                session.flush()
            source = self._source(session, universe.id, 'Declaraciones del asistente de creación', request.model_dump_json())
            created = {}
            def add(kind, name, data, world_id=None):
                validated = RecordInput(kind=kind, name=name, world_id=world_id, data=data, source_id=source.id)
                row = Record(id=uid(), universe_id=universe.id, **validated.model_dump())
                session.add(row)
                session.flush()
                created[row.id] = serialize(row)
                return row
            if request.world_id:
                world = self._record(session, request.world_id, 'world')
                if world.universe_id != universe.id:
                    raise DomainError(422, 'El mundo pertenece a otro universo.')
            else:
                world = add('world', request.world_name.strip() or 'Mundo por descubrir', dict(mode=request.mode, description='', established=request.mode == 'defined'))
            if request.protagonist_id:
                hero = self._record(session, request.protagonist_id, 'character')
                if hero.universe_id != universe.id or hero.world_id != world.id:
                    raise DomainError(422, 'El protagonista debe pertenecer a este universo y mundo.')
            else:
                hero = add('character', request.protagonist_name, dict(player_controlled=True, personality={'description': request.personality} if request.personality else {}), world.id)
            campaign = add('campaign', request.campaign_name, dict(protagonist_id=hero.id, premise=request.premise, mode=request.mode, current_day=world.data['current_day']), world.id)
            if request.world_rules.strip():
                add('rule', 'Reglas iniciales declaradas por el usuario', dict(statement=request.world_rules, certainty='confirmed'), world.id)
            self._revision(session, universe, {k: None for k in created}, created, 'Creación explícita de campaña: ' + request.campaign_name)
            return dict(universe_id=universe.id, world_id=world.id, campaign_id=campaign.id, protagonist_id=hero.id)

    def snapshot(self, universe_id):
        with self.db.session() as session:
            universe = self._universe(session, universe_id)
            return dict(universe=serialize(universe), records=list(self._records(session, universe_id).values()),
                        proposals=[serialize(p) for p in session.scalars(select(Proposal).where(Proposal.universe_id == universe_id).order_by(Proposal.created_at.desc()))],
                        revisions=[{k: v for k, v in serialize(r).items() if k not in ['before', 'after']} for r in session.scalars(select(Revision).where(Revision.universe_id == universe_id).order_by(Revision.version.desc()))],
                        sources=[{k: v for k, v in serialize(r).items() if k != 'content'} for r in session.scalars(select(Source).where(Source.universe_id == universe_id))])

    def _new_proposal(self, session, universe, request):
        if request.expected_version is not None:
            self._version(universe, request.expected_version)
        p = Proposal(id=uid(), universe_id=universe.id, title=request.title, operation=request.operation, target_id=request.target_id, record=request.record.model_dump(), base_version=universe.version)
        session.add(p)
        session.flush()
        p.issues, _ = self._check(session, p)
        session.flush()
        return p

    def propose(self, universe_id, request: ProposalInput):
        with self.db.session(True) as session:
            universe = self._universe(session, universe_id)
            return serialize(self._new_proposal(session, universe, request))

    def _proposal(self, session, identity):
        p = session.get(Proposal, identity)
        if not p:
            raise DomainError(404, 'Borrador inexistente.')
        if p.status != 'pending':
            raise DomainError(409, 'Este borrador ya fue resuelto.')
        return p

    def _check(self, session, proposal):
        validated = RecordInput.model_validate(proposal.record)
        records = self._records(session, proposal.universe_id)
        issues = []
        # The bridge wraps the caller's reference to retain transport provenance.
        # Corrections can wrap it again; every original reference still needs the
        # same checks as one entered directly in the owner interface.
        references, reference = [], validated.source_ref
        for _ in range(16):
            references.append(reference)
            if 'original_reference' not in reference:
                break
            reference = reference['original_reference']
            if not isinstance(reference, dict):
                issues.append(dict(code='source_reference', severity='error', message='La referencia original debe ser un objeto de procedencia.', suggestion='Conserva la referencia original sin cambiar su estructura.'))
                break
        else:
            issues.append(dict(code='source_reference_depth', severity='error', message='La cadena de referencias originales es demasiado profunda para revisarla.', suggestion='Usa la referencia directa de la intervención original.'))
        for reference in references:
            for warning in reference.get('import_warnings', []):
                issues.append(dict(code='import_review', severity='warning', message=str(warning), suggestion='Contrasta el fragmento con su fuente original.'))
        if proposal.operation == 'replace' and proposal.target_id not in records:
            issues.append(dict(code='target_missing', severity='error', message='No existe el registro que se quiere corregir.', suggestion='Selecciona un registro del universo actual.'))
        if proposal.operation == 'create' and proposal.target_id:
            issues.append(dict(code='unexpected_target', severity='error', message='Una creación no puede reemplazar un registro.', suggestion='Usa una propuesta de corrección.'))
        if validated.source_id:
            source = session.get(Source, validated.source_id)
            if not source or source.universe_id != proposal.universe_id:
                issues.append(dict(code='source_universe', severity='error', message='La fuente no pertenece a este universo.', suggestion='Selecciona una fuente válida.'))
        turns = {t.id: serialize(t) for t in session.scalars(select(Turn).where(Turn.universe_id == proposal.universe_id))}
        for reference in references if validated.kind == 'detail' else []:
            if 'turn_id' not in reference:
                continue
            turn_id = reference['turn_id']
            turn = turns.get(turn_id) if isinstance(turn_id, str) else None
            turn_campaign = records.get(turn['campaign_id']) if turn else None
            if (not turn or (validated.campaign_id and turn['campaign_id'] != validated.campaign_id) or
                    (turn_campaign and turn_campaign['world_id'] != validated.world_id)):
                issues.append(dict(code='detail_turn_source', severity='error', message='La intervención de origen no pertenece al universo y campaña del detalle.', suggestion='Selecciona una intervención de esta campaña.'))
            elif 'excerpt' in reference and (not isinstance(reference['excerpt'], str) or not reference['excerpt'] or reference['excerpt'] not in turn['content']):
                issues.append(dict(code='detail_excerpt', severity='error', message='El extracto del detalle debe ser texto exacto de su intervención de origen.', suggestion='Copia el fragmento original sin modificarlo.'))
        detected, effects = check(validated.model_dump(), records, turns, proposal.target_id)
        return issues + detected, effects

    def review(self, proposal_id):
        with self.db.session(True) as session:
            p = self._proposal(session, proposal_id)
            p.issues, _ = self._check(session, p)
            p.base_version = self._universe(session, p.universe_id).version
            p.updated_at = now()
            session.flush()
            return serialize(p)

    def accept(self, proposal_id, request: Acceptance):
        with self.db.session(True) as session:
            p = self._proposal(session, proposal_id)
            universe = self._universe(session, p.universe_id)
            self._version(universe, request.expected_version)
            self._version(universe, p.base_version)
            if not request.decision.strip():
                raise DomainError(422, 'La validación necesita una decisión explícita del usuario.')
            issues, effects = self._check(session, p)
            if any(i['severity'] in ['error', 'critical'] for i in issues):
                raise DomainError(422, dict(message='Hay contradicciones que bloquean la incorporación.', issues=issues))
            if any(i['severity'] == 'warning' for i in issues) and not request.acknowledge_warnings:
                raise DomainError(422, dict(message='Revisa y reconoce las advertencias antes de validar.', issues=issues))
            if any(i['code'] == 'protected_character_change' for i in issues) and not request.authorize_character_change:
                raise DomainError(422, 'El cambio de identidad, personalidad o estado psicológico requiere autorización específica del usuario, además de la revisión general.')
            records = self._records(session, universe.id)
            record = deepcopy(p.record)
            if not record.get('source_id'):
                source = self._source(session, universe.id, 'Decisión del usuario: ' + p.title, json.dumps(dict(decision=request.decision, proposal=record), ensure_ascii=False))
                record['source_id'] = source.id
            record_id = p.target_id if p.operation == 'replace' else uid()
            before = {identity: records[identity] for identity in effects}
            before[record_id] = records.get(record_id)
            for identity, projected in effects.items():
                row = session.get(Record, identity)
                row.data = deepcopy(projected['data'])
                row.revision += 1
                row.updated_at = now()
            if p.operation == 'create':
                row = Record(id=record_id, universe_id=universe.id, **record)
                session.add(row)
            else:
                row = session.get(Record, record_id)
                for field, value in record.items():
                    setattr(row, field, value)
                row.revision += 1
                row.updated_at = now()
            session.flush()
            after = {identity: serialize(session.get(Record, identity)) for identity in before}
            p.status, p.decision, p.issues, p.updated_at = 'accepted', request.decision, issues, now()
            revision = self._revision(session, universe, before, after, request.decision, p.id)
            return dict(record=serialize(row), revision=revision, version=universe.version)

    def reject(self, proposal_id, decision):
        if not decision.strip():
            raise DomainError(422, 'Indica el motivo de rechazo.')
        with self.db.session(True) as session:
            p = self._proposal(session, proposal_id)
            p.status, p.decision, p.updated_at = 'rejected', decision, now()
            return serialize(p)

    def revert(self, universe_id, expected_version, decision):
        if not decision.strip():
            raise DomainError(422, 'La reversión requiere un motivo explícito.')
        with self.db.session(True) as session:
            universe = self._universe(session, universe_id)
            self._version(universe, expected_version)
            previous = session.scalar(select(Revision).where(Revision.universe_id == universe_id).order_by(Revision.version.desc()))
            if not previous:
                raise DomainError(409, 'No hay una revisión que revertir.')
            before, after = {}, {}
            for identity, snapshot in previous.before.items():
                row = session.get(Record, identity)
                before[identity] = serialize(row) if not row.deleted else None
                if snapshot is None:
                    row.deleted = True
                else:
                    for field in ['name', 'kind', 'world_id', 'campaign_id', 'source_id', 'source_ref', 'data', 'deleted']:
                        setattr(row, field, deepcopy(snapshot[field]))
                row.revision += 1
                row.updated_at = now()
                after[identity] = serialize(row) if not row.deleted else None
            return self._revision(session, universe, before, after, f'Reversión de v{previous.version}: {decision}')

    def add_turn(self, campaign_id, request: TurnInput, expected_version=None, generation_basis=None):
        with self.db.session(True) as session:
            campaign = self._record(session, campaign_id, 'campaign')
            universe = self._universe(session, campaign.universe_id)
            if expected_version is not None and request.expected_version is not None and expected_version != request.expected_version:
                raise DomainError(422, 'Las versiones indicadas para esta intervención no coinciden.')
            if expected_version is None:
                expected_version = request.expected_version
            cast = [member.model_dump() for member in request.cast]
            turn_id = str(uuid5(NAMESPACE_URL, f'umbral/http/turn/{campaign.id}/{request.request_id}')) if request.request_id else uid()
            previous = session.get(Turn, turn_id)
            if previous:
                original = session.get(Proposal, previous.proposal_id) if previous.proposal_id else None
                original_cast = original.record['data'].get('cast', []) if original else []
                if (previous.campaign_id != campaign.id or previous.role != request.role or
                        previous.content != request.content or original_cast != cast or
                        (previous.role == 'narrator' and original is None) or
                        (expected_version is not None and previous.canon_version != expected_version)):
                    raise DomainError(409, 'Este identificador de envío ya se usó con otra intervención, reparto o versión.')
                # Acknowledging a committed write does not depend on the present
                # canon version. No turn, source or proposal is created again.
                return serialize(previous)
            if expected_version is not None:
                self._version(universe, expected_version)
            if generation_basis is not None:
                current = [serialize(t) for t in session.scalars(select(Turn).where(Turn.campaign_id == campaign_id).order_by(Turn.created_at, Turn.id))]
                if transcript_fingerprint(current) != generation_basis['input_fingerprint']:
                    raise DomainError(409, 'La conversación cambió mientras el narrador respondía. No se guardó una respuesta para otro momento; recarga antes de continuar.')
                expected_reviews = generation_basis.get('review_fingerprint')
                if expected_reviews and self._scene_reviews(session, campaign_id)[1] != expected_reviews:
                    raise DomainError(409, 'Cambió la revisión de una escena mientras el narrador respondía. Recarga antes de continuar.')
            turn = Turn(id=turn_id, universe_id=universe.id, campaign_id=campaign.id, protagonist_id=campaign.data['protagonist_id'], role=request.role, content=request.content, canon_version=universe.version)
            if generation_basis is not None:
                turn.provenance = {'generation': deepcopy(generation_basis)}
            session.add(turn)
            session.flush()
            if request.role == 'narrator':
                source = self._source(session, universe.id, 'Intervención de narrador ' + turn.id, request.content)
                reference = {'turn_id': turn.id}
                if request.request_id:
                    reference['request_id'] = request.request_id
                proposal = self._new_proposal(session, universe, ProposalInput(title='Escena: ' + request.content[:80], record=RecordInput(kind='event', name='Escena narrada', world_id=campaign.world_id, campaign_id=campaign.id, source_id=source.id, source_ref=reference, data=dict(summary=request.content, certainty='unknown', day=campaign.data['current_day'], cast=cast))))
                turn.proposal_id = proposal.id
            session.flush()
            return serialize(turn)

    def turns(self, campaign_id):
        with self.db.session() as session:
            campaign = self._record(session, campaign_id, 'campaign')
            return dict(campaign=serialize(campaign), turns=[serialize(t) for t in session.scalars(select(Turn).where(Turn.campaign_id == campaign_id).order_by(Turn.created_at, Turn.id))])

    def review_player_message(self, campaign_id, turn_id, request):
        """Owner-only attribution review, never exposed to model tool dispatch.

        Keep the original text/source intact. Confirmation authorizes that exact
        intervention; any resulting world changes still require canon review.
        """
        with self.db.session(True) as session:
            campaign = self._record(session, campaign_id, 'campaign')
            turn = session.get(Turn, turn_id)
            if not turn or turn.campaign_id != campaign.id or turn.universe_id != campaign.universe_id:
                raise DomainError(404, 'Intervención inexistente en esta campaña.')
            if turn.content != request.content:
                raise DomainError(409, 'El texto revisado no coincide con la intervención guardada.')
            previous = turn.provenance.get('review')
            if previous:
                if previous['decision'] != request.decision:
                    raise DomainError(409, 'Esta atribución ya fue revisada con otra decisión.')
                return serialize(turn)
            if turn.role != 'player_pending' or turn.provenance.get('producer') != 'mcp':
                raise DomainError(422, 'Esta intervención no es un mensaje externo por confirmar.')
            universe = self._universe(session, campaign.universe_id)
            self._version(universe, request.expected_version)
            if request.decision == 'confirm' and turn.protagonist_id != campaign.data['protagonist_id']:
                raise DomainError(409, 'El protagonista cambió. Esta intervención no puede atribuirse al personaje actual.')
            review = dict(decision=request.decision, reviewed_at=now(), previous_role=turn.role,
                          actor='user', canon_version=universe.version)
            source = self._source(session, universe.id, 'Revisión humana de atribución ' + turn.id,
                                  json.dumps(dict(turn_id=turn.id, content=turn.content, **review), ensure_ascii=False))
            review['source_id'] = source.id
            turn.provenance = {**turn.provenance, 'review': review}
            turn.role = 'user' if request.decision == 'confirm' else 'player_rejected'
            session.flush()
            return serialize(turn)

    def switch_protagonist(self, campaign_id, character_id, expected_version, reason):
        if not reason.strip():
            raise DomainError(422, 'Indica el motivo del cambio de protagonista.')
        with self.db.session(True) as session:
            campaign = self._record(session, campaign_id, 'campaign')
            universe = self._universe(session, campaign.universe_id)
            self._version(universe, expected_version)
            hero = self._record(session, character_id, 'character')
            if hero.universe_id != universe.id or hero.world_id != campaign.world_id:
                raise DomainError(422, 'El protagonista debe pertenecer al mismo universo y mundo de la campaña.')
            before = {campaign.id: serialize(campaign)}
            campaign.data = {**campaign.data, 'protagonist_id': hero.id}
            campaign.revision += 1
            campaign.updated_at = now()
            self._revision(session, universe, before, {campaign.id: serialize(campaign)}, 'Cambio de protagonista: ' + reason)
            return serialize(campaign)

    def _scene_reviews(self, session, campaign_id):
        rows = list(session.execute(select(Turn.id, Proposal.status, Proposal.record).join(
            Proposal, Turn.proposal_id == Proposal.id).where(Turn.campaign_id == campaign_id).order_by(Turn.id)))
        fingerprint = hashlib.sha256(json.dumps([list(row) for row in rows],
            ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return rows, fingerprint

    def narration_memory(self, campaign_id, turns):
        from .recall import prepare_narration_memory
        with self.db.session() as session:
            self._record(session, campaign_id, 'campaign')
            if any(turn['campaign_id'] != campaign_id for turn in turns):
                raise DomainError(422, 'La memoria narrativa no puede mezclar conversaciones de campañas distintas.')
            rows, fingerprint = self._scene_reviews(session, campaign_id)
            statuses = {identity: dict(status=status, source_id=record.get('source_id'),
                certainty=record.get('data', {}).get('certainty', 'unknown'))
                for identity, status, record in rows}
            recent, recall = prepare_narration_memory(turns, statuses)
            return recent, recall, fingerprint

    def context(self, campaign_id, audience='character'):
        if audience not in ['character', 'narrator']:
            raise DomainError(422, 'Audiencia inválida.')
        with self.db.session() as session:
            campaign = self._record(session, campaign_id, 'campaign')
            universe = self._universe(session, campaign.universe_id)
            records = self._records(session, universe.id)
            hero_id = campaign.data['protagonist_id']
            if audience == 'narrator':
                visible = list(records.values())
            else:
                visible = character_projection(records, hero_id)
            context = dict(universe_id=universe.id, campaign_id=campaign.id, protagonist_id=hero_id, version=universe.version, audience=audience, records=visible,
                        instructions=['No decidas acciones significativas del protagonista.', 'Una creencia nunca equivale automáticamente a un hecho.', 'Los borradores y la conversación no son canon validado.'])
            if audience == 'narrator':
                context['instructions'] += ['El archivo delimita lo establecido, no todo lo que puede existir: desarrolla el mundo todavía abierto mediante escenas propuestas coherentes.',
                    'No atribuyas al pasado ni al canon lo que acabas de crear. Una incógnita histórica o un secreto establecido conserva su incertidumbre.',
                    'Al explorar, ofrece un resultado concreto y observable de la intención del jugador, no otro tramo vacío que solo admita seguir caminando.']
                context['continuity_anchors'] = narrator_anchors(records)
                context['instructions'] += ['Reutiliza la identidad y el nombre guardados de cada NPC, también al volver a un lugar.',
                    'El paso del tiempo y la extensión de la conversación no cambian personalidad, emociones ni papeles por sí solos.',
                    'Un gesto cortés puntual no convierte a un personaje altanero en una persona amable.',
                    'Declara en event.cast los identificadores, nombres y rasgos utilizados; no inventes sustitutos para puestos ya ocupados.',
                    'Conserva los detalles pequeños por su identificador; parecidos de símbolos, materiales o rasgos no demuestran identidad ni conexión.',
                    'Las conexiones de detalles conservan su certeza propia; busca el archivo antes de afirmar una relación histórica.']
            else:
                context['instructions'].append('Lo no establecido sigue desconocido para el personaje; esta vista no autoriza a completar secretos ni recuerdos.')
            return context

    def _memory_entries(self, session, universe_id, audience, campaign_id):
        self._universe(session, universe_id)
        if audience not in ['narrator', 'character']:
            raise DomainError(422, 'Audiencia inválida.')
        if audience == 'character' and not campaign_id:
            raise DomainError(422, 'La memoria del personaje requiere una campaña.')
        campaign = self._record(session, campaign_id, 'campaign') if campaign_id else None
        if campaign and campaign.universe_id != universe_id:
            raise DomainError(404, 'Campaña inexistente en este universo.')
        if audience == 'character':
            records = self._records(session, universe_id)
            return [entry('record', r['id'], r['name'], r, canonical=True,
                          certainty=r['data'].get('certainty'), status='canonical',
                          world_id=r.get('world_id'), campaign_id=r.get('campaign_id'), kind=r['kind'])
                    for r in character_projection(records, campaign.data['protagonist_id'])]

        def in_scope(row):
            if not campaign:
                return True
            return row.get('campaign_id') == campaign_id or (not row.get('campaign_id') and row.get('world_id') in [None, campaign.world_id])

        entries, source_links = [], {}
        def link_source(source_id, world_id, linked_campaign):
            if source_id:
                source_links.setdefault(source_id, []).append((world_id, linked_campaign))
        for row in session.scalars(select(Record).where(Record.universe_id == universe_id).order_by(Record.created_at, Record.id)):
            r = serialize(row)
            link_source(r['source_id'], r['world_id'], r['campaign_id'])
            if in_scope(r):
                entries.append(entry('record', r['id'], r['name'], r, canonical=not r['deleted'],
                                     certainty=r['data'].get('certainty'), status='archived' if r['deleted'] else 'canonical',
                                     world_id=r['world_id'], campaign_id=r['campaign_id'], source_id=r['source_id'], kind=r['kind'], created_at=r['created_at']))
        proposals = {p.id: serialize(p) for p in session.scalars(select(Proposal).where(Proposal.universe_id == universe_id).order_by(Proposal.created_at, Proposal.id))}
        for p in proposals.values():
            r = p['record']
            link_source(r.get('source_id'), r.get('world_id'), r.get('campaign_id'))
            if in_scope(r):
                entries.append(entry('proposal', p['id'], p['title'], p, status=p['status'], certainty=r['data'].get('certainty'),
                                     world_id=r.get('world_id'), campaign_id=r.get('campaign_id'), source_id=r.get('source_id'),
                                     kind=r['kind'], target_id=p['target_id'], created_at=p['created_at']))
        for row in session.scalars(select(Turn).where(Turn.universe_id == universe_id).order_by(Turn.created_at, Turn.id)):
            t = serialize(row)
            turn_campaign = session.get(Record, t['campaign_id'])
            turn_world = turn_campaign.world_id if turn_campaign else None
            provenance_source = t['provenance'].get('source_id')
            link_source(provenance_source, turn_world, t['campaign_id'])
            link_source(t['provenance'].get('review', {}).get('source_id'), turn_world, t['campaign_id'])
            if campaign_id and t['campaign_id'] != campaign_id:
                continue
            p = proposals.get(t['proposal_id'])
            role_label = {'narrator': 'Narrador: ', 'user': 'Jugador: ', 'player_pending': 'Mensaje por confirmar: ',
                          'player_rejected': 'Atribución rechazada: '}.get(t['role'], 'Intervención: ')
            entries.append(entry('turn', t['id'], role_label + t['content'][:80], t,
                                 status=p['status'] if p else t['role'] if t['role'].startswith('player_') else 'unvalidated', world_id=turn_world,
                                 campaign_id=t['campaign_id'], source_id=p['record'].get('source_id') if p else provenance_source,
                                 role=t['role'], proposal_id=t['proposal_id'], created_at=t['created_at']))
        for row in session.scalars(select(Source).where(Source.universe_id == universe_id).order_by(Source.created_at, Source.id)):
            s = serialize(row)
            links = source_links.get(s['id'], [])
            if campaign and links and not any(in_scope(dict(world_id=w, campaign_id=c)) for w, c in links):
                continue
            # Unassigned imports remain searchable; their scope is explicitly unknown.
            worlds = {w for w, _ in links if w}
            campaigns = {c for _, c in links if c}
            entries.append(entry('source', s['id'], s['title'], s, status='source', source_id=s['id'],
                                 world_id=next(iter(worlds)) if len(worlds) == 1 else None,
                                 campaign_id=next(iter(campaigns)) if len(campaigns) == 1 else None,
                                 linked_campaign_ids=sorted(campaigns), created_at=s['created_at']))
        return entries

    def search_memory(self, universe_id, query='', audience='narrator', campaign_id=None, offset=0, limit=25):
        if not isinstance(query, str) or type(offset) is not int or type(limit) is not int or offset < 0 or limit < 1:
            raise DomainError(422, 'La búsqueda requiere texto, offset no negativo y limit positivo.')
        with self.db.session() as session:
            found = [result for item in self._memory_entries(session, universe_id, audience, campaign_id)
                     if (result := public_entry(item, query)) is not None]
            end = offset + limit
            return dict(query=query, total=len(found), next_offset=end if end < len(found) else None, results=found[offset:end])

    def read_memory(self, universe_id, origin, item_id, audience='narrator', campaign_id=None):
        if origin not in ['record', 'turn', 'source', 'proposal']:
            raise DomainError(422, 'Origen de memoria inválido.')
        with self.db.session() as session:
            for item in self._memory_entries(session, universe_id, audience, campaign_id):
                if item['origin'] == origin and item['id'] == item_id:
                    return public_entry(item, full=True)
        raise DomainError(404, 'Entrada de memoria inexistente o no accesible para esta audiencia.')

    def advance(self, universe_id, request: Advance):
        with self.db.session(True) as session:
            universe = self._universe(session, universe_id)
            world = self._record(session, request.world_id, 'world')
            if world.universe_id != universe_id:
                raise DomainError(422, 'El mundo pertenece a otro universo.')
            records = self._records(session, universe_id)
            result = preview(serialize(world), records, request.days, request.relevant_character_ids)
            proposal = self._new_proposal(session, universe, ProposalInput(title=f'Avance de {request.days} días locales', record=RecordInput(kind='event', name=f"Intervalo local {result['start']}–{result['end']}", world_id=world.id, data=dict(summary=result['summary'], certainty='confirmed', day=result['end'], development='routine', simulation=result['simulation']))))
            return serialize(proposal)

    def import_text(self, universe_id, filename, content, campaign_id=None, world_id=None):
        from .importer import extract_candidates
        with self.db.session(True) as session:
            universe = self._universe(session, universe_id)
            for identity, kind in [(campaign_id, 'campaign'), (world_id, 'world')]:
                if identity and self._record(session, identity, kind).universe_id != universe_id:
                    raise DomainError(422, 'La importación no puede mezclar universos.')
            digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
            duplicate = session.scalar(select(Source).where(Source.universe_id == universe_id, Source.sha256 == digest))
            if duplicate:
                raise DomainError(409, 'Este contenido ya se importó o conservó como fuente; revisa sus borradores existentes.')
            records = self._records(session, universe_id)
            names = [r['name'] for r in records.values() if r['kind'] == 'character']
            extracted = extract_candidates(filename, content, names)
            source = self._source(session, universe_id, filename, content)
            proposals = []
            for candidate in extracted['candidates']:
                reference = {**candidate['source_ref'], 'import_warnings': candidate['warnings'] + extracted['warnings']}
                inp = RecordInput(kind=candidate['kind'], name=candidate['name'], world_id=world_id, campaign_id=campaign_id, source_id=source.id, source_ref=reference, data=candidate['data'])
                proposals.append(serialize(self._new_proposal(session, universe, ProposalInput(title=candidate['title'], record=inp))))
            return dict(source_id=source.id, proposals=proposals, count=len(proposals), warnings=extracted['warnings'], name_matches=extracted['name_matches'])

    def export_markdown(self, universe_id):
        snapshot = self.snapshot(universe_id)
        universe = snapshot['universe']
        lines = [f"# Biblia del universo: {universe['name']}", '', f"Versión del canon: {universe['version']}", '', 'Vista del narrador: puede contener secretos. Los borradores no se exportan como canon.', '']
        if universe['is_demo']:
            lines += ['**DEMOSTRACIÓN FICTICIA. No pertenece a las campañas reales.**', '']
        for record in sorted(snapshot['records'], key=lambda r: (r['kind'], r['name'])):
            lines += [f"## {record['name']} ({record['kind']})", '', f"ID: {record['id']} · revisión {record['revision']}", f"Mundo: {record['world_id'] or 'Sin establecer'} · campaña: {record['campaign_id'] or 'No exclusiva'}", f"Fuente: {record['source_id'] or 'Decisión registrada'}", '', '```json', json.dumps(record['data'], ensure_ascii=False, indent=2).replace('```', '\u0060\u0060\u0060'), '```', '']
        lines += ['## Historial de decisiones', '']
        lines += [f"- v{r['version']}: {r['summary']} ({r['created_at']})" for r in reversed(snapshot['revisions'])]
        return '\n'.join(lines)

    def search(self, universe_id, query):
        snapshot = self.snapshot(universe_id)
        needle = query.casefold().strip()
        return [r for r in snapshot['records'] if needle in (r['name'] + json.dumps(r['data'], ensure_ascii=False)).casefold()]
