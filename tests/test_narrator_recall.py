import hashlib
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from narrative.api import create_app
from narrative.recall import prepare_narration_memory
from narrative.schemas import TurnInput
from narrative.service import DomainError


def turn(content,role='user',**more):
    return dict(id=str(uuid4()),campaign_id='campaign',role=role,content=content,protagonist_id='hero',**more)


def test_old_plate_recovers_original_quote_without_becoming_canon():
    old=turn('En el suelo hay una placa de metal con un símbolo: círculo abierto y tres marcas.','narrator')
    turns=[old]+[turn(f'Conversación posterior {i} sobre provisiones.') for i in range(70)]+[turn('Examino otra placa con un símbolo parecido.')]
    recent,recall=prepare_narration_memory(turns,{old['id']:dict(status='pending',source_id='original',certainty='unknown')})
    assert len(recent)==30 and old not in recent
    saved=recall['items'][0]
    assert saved['turn_id']==old['id'] and saved['source_id']=='original'
    assert saved['excerpt'] in old['content']
    assert 'círculo abierto y tres marcas' in saved['excerpt']
    assert saved['canonical'] is False and saved['certainty']=='unknown'
    assert saved['review_status']=='pending'
    assert saved['text_sha256']==hashlib.sha256(old['content'].encode()).hexdigest()
    assert 'conexiones' in recall['instructions']


def test_pending_player_and_rejected_scenes_never_enter_model_history():
    rejected=turn('La placa es de oro.','narrator')
    pending=turn('Mi decisión inventada sobre la placa.','player_pending')
    denied=turn('Otra decisión falsa sobre la placa.','player_rejected')
    valid=turn('Examino la placa.')
    recent,recall=prepare_narration_memory([rejected,pending,denied,valid],{rejected['id']:dict(status='rejected')})
    assert recent==[valid] and not recall['items']
    recent,recall=prepare_narration_memory([rejected,pending,denied]+[turn('Rutina.') for _ in range(40)]+[valid],{rejected['id']:dict(status='rejected')})
    assert not recall['items']


def test_literal_matching_does_not_infer_synonyms_or_symbol_connections():
    old=turn('Una placa con un símbolo azul.','narrator')
    turns=[old]+[turn('Camino.') for _ in range(35)]+[turn('Examino un emblema celeste.')]
    _,recall=prepare_narration_memory(turns,{})
    assert not recall['items'] and recall['candidates']==0
    assert 'no demuestra' in recall['instructions']


def test_recall_is_bounded_and_fragments_preserve_unicode_offsets():
    turns=[turn('Texto previo. '*200+'SÍMBOLO Ω único. '+'Texto posterior. '*200,'narrator') for _ in range(20)]
    turns += [turn('Camino.') for _ in range(35)]+[turn('Recuerdo el símbolo.')]
    _,recall=prepare_narration_memory(turns,{},max_items=3,excerpt_limit=300)
    assert len(recall['items'])==3 and recall['omitted']==17
    by_id={t['id']:t for t in turns}
    for item in recall['items']:
        assert len(item['excerpt'])<=300 and item['excerpt_is_partial']
        assert item['excerpt']==by_id[item['turn_id']]['content'][item['start']:item['end']]
        assert 'SÍMBOLO Ω' in item['excerpt']


class Narrator:
    name='recall-test'
    def __init__(self):self.during=None;self.seen=None
    def narrate(self,context,turns):
        self.seen=(context,turns)
        if self.during:self.during()
        return 'Escena propuesta con recuerdos consultados.'


def setup(tmp_path):
    provider=Narrator();c=TestClient(create_app(tmp_path/'recall.db',provider=provider))
    c.headers['X-CSRF-Token']=c.get('/api/bootstrap').json()['csrf_token']
    data=c.post('/api/wizard',json=dict(universe_name='Ficticio',campaign_name='Memoria',protagonist_name='Ada',mode='discover')).json()
    e=c.app.state.engine
    old=e.add_turn(data['campaign_id'],TurnInput(role='narrator',content='Una placa de metal tiene un símbolo azul.'))
    for _ in range(35):e.add_turn(data['campaign_id'],TurnInput(role='user',content='Camino por la senda.'))
    last=e.add_turn(data['campaign_id'],TurnInput(role='user',content='Reviso aquella placa y su símbolo.'))
    return c,e,data,provider,old,last


def test_generator_receives_archival_quote_after_restart_and_persists_receipt(tmp_path):
    c,e,data,provider,old,last=setup(tmp_path)
    route='/api/campaigns/'+data['campaign_id']+'/generate'
    request=dict(expected_version=1,request_id=str(uuid4()),expected_last_turn_id=last['id'])
    c.close()
    e.db.engine.dispose()
    provider=Narrator()
    c=TestClient(create_app(tmp_path/'recall.db',provider=provider))
    c.headers['X-CSRF-Token']=c.get('/api/bootstrap').json()['csrf_token']
    result=c.post(route,json=request)
    assert result.status_code==200,result.text
    context,turns=provider.seen
    assert old['id'] in [item['turn_id'] for item in context['archival_recall']['items']]
    assert len(turns)==30
    assert old['id'] in result.json()['provenance']['generation']['recalled_turn_ids']
    c.close()
    provider2=Narrator();c=TestClient(create_app(tmp_path/'recall.db',provider=provider2))
    c.headers['X-CSRF-Token']=c.get('/api/bootstrap').json()['csrf_token']
    assert c.post(route,json=request).json()['id']==result.json()['id']
    assert provider2.seen is None
    assert c.get('/api/universes/'+data['universe_id']).json()['universe']['version']==1


def test_rejecting_recalled_scene_during_generation_blocks_stale_response(tmp_path):
    c,e,data,provider,old,last=setup(tmp_path)
    provider.during=lambda:e.reject(old['proposal_id'],'Escena descartada por el usuario.')
    response=c.post('/api/campaigns/'+data['campaign_id']+'/generate',json=dict(expected_version=1,request_id=str(uuid4()),expected_last_turn_id=last['id']))
    assert response.status_code==409,response.text
    assert 'revisión' in response.json()['detail']
    assert len(e.turns(data['campaign_id'])['turns'])==37
    _,recall,_=e.narration_memory(data['campaign_id'],e.turns(data['campaign_id'])['turns'])
    assert not recall['items']


def test_recall_does_not_accept_another_campaign_transcript(tmp_path):
    c,e,data,provider,old,last=setup(tmp_path)
    bad=dict(last,campaign_id='another-campaign')
    with pytest.raises(DomainError):e.narration_memory(data['campaign_id'],[bad])
