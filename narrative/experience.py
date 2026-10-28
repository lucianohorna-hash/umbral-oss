"""Player-facing conveniences, independent of model inference."""
from copy import deepcopy
from sqlalchemy import select
from .db import Record, Turn, Proposal, RecoveryPoint, Source, uid, now
from .schemas import NarrationPreferences, ProposalInput, RecordInput
from .memory import character_projection
from .recovery import metadata, restore_as_copy

STYLE = {
    'pace': {'balanced': 'Resume viajes rutinarios y desarrolla situaciones relevantes.', 'brisk': 'Usa un ritmo ágil; resuelve la intención completa y resume los tramos sin novedades.', 'detailed': 'Desarrolla escenas relevantes con calma, sin fragmentar artificialmente acciones rutinarias.'},
    'length': {'brief': 'Prefiere respuestas breves, sin omitir el resultado de la acción.', 'medium': 'Usa extensión media según lo que requiera la escena.', 'long': 'Puedes desarrollar respuestas extensas cuando aporten contenido, sin relleno.'},
    'tone': {'natural': 'Usa un tono natural y concreto.', 'adventure': 'Usa un tono de aventura sin imponer peligros ni misiones.', 'intimate': 'Usa un tono cercano y atento al diálogo, sin inventar emociones del protagonista.', 'dark': 'Usa un tono sobrio y oscuro compatible con el contexto; no inventes tragedias por estilo.'},
    'dialogue': {'balanced': 'Equilibra diálogo y descripción según la situación.', 'more': 'Da espacio a diálogos naturales cuando haya interlocutores presentes.', 'less': 'Prefiere diálogo conciso sin omitir respuestas importantes.'}}


class Experience:
    def __init__(self, engine):
        self.engine = engine

    def resume(self, campaign_id):
        e = self.engine
        with e.db.session() as session:
            campaign = e._record(session, campaign_id, 'campaign')
            records = e._records(session, campaign.universe_id)
            hero = records[campaign.data['protagonist_id']]
            # Only protagonist + explicitly acquired information. Never narrator turns/secrets.
            visible = character_projection(records, hero['id'])
            facts = [r for r in visible if r['kind'] in ('fact','event','detail','rule') and r.get('campaign_id') in (None,campaign.id)
                     and r.get('world_id') in (None,campaign.world_id)]
            facts.sort(key=lambda r: (r['data'].get('day') if r['data'].get('day') is not None else -1, r['id']))
            location_id = hero['data'].get('location_id')
            location = records.get(location_id, {}).get('name', 'Sin ubicación establecida')
            pending = session.scalars(select(Proposal).where(Proposal.universe_id == campaign.universe_id, Proposal.status == 'pending')).all()
            return dict(campaign=campaign.name, protagonist=hero['name'], location=location,
                day=campaign.data['current_day'], status=hero['data']['status'], injuries=hero['data'].get('injuries',[]),
                reminders=[dict(id=r['id'],name=r['name'],text=r['data'].get('summary') or r['data'].get('statement'),
                                certainty=r['data'].get('certainty'),belief=r['data'].get('belief')) for r in facts[-8:]],
                pending_scenes=sum(1 for p in pending if p.record.get('campaign_id') == campaign_id),
                notice='Resumen del canon conocido por tu protagonista. No resume escenas sin validar ni supone quién lo acompaña.')

    def preferences(self, campaign_id, preferences=None, expected_version=None):
        e = self.engine
        with e.db.session(preferences is not None) as session:
            campaign = e._record(session, campaign_id, 'campaign')
            if preferences is None:
                return NarrationPreferences.model_validate(campaign.data.get('narration_preferences', {})).model_dump()
            universe = e._universe(session,campaign.universe_id)
            e._version(universe, expected_version)
            from .service import serialize
            before = serialize(campaign)
            campaign.data = {**campaign.data, 'narration_preferences': preferences.model_dump()}
            campaign.revision += 1
            campaign.updated_at = now()
            session.flush()
            e._revision(session,universe,{campaign.id:before},{campaign.id:serialize(campaign)},'Preferencias de narración elegidas por el usuario')
            return campaign.data['narration_preferences']

    def correction(self, campaign_id, turn_id, request):
        from .service import DomainError, serialize
        e = self.engine
        with e.db.session(True) as session:
            campaign = e._record(session,campaign_id,'campaign')
            universe = e._universe(session,campaign.universe_id)
            e._version(universe,request.expected_version)
            turn = session.get(Turn,turn_id)
            if not turn or turn.campaign_id != campaign_id or turn.role != 'narrator':
                raise DomainError(404,'Escena del narrador inexistente en esta campaña.')
            original = session.get(Proposal,turn.proposal_id) if turn.proposal_id else None
            existing = [t for t in session.scalars(select(Turn).where(Turn.campaign_id == campaign_id))
                        if t.provenance.get('corrects_turn_id') == turn_id and
                        session.get(Proposal,t.proposal_id).status != 'rejected']
            if existing:
                raise DomainError(409,'Esta escena ya tiene una corrección activa. Corrige o revisa esa propuesta.')
            target = next((r for r in session.scalars(select(Record).where(Record.universe_id == campaign.universe_id,Record.kind=='event',Record.deleted==False))
                           if r.source_ref.get('turn_id') == turn_id),None)
            new_id = uid()
            source = e._source(session,universe.id,'Corrección explícita de escena',request.model_dump_json())
            body = deepcopy(target.data if target else original.record['data'] if original else
                            dict(certainty='unknown',day=campaign.data['current_day'],actions=[]))
            body['summary'] = request.content
            rec = RecordInput(kind='event',name='Corrección de escena',world_id=campaign.world_id,campaign_id=campaign.id,
                source_id=source.id,source_ref=dict(turn_id=new_id,corrects_turn_id=turn_id,reason=request.reason,prose_only=bool(target)),data=body)
            p = e._new_proposal(session,universe,ProposalInput(title='Corrección: '+request.reason[:220],operation='replace' if target else 'create',
                target_id=target.id if target else None,expected_version=universe.version,record=rec))
            replacement = Turn(id=new_id,universe_id=universe.id,campaign_id=campaign.id,protagonist_id=turn.protagonist_id,
                role='narrator',content=request.content,proposal_id=p.id,canon_version=universe.version,
                provenance=dict(producer='user_correction',corrects_turn_id=turn.id,reason=request.reason,source_id=source.id))
            session.add(replacement)
            if original and original.status == 'pending':
                original.status,original.decision,original.updated_at='rejected','Sustituida por una propuesta corregida: '+request.reason,now()
            later=list(session.scalars(select(Turn).where(Turn.campaign_id==campaign_id,Turn.created_at>turn.created_at).order_by(Turn.created_at)))
            session.flush()
            return dict(proposal=serialize(p),turn=serialize(replacement),later_turns=[dict(id=t.id,created_at=t.created_at) for t in later if t.id != new_id],
                warning='Revisa las escenas posteriores: podrían depender de lo corregido. No se han reescrito. Los efectos materiales se conservan en la propuesta y deben revisarse.')

    def recovery_points(self,universe_id):
        with self.engine.db.session() as session:
            self.engine._universe(session,universe_id)
            return [metadata(p) for p in session.scalars(select(RecoveryPoint).where(RecoveryPoint.universe_id==universe_id).order_by(RecoveryPoint.bucket.desc()))]

    def recover(self,universe_id,point_id,label):
        from .service import DomainError
        with self.engine.db.session(True) as session:
            self.engine._universe(session,universe_id)
            point=session.get(RecoveryPoint,point_id)
            if not point or point.universe_id != universe_id:
                raise DomainError(404,'Punto de recuperación inexistente.')
            try:
                identity=restore_as_copy(session,point,label)
            except ValueError as error:
                raise DomainError(422,str(error)) from None
            return dict(universe_id=identity)
