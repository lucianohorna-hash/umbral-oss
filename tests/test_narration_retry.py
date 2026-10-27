from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from fastapi.testclient import TestClient
from narrative.api import create_app
from narrative.schemas import TurnInput


class Provider:
    name='controlled-test'
    def __init__(self):self.calls=0;self.during=None
    def narrate(self, context, turns):
        self.calls+=1
        if self.during:self.during()
        return 'Propuesta ficticia: el puerto permanece tranquilo.'


def setup(tmp_path, provider):
    c=TestClient(create_app(tmp_path/'canon.db',provider=provider))
    c.headers['X-CSRF-Token']=c.get('/api/bootstrap').json()['csrf_token']
    data=c.post('/api/wizard',json=dict(universe_name='Ficticio',campaign_name='Reintentos',protagonist_name='Ada',mode='discover')).json()
    route='/api/campaigns/'+data['campaign_id']
    turn=c.post(route+'/turns',json=dict(role='user',content='Observo.')).json()
    request=dict(expected_version=1,request_id=str(uuid4()),expected_last_turn_id=turn['id'])
    return c,data,route,request


def test_committed_retry_survives_restart_without_model_call(tmp_path):
    provider=Provider();c,data,route,request=setup(tmp_path,provider)
    first=c.post(route+'/generate',json=request)
    assert first.status_code==200,first.text
    assert c.post(route+'/generate',json=request).json()['id']==first.json()['id']
    assert provider.calls==1
    c.close()
    provider2=Provider();c=TestClient(create_app(tmp_path/'canon.db',provider=provider2))
    c.headers['X-CSRF-Token']=c.get('/api/bootstrap').json()['csrf_token']
    assert c.post(route+'/generate',json=request).json()['id']==first.json()['id']
    assert provider2.calls==0
    assert len(c.get(route+'/turns').json()['turns'])==2
    assert c.get('/api/universes/'+data['universe_id']).json()['universe']['version']==1
    request['expected_version']=2
    assert c.post(route+'/generate',json=request).status_code==409
    assert provider2.calls==0


def test_same_request_in_parallel_calls_provider_once(tmp_path):
    provider=Provider();c,_,route,request=setup(tmp_path,provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:c.post(route+'/generate',json=request),range(2)))
    assert all(r.status_code==200 for r in responses)
    assert responses[0].json()['id']==responses[1].json()['id']
    assert provider.calls==1


def test_new_turn_before_request_rejected_without_consuming_model(tmp_path):
    provider=Provider();c,_,route,request=setup(tmp_path,provider)
    c.post(route+'/turns',json=dict(role='user',content='Espero.'))
    assert c.post(route+'/generate',json=request).status_code==409
    assert provider.calls==0


def test_turn_added_while_narrating_cannot_save_stale_scene(tmp_path):
    provider=Provider();c,data,route,request=setup(tmp_path,provider)
    provider.during=lambda:c.app.state.engine.add_turn(data['campaign_id'],TurnInput(role='user',content='Cambio de decisión.'))
    result=c.post(route+'/generate',json=request)
    assert result.status_code==409,result.text
    turns=c.get(route+'/turns').json()['turns']
    assert [t['role'] for t in turns]==['user','user']
    assert not c.get('/api/universes/'+data['universe_id']).json()['proposals']


def test_existing_player_request_id_cannot_be_reused_for_generation(tmp_path):
    provider=Provider();c,_,route,request=setup(tmp_path,provider)
    c.post(route+'/turns',json=dict(role='user',content='Otra decisión.',request_id=request['request_id']))
    assert c.post(route+'/generate',json=request).status_code==409
    assert provider.calls==0


def test_explicit_empty_history_anchor_rejects_new_message(tmp_path):
    provider=Provider();c,_,route,request=setup(tmp_path,provider)
    request['expected_last_turn_id']=None
    assert c.post(route+'/generate',json=request).status_code==409
    assert provider.calls==0
