import json
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from narrative.api import create_app
from narrative.chatgpt_plan import AUTH, RESOURCE
from narrative.db import Database
from narrative.runtime_credentials import RuntimeCredentialStore
from narrative.security import CloudAccess
from narrative.self_hosted_plan import SelfHostedPlanNarrator


SECRET = b'fictional-server-secret-' + b'x' * 40
ORIGIN = 'https://umbral.example'


def provider(tmp_path, authorized=True, expired=False):
    db = Database(tmp_path / 'canon.db')
    store = RuntimeCredentialStore(db, SECRET)
    if authorized:
        with store.lease() as lease:
            lease.save(dict(lease.read(), client_id='oaiapp_fictitious', subject='fictitious-owner',
                email='owner@example.test', access_token='fictitious-access', refresh_token='fictitious-refresh',
                scopes=['resource.invoke', 'chatgpt.tokens.use.direct'],
                expires=0 if expired else time.time() + 3600, model='small-test', server_verified=True))
    return SelfHostedPlanNarrator(store, ORIGIN)


def wire(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr('narrative.chatgpt_plan.httpx.Client',
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


def cloud_client(path, narrator):
    access = CloudAccess.build(ORIGIN, 'fictitious-password-1234', SECRET.decode())
    client = TestClient(create_app(path, provider=narrator, access=access), base_url=ORIGIN)
    assert client.get('/api/bootstrap').status_code == 401
    client.headers['X-CSRF-Token'] = client.get('/api/auth').json()['csrf_token']
    assert client.post('/api/login', json={'password': 'fictitious-password-1234'}).status_code == 200
    client.headers['X-CSRF-Token'] = client.get('/api/bootstrap').json()['csrf_token']
    return client


def stream(text='Propuesta ficticia: el puerto sigue tranquilo.'):
    return httpx.Response(200, headers={'Content-Type': 'text/event-stream'}, content=(
        'data: ' + json.dumps({'type': 'response.output_text.delta', 'delta': text}) + '\n\n' +
        'data: ' + json.dumps({'type': 'response.completed'}) + '\n\n').encode())


def test_cloud_plan_generates_persists_and_retries_after_restart_without_pc(tmp_path, monkeypatch):
    narrator = provider(tmp_path)
    client = cloud_client(tmp_path / 'canon.db', narrator)
    seen = []
    def handler(request):
        seen.append(str(request.url))
        assert str(request.url) == RESOURCE + '/responses'
        data = json.loads(request.content)
        assert data['store'] is False and data['stream'] is True
        assert data['model'] == 'small-test'
        assert 'archival_recall' in data['input'][0]['content']
        return stream()
    wire(monkeypatch, handler)
    setup = client.post('/api/wizard', json=dict(universe_name='Ficticio', campaign_name='Servidor', protagonist_name='Ada', mode='discover')).json()
    route = '/api/campaigns/' + setup['campaign_id']
    turn = client.post(route + '/turns', json={'role': 'user', 'content': 'Observo el puerto.'}).json()
    request = dict(expected_version=1, request_id=str(uuid4()), expected_last_turn_id=turn['id'])
    generated = client.post(route + '/generate', json=request)
    assert generated.status_code == 200, generated.text
    assert generated.json()['role'] == 'narrator' and generated.json()['proposal_id']
    assert client.get('/api/universes/' + setup['universe_id']).json()['universe']['version'] == 1
    client.close()
    reopened = SelfHostedPlanNarrator(RuntimeCredentialStore(Database(tmp_path / 'canon.db'), SECRET), ORIGIN)
    client = cloud_client(tmp_path / 'canon.db', reopened)
    assert client.post(route + '/generate', json=request).json()['id'] == generated.json()['id']
    assert len(client.get(route + '/turns').json()['turns']) == 2
    assert seen == [RESOURCE + '/responses']
    status = client.get('/api/chatgpt-plan').json()
    assert status['connected'] and status['mode'] == 'personal-oss-server'
    assert 'fictitious-access' not in json.dumps(status) and 'fictitious-refresh' not in json.dumps(status)


def test_rotated_refresh_survives_later_inference_failure(tmp_path, monkeypatch):
    narrator = provider(tmp_path, expired=True)
    seen = []
    def handler(request):
        seen.append(str(request.url))
        if str(request.url) == AUTH + '/api/accounts/oauth/token':
            return httpx.Response(200, json=dict(access_token='new-access', refresh_token='new-refresh',
                expires_in=3600, scope='resource.invoke chatgpt.tokens.use.direct'))
        assert request.headers['Authorization'] == 'Bearer new-access'
        return httpx.Response(429)
    wire(monkeypatch, handler)
    with pytest.raises(ValueError, match='No se usará facturación'):
        narrator.narrate({}, [])
    restarted = SelfHostedPlanNarrator(RuntimeCredentialStore(Database(tmp_path / 'canon.db'), SECRET), ORIGIN)
    with restarted.store.lease() as lease:
        assert lease.read()['refresh_token'] == 'new-refresh'
    assert len(seen) == 2


def test_two_server_instances_use_latest_rotated_session(tmp_path, monkeypatch):
    first = provider(tmp_path, expired=True)
    second = SelfHostedPlanNarrator(RuntimeCredentialStore(Database(tmp_path / 'canon.db'), SECRET), ORIGIN)
    refreshes = []
    def handler(request):
        if str(request.url) == AUTH + '/api/accounts/oauth/token':
            refreshes.append(request.content)
            return httpx.Response(200, json=dict(access_token='rotated-access', refresh_token='rotated-refresh',
                expires_in=3600, scope='resource.invoke chatgpt.tokens.use.direct'))
        assert str(request.url) == RESOURCE + '/models'
        assert request.headers['Authorization'] == 'Bearer rotated-access'
        return httpx.Response(200, json={'models': [{'slug': 'small-test', 'display_name': 'Modelo ficticio', 'visibility': 'list'}]})
    wire(monkeypatch, handler)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda p: p.models(), [first, second]))
    assert all(result[0]['id'] == 'small-test' for result in results)
    assert len(refreshes) == 1


def test_server_rejects_loopback_authorization_and_unauthenticated_state(tmp_path):
    narrator = provider(tmp_path, authorized=False)
    client = cloud_client(tmp_path / 'canon.db', narrator)
    status = client.get('/api/chatgpt-plan').json()
    assert status['available'] and not status['connected']
    assert client.post('/api/chatgpt-plan/connect', json={}).status_code == 409
    assert client.get('/auth/callback?code=fictitious&state=fictitious').status_code == 409
    client.headers.pop('X-CSRF-Token')
    assert client.post('/api/chatgpt-plan/disconnect', json={}).status_code == 403
    client.cookies.clear()
    assert client.get('/api/chatgpt-plan').status_code == 401


def test_server_adapter_cannot_run_without_owner_https_boundary(tmp_path):
    narrator = provider(tmp_path)
    with pytest.raises(ValueError, match='autenticación'):
        create_app(tmp_path / 'unprotected.db', provider=narrator)
    assert not (tmp_path / 'unprotected.db').exists()
    with pytest.raises(ValueError, match='HTTPS'):
        SelfHostedPlanNarrator(narrator.store, 'http://umbral.example')
    access = CloudAccess.build('https://other.example', 'fictitious-password-1234', SECRET.decode())
    with pytest.raises(ValueError, match='origen'):
        create_app(tmp_path / 'wrong-origin.db', provider=narrator, access=access)


def test_explicit_cloud_mode_rejects_fallback_provider(monkeypatch):
    from narrative import cloud
    monkeypatch.setenv('DATABASE_URL', 'postgresql://u:p@localhost/db?sslmode=require')
    monkeypatch.setenv('NARRATIVE_PUBLIC_URL', ORIGIN)
    monkeypatch.setenv('NARRATIVE_ACCESS_PASSWORD', 'fictitious-password-1234')
    monkeypatch.setenv('NARRATIVE_SESSION_SECRET', SECRET.decode())
    monkeypatch.setenv('NARRATIVE_PERSONAL_OSS_PLAN', 'true')
    for name in ('NARRATIVE_AI_BASE_URL', 'NARRATIVE_AI_MODEL', 'NARRATIVE_AI_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cloud, 'create_app', lambda database, **kwargs: kwargs)
    assert cloud.from_environment()['self_hosted_plan'] is True
    monkeypatch.setenv('NARRATIVE_AI_BASE_URL', 'https://paid-provider.invalid/v1')
    with pytest.raises(ValueError, match='sin proveedor alternativo'):
        cloud.from_environment()
