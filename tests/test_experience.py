from uuid import uuid4
import json
from sqlalchemy import select
from fastapi.testclient import TestClient
from narrative.api import create_app
from narrative.db import Database, Universe, RecoveryPoint, Record, Source, Turn, RuntimeSecret
from narrative.recovery import capture, digest
from narrative.service import Engine


def setup(tmp_path):
    path=tmp_path/'experience.db'
    app=create_app(path)
    c=TestClient(app)
    c.headers['X-CSRF-Token']=c.get('/api/bootstrap').json()['csrf_token']
    d=c.post('/api/wizard',json=dict(universe_name='Ficticio',campaign_name='Prueba',protagonist_name='Ada',mode='discover')).json()
    return c,d,Database(path)


def test_resume_never_leaks_narrator_secrets_or_unvalidated_scenes(tmp_path):
    c,d,db=setup(tmp_path)
    with db.session(True) as s:
        s.add(Record(id=str(uuid4()),universe_id=d['universe_id'],kind='fact',name='Secreto',data=dict(statement='El rey es un dragón',certainty='confirmed',visibility='narrator',known_by=[])))
    route='/api/campaigns/'+d['campaign_id']
    c.post(route+'/turns',json=dict(role='narrator',content='Secreto sin validar: hay una emboscada.'))
    reply=c.get(route+'/resume')
    assert reply.status_code==200
    assert 'dragón' not in reply.text and 'emboscada' not in reply.text
    assert reply.json()['protagonist']=='Ada'
    assert reply.json()['pending_scenes']==1


def test_preferences_are_campaign_scoped_versioned_and_in_narrator_context(tmp_path):
    c,d,db=setup(tmp_path);route='/api/campaigns/'+d['campaign_id']
    prefs=c.get(route+'/preferences').json();prefs['pace']='brisk'
    assert c.post(route+'/preferences',json=dict(expected_version=1,preferences=prefs)).status_code==200
    assert c.get(route+'/context?audience=narrator').json()['narration_preferences']['pace']=='brisk'
    assert c.post(route+'/preferences',json=dict(expected_version=1,preferences=prefs)).status_code==409
    other=c.post('/api/wizard',json=dict(universe_id=d['universe_id'],campaign_name='Otra',protagonist_name='B',mode='discover')).json()
    assert c.get('/api/campaigns/'+other['campaign_id']+'/preferences').json()['pace']=='balanced'
    assert c.post(route+'/preferences',json=dict(expected_version=3,preferences={**prefs,'tone':'inventado'})).status_code==422


def test_scene_correction_preserves_original_and_excludes_superseded_prose(tmp_path):
    c,d,db=setup(tmp_path);route='/api/campaigns/'+d['campaign_id']
    original=c.post(route+'/turns',json=dict(role='narrator',content='La recepcionista se llama Eva.')).json()
    result=c.post(route+'/turns/'+original['id']+'/correction',json=dict(expected_version=1,content='La recepcionista se llama Iria.',reason='El nombre estaba mal.'))
    assert result.status_code==200,result.text
    turns=c.get(route+'/turns').json()['turns']
    assert turns[0]['content']==original['content'] and len(turns)==2
    assert c.get('/api/universes/'+d['universe_id']).json()['universe']['version']==1
    recent,_,_=Engine(db).narration_memory(d['campaign_id'],turns)
    assert [t['id'] for t in recent]==[result.json()['turn']['id']]
    assert c.post(route+'/turns/'+original['id']+'/correction',json=dict(expected_version=1,content='Otro',reason='Otro')).status_code==409


def test_recovery_is_atomic_private_integrity_checked_and_restores_as_new_universe(tmp_path):
    c,d,db=setup(tmp_path);route='/api/campaigns/'+d['campaign_id'];base='/api/universes/'+d['universe_id']
    turn=c.post(route+'/turns',json=dict(role='user',content='Observo la placa.')).json()
    with db.session(True) as s:
        s.add(RuntimeSecret(name='test',host_id='local',sealed='CREDENTIAL-MUST-NOT-LEAK',revision=1))
    points=c.get(base+'/recovery').json()['points']
    assert len(points)==1
    with db.session() as s:
        point=s.get(RecoveryPoint,points[0]['id'])
        assert 'CREDENTIAL-MUST-NOT-LEAK' not in json.dumps(point.payload)
        assert point.sha256==digest(point.payload)
    result=c.post(base+'/recovery/'+points[0]['id']+'/restore',json={'name':'Recuperado'})
    assert result.status_code==200,result.text
    new=result.json()['universe_id'];assert new!=d['universe_id']
    snap=c.get('/api/universes/'+new).json()
    campaign=next(r for r in snap['records'] if r['kind']=='campaign')
    assert c.get('/api/campaigns/'+campaign['id']+'/turns').json()['turns'][0]['content']=='Observo la placa.'
    assert c.get(route+'/turns').json()['turns'][0]['id']==turn['id']
    assert c.post('/api/universes/'+new+'/recovery/'+points[0]['id']+'/restore',json={'name':'No'}).status_code==404
    with db.session(True) as s:
        point=s.get(RecoveryPoint,points[0]['id']);point.sha256='tampered'
    assert c.post(base+'/recovery/'+points[0]['id']+'/restore',json={'name':'No'}).status_code==422


def test_recovery_retention_and_failed_writes(tmp_path):
    c,d,db=setup(tmp_path)
    with db.session(True) as s:
        point=s.scalar(select(RecoveryPoint))
        for i in range(10):
            s.add(RecoveryPoint(id=str(uuid4()),universe_id=d['universe_id'],bucket='2000-01-01T%02d'%i,created_at='old',version=1,sha256=point.sha256,payload=point.payload))
        capture(s,d['universe_id'])
    with db.session() as s:assert len(list(s.scalars(select(RecoveryPoint))))==7
    count=len(c.get('/api/universes/'+d['universe_id']+'/recovery').json()['points'])
    try:
        with db.session(True) as s:
            u=s.get(Universe,d['universe_id']);u.name='Should rollback';s.flush();raise ValueError('rollback')
    except ValueError:pass
    assert c.get('/api/universes/'+d['universe_id']).json()['universe']['name']=='Ficticio'
    assert len(c.get('/api/universes/'+d['universe_id']+'/recovery').json()['points'])==count

def test_prose_correction_does_not_reapply_existing_material_effects(tmp_path):
    c,d,db=setup(tmp_path);route='/api/campaigns/'+d['campaign_id']
    with db.session(True) as s:
        hero=s.get(Record,d['protagonist_id']);hero.data={**hero.data,'balance':100}
    authorization=c.post(route+'/turns',json=dict(role='user',content='Pago diez monedas por una capa.')).json()
    original=c.post(route+'/turns',json=dict(role='narrator',content='Pagas diez monedas por una capa.')).json()
    from narrative.schemas import Event
    with db.session(True) as s:
        from narrative.db import Proposal
        p=s.get(Proposal,original['proposal_id']);rec=dict(p.record)
        rec['data']=Event.model_validate({**rec['data'],'certainty':'confirmed','day':0,'actions':[dict(type='spend',character_id=d['protagonist_id'],amount=10,authorized_turn_id=authorization['id'],decision=authorization['content'])]}).model_dump()
        p.record=rec
    approved=c.post('/api/proposals/'+original['proposal_id']+'/accept',json=dict(expected_version=1,decision='Validado',acknowledge_warnings=True))
    assert approved.status_code==200,approved.text
    before=Engine(db).snapshot(d['universe_id'])
    corrected=c.post(route+'/turns/'+original['id']+'/correction',json=dict(expected_version=2,content='Pagas diez monedas por una capa azul.',reason='Precisar el color')).json()
    result=c.post('/api/proposals/'+corrected['proposal']['id']+'/accept',json=dict(expected_version=2,decision='Corrijo solo el texto',acknowledge_warnings=True))
    assert result.status_code==200,result.text
    after=Engine(db).snapshot(d['universe_id'])
    assert next(r for r in before['records'] if r['id']==d['protagonist_id'])['data']['balance']==90
    assert next(r for r in after['records'] if r['id']==d['protagonist_id'])['data']['balance']==90


def test_recovery_keeps_literal_source_text_even_if_it_is_an_identifier(tmp_path):
    c,d,db=setup(tmp_path)
    with db.session(True) as session:
        import hashlib
        text=d['protagonist_id']
        session.add(Source(id=str(uuid4()),universe_id=d['universe_id'],title='Fuente literal',content=text,sha256=hashlib.sha256(text.encode()).hexdigest()))
    base='/api/universes/'+d['universe_id']
    point=c.get(base+'/recovery').json()['points'][0]
    new=c.post(base+'/recovery/'+point['id']+'/restore',json={'name':'Copia literal'}).json()['universe_id']
    with db.session() as session:
        source=session.scalar(select(Source).where(Source.universe_id==new,Source.title=='Fuente literal'))
        assert source.content==d['protagonist_id']
        assert session.scalar(select(Record).where(Record.universe_id==new,Record.kind=='character')).id != d['protagonist_id']
