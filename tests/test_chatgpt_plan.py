import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from narrative.api import create_app
from narrative.chatgpt_plan import ChatGPTPlanNarrator, RESOURCE, AUTH
from narrative.security import CloudAccess


def runtime(tmp_path):
    return ChatGPTPlanNarrator(tmp_path/'private')


def wire(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr('narrative.chatgpt_plan.httpx.Client',
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


def authorized(p):
    p.profile.update(client_id='oaiapp_test',subject='owner',access_token='private-token',
        refresh_token='private-refresh',scopes=['resource.invoke','chatgpt.tokens.use.direct'],
        expires=time.time()+3600,model='available-model')
    p._save()


def test_loopback_pkce_host_and_single_use_state(tmp_path):
    p=runtime(tmp_path)
    url=p.begin('http://127.0.0.1:8123')
    query=parse_qs(urlsplit(url).query)
    assert query['client_id']==['dynamic_agent_client']
    assert query['agent_name_hint']==['Umbral']
    assert query['redirect_uri']==['http://127.0.0.1:8123/auth/callback']
    assert query['code_challenge_method']==['S256']
    assert p.pending['verifier'] not in url
    assert runtime(tmp_path).host==p.host
    for origin in ['https://umbral.example','http://localhost:8123','http://127.0.0.1.evil','http://127.0.0.1/path']:
        with pytest.raises(ValueError): p.begin(origin)
    with pytest.raises(ValueError): p.finish({'state':'wrong'},'http://127.0.0.1:8123/auth/callback')
    state=p.pending['state']
    with pytest.raises(ValueError): p.finish({'state':state,'error':'access_denied'},'http://127.0.0.1:8123/auth/callback')
    assert p.pending is None
    assert not p.status()['connected']


def test_authorization_scopes_restart_and_no_browser_secrets(tmp_path,monkeypatch):
    p=runtime(tmp_path)
    p.begin('http://127.0.0.1:8123')
    state=p.pending['state']
    def handler(req):
        assert req.url==AUTH+'/api/accounts/oauth/token'
        fields=parse_qs(req.content.decode())
        assert fields['client_id']==['oaiapp_issued']
        assert fields['resource']==[RESOURCE]
        return httpx.Response(200,json=dict(id_token='signed',access_token='secret',refresh_token='renew',
            token_type='Bearer',scope='resource.invoke chatgpt.tokens.use.direct',expires_in=3600))
    wire(monkeypatch,handler)
    monkeypatch.setattr(p,'_validate_identity',lambda *a:dict(sub='owner',email='owner@example.test'))
    p.finish(dict(state=state,code='one-use',client_id='oaiapp_issued'),'http://127.0.0.1:8123/auth/callback')
    q=runtime(tmp_path)
    assert q.status()['connected']
    assert q.profile['client_id']=='oaiapp_issued'
    assert 'access_token' not in q.status() and 'renew' not in json.dumps(q.status())
    p.begin('http://127.0.0.1:8123')
    with pytest.raises(ValueError):
        p.finish(dict(state=p.pending['state'],code='new',client_id='other'),'http://127.0.0.1:8123/auth/callback')
    assert p.profile['client_id']=='oaiapp_issued'


def test_identity_alone_does_not_enable_inference(tmp_path,monkeypatch):
    p=runtime(tmp_path);p.begin('http://127.0.0.1:8123')
    wire(monkeypatch,lambda req:httpx.Response(200,json=dict(id_token='signed',access_token='secret',token_type='Bearer',scope='openid email')))
    monkeypatch.setattr(p,'_validate_identity',lambda *a:dict(sub='owner'))
    with pytest.raises(ValueError,match='No se autorizó'):
        p.finish(dict(state=p.pending['state'],code='code',client_id='issued'),'http://127.0.0.1:8123/auth/callback')
    assert not runtime(tmp_path).status()['connected']


def test_stream_contract_and_only_completed_output(tmp_path,monkeypatch):
    p=runtime(tmp_path);authorized(p)
    events=[dict(type='response.output_text.delta',delta='Una escena propuesta.'),dict(type='response.completed')]
    def handler(req):
        assert str(req.url)==RESOURCE+'/responses'
        body=json.loads(req.content)
        assert body['store'] is False and body['stream'] is True
        assert body['input'][0]['role']=='developer'
        assert all(m['content']!='unconfirmed' for m in body['input'])
        assert not {'temperature','max_tokens','previous_response_id','conversation'} & body.keys()
        return httpx.Response(200,text=''.join('data: '+json.dumps(e)+'\n\n' for e in events))
    wire(monkeypatch,handler)
    assert p.narrate({'version':1},[dict(role='player_pending',content='unconfirmed'),dict(role='user',content='Observo.')])=='Una escena propuesta.'
    events.pop()
    with pytest.raises(ValueError,match='incompleta'):p.narrate({},[])
    events.append(dict(type='response.failed',response={'error':{'code':'subscription_sharing_usage_limit_exceeded'}}))
    with pytest.raises(ValueError,match='API de pago'):p.narrate({},[])


def test_rotation_models_and_no_paid_fallback(tmp_path,monkeypatch):
    p=runtime(tmp_path);authorized(p);p.profile['expires']=0
    seen=[]
    def handler(req):
        seen.append(str(req.url))
        if req.method=='POST':
            return httpx.Response(200,json=dict(access_token='new-token',refresh_token='rotated',expires_in=3600))
        assert req.headers['authorization']=='Bearer new-token'
        return httpx.Response(200,json={'models':[dict(slug='available-model',display_name='Available',visibility='list'),dict(slug='hidden',visibility='hide')]})
    wire(monkeypatch,handler)
    assert p.models()==[dict(id='available-model',name='Available')]
    assert runtime(tmp_path).profile['refresh_token']=='rotated'
    with pytest.raises(ValueError):p.select_model('hidden')
    assert seen.count(AUTH+'/api/accounts/oauth/token')==1


def test_routes_csrf_cloud_block_and_canon_unchanged(tmp_path):
    p=runtime(tmp_path)
    c=TestClient(create_app(tmp_path/'canon.db',provider=p))
    b=c.get('/api/bootstrap').json()
    assert c.post('/api/chatgpt-plan/connect').status_code==403
    c.headers['X-CSRF-Token']=b['csrf_token']
    assert c.post('/api/chatgpt-plan/connect').status_code==422  # testserver is not loopback callback
    assert c.get('/api/chatgpt-plan').json()['connected'] is False
    assert c.get('/auth/callback?state=wrong&code=secret').status_code==400
    assert c.get('/api/bootstrap').json()['universes']==[]
    cloud=CloudAccess.build('https://example.test','a-long-password-1234','s'*40)
    with pytest.raises(ValueError,match='servidor remoto'):
        create_app(tmp_path/'blocked.db',provider=p,access=cloud)


def test_signed_identity_requires_signature_audience_nonce_and_expiry(tmp_path):
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    p=runtime(tmp_path)
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk['kid']='test-key'
    claims=dict(iss=AUTH,aud='issued',sub='owner',nonce='nonce',exp=int(time.time())+60)
    def handler(req):
        if str(req.url).endswith('openid-configuration'):
            return httpx.Response(200,json={'jwks_uri':AUTH+'/keys'})
        return httpx.Response(200,json={'keys':[jwk]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        token=jwt.encode(claims,key,algorithm='RS256',headers={'kid':'test-key'})
        assert p._validate_identity(client,token,'issued','nonce')['sub']=='owner'
        for audience,nonce in [('other','nonce'),('issued','wrong')]:
            with pytest.raises(Exception): p._validate_identity(client,token,audience,nonce)
        expired=jwt.encode(dict(claims,exp=int(time.time())-10),key,algorithm='RS256',headers={'kid':'test-key'})
        with pytest.raises(jwt.ExpiredSignatureError):p._validate_identity(client,expired,'issued','nonce')
        other=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        forged=jwt.encode(claims,other,algorithm='RS256',headers={'kid':'test-key'})
        with pytest.raises(jwt.InvalidSignatureError):p._validate_identity(client,forged,'issued','nonce')


def test_disconnect_network_failure_clears_local_access(tmp_path,monkeypatch):
    p=runtime(tmp_path);authorized(p)
    def handler(req):raise httpx.ConnectError('offline')
    wire(monkeypatch,handler)
    result=p.disconnect()
    assert result['remote_revoked'] is False
    q=runtime(tmp_path)
    assert not q.status()['connected']
    assert q.profile['client_id']=='oaiapp_test'
    assert not {'access_token','refresh_token','id_token'} & q.profile.keys()


def test_direct_narrator_route_preserves_player_and_draft_without_canon_write(tmp_path,monkeypatch):
    p=runtime(tmp_path);authorized(p)
    c=TestClient(create_app(tmp_path/'game.db',provider=p))
    c.headers['X-CSRF-Token']=c.get('/api/bootstrap').json()['csrf_token']
    setup=c.post('/api/wizard',json=dict(universe_name='Fictional',campaign_name='Local',protagonist_name='Ada',mode='discover')).json()
    route='/api/campaigns/'+setup['campaign_id']
    assert c.post(route+'/turns',json=dict(role='user',content='Observo la puerta.')).status_code==200
    def handler(req):
        body=json.loads(req.content)
        assert body['input'][-1]['content']=='Observo la puerta.'
        events=[dict(type='response.output_text.delta',delta='Propuesta: la puerta permanece cerrada. ¿Qué decides?'),dict(type='response.completed')]
        return httpx.Response(200,text=''.join('data: '+json.dumps(e)+'\n\n' for e in events))
    wire(monkeypatch,handler)
    response=c.post(route+'/generate',json={'expected_version':1})
    assert response.status_code==200,response.text
    assert response.json()['proposal_id']
    snapshot=c.get('/api/universes/'+setup['universe_id']).json()
    assert snapshot['universe']['version']==1
    assert len(snapshot['proposals'])==1
    assert [t['role'] for t in c.get(route+'/turns').json()['turns']]==['user','narrator']
    c.close()
    c=TestClient(create_app(tmp_path/'game.db',provider=runtime(tmp_path)))
    assert len(c.get(route+'/turns').json()['turns'])==2
