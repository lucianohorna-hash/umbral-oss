import json
import time
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from narrative.api import create_app
from narrative.chatgpt_plan import ChatGPTPlanNarrator, RESOURCE
from narrative.db import Database
from narrative.plan_handoff import challenge, seal, accept
from narrative.runtime_credentials import RuntimeCredentialStore
from narrative.self_hosted_plan import SelfHostedPlanNarrator
from narrative.security import CloudAccess


ORIGIN = 'https://umbral.example'
SECRET = b'fictitious-handoff-secret-' + b'x' * 40


def registration():
    return dict(host_id='urn:uuid:' + str(uuid4()), client_id='oaiapp_fictitious', subject='fictitious-owner',
        access_token='ACCESS-TOKEN-FICTITIOUS-FOR-TRANSFER-ONLY',
        refresh_token='REFRESH-TOKEN-FICTITIOUS-FOR-TRANSFER-ONLY', id_token='IDENTITY-FICTITIOUS-FOR-TRANSFER-ONLY',
        scopes=['resource.invoke','chatgpt.tokens.use.direct'], expires=time.time()+3600, model='small-test')


def destination():
    return {'host_id': 'urn:uuid:' + str(uuid4())}


def test_ciphertext_preserves_registration_but_never_replaces_server_host():
    source = registration()
    pending, descriptor = challenge(destination(), ORIGIN)
    packet = seal(source, descriptor, ORIGIN)
    serialized = json.dumps([descriptor, packet])
    assert all(source[field] not in serialized for field in ('access_token','refresh_token','id_token'))
    assert 'PRIVATE KEY' not in serialized
    received, replay = accept(pending, packet, ORIGIN)
    assert not replay and received['host_id'] == pending['host_id'] != source['host_id']
    assert received['refresh_token'] == source['refresh_token']
    assert received['client_id'] == source['client_id']
    assert received['server_verified'] is False
    assert 'handoff_private_key' not in received
    rotated = dict(received, refresh_token='ROTATED-FICTITIOUS-REFRESH')
    recovered, replay = accept(rotated, packet, ORIGIN, now=packet['expires']+1)
    assert replay and recovered == rotated


@pytest.mark.parametrize('change', ['origin','host','nonce','ciphertext','expiry','key','extra'])
def test_tampered_transfer_never_changes_destination(change):
    pending, descriptor = challenge(destination(), ORIGIN)
    packet = seal(registration(), descriptor, ORIGIN)
    altered = dict(packet)
    if change == 'origin': altered['target_origin'] = 'https://other.example'
    if change == 'host': altered['host_id'] = 'urn:uuid:' + str(uuid4())
    if change == 'nonce': altered['nonce'] = 'AA=='
    if change == 'ciphertext': altered['ciphertext'] = altered['ciphertext'][:-5] + 'AAAAA'
    if change == 'expiry': altered['expires'] += 1
    if change == 'key': altered['wrapped_key'] = 'AA=='
    if change == 'extra': altered['unexpected'] = True
    before = json.dumps(pending, sort_keys=True)
    with pytest.raises(ValueError): accept(pending, altered, ORIGIN)
    assert json.dumps(pending, sort_keys=True) == before


def test_expired_wrong_account_and_old_descriptor_are_rejected():
    pending, descriptor = challenge(destination(), ORIGIN)
    packet = seal(registration(), descriptor, ORIGIN)
    with pytest.raises(ValueError): accept(pending, packet, ORIGIN, now=packet['expires']+1)
    with pytest.raises(ValueError): accept(dict(pending, subject='another-owner'), packet, ORIGIN)
    again, same_descriptor = challenge(pending, ORIGIN)
    assert again == pending and same_descriptor == descriptor
    replacement, _ = challenge(pending, ORIGIN, now=descriptor['expires']+1)
    with pytest.raises(ValueError): accept(replacement, packet, ORIGIN)
    with pytest.raises(ValueError): seal(registration(), descriptor, 'https://other.example')
    bad = registration(); bad['scopes'] = ['openid','profile']
    with pytest.raises(ValueError): seal(bad, descriptor, ORIGIN)


def test_local_transfer_removes_refresh_ownership_and_survives_lost_response(tmp_path):
    local = ChatGPTPlanNarrator(tmp_path / 'local-profile')
    local.profile.update(registration())
    local.profile['host_id'] = local.host
    local._save()
    _, descriptor = challenge(destination(), ORIGIN)
    packet = local.prepare_handoff(descriptor, ORIGIN)
    assert not local.status()['connected']
    assert not any(key in local.profile for key in ('access_token','refresh_token','id_token'))
    restored = ChatGPTPlanNarrator(tmp_path / 'local-profile')
    assert restored.prepare_handoff(descriptor, ORIGIN) == packet
    _, other = challenge(destination(), ORIGIN)
    with pytest.raises(ValueError): restored.prepare_handoff(other, ORIGIN)


def test_owner_can_import_verify_and_replay_without_exposing_tokens(tmp_path, monkeypatch):
    access = CloudAccess.build(ORIGIN, 'fictitious-password-1234', SECRET.decode())
    app = create_app(tmp_path / 'canon.db', access=access, self_hosted_plan=True)
    client = TestClient(app, base_url=ORIGIN)
    client.headers['X-CSRF-Token'] = client.get('/api/auth').json()['csrf_token']
    assert client.post('/api/login', json={'password':'fictitious-password-1234'}).status_code == 200
    client.headers['X-CSRF-Token'] = client.get('/api/bootstrap').json()['csrf_token']
    descriptor = client.post('/api/chatgpt-plan/handoff', json={}).json()
    packet = seal(registration(), descriptor, ORIGIN)
    imported = client.post('/api/chatgpt-plan/import', json={'packet': packet})
    assert imported.status_code == 200 and imported.json()['received']
    assert client.post('/api/chatgpt-plan/import', json={'packet':packet}).json()['replay']
    status = client.get('/api/chatgpt-plan').json()
    assert not status['connected'] and status['needs_verification']
    narrator = SelfHostedPlanNarrator(RuntimeCredentialStore(app.state.db, access.secret), ORIGIN)
    with pytest.raises(ValueError, match='Verifica primero'): narrator.narrate({}, [])
    original = httpx.Client
    calls = []
    def handler(request):
        calls.append(str(request.url))
        assert str(request.url) == RESOURCE + '/models'
        return httpx.Response(200, json={'models':[{'slug':'small-test','display_name':'Ficticio','visibility':'list'}]})
    monkeypatch.setattr('narrative.chatgpt_plan.httpx.Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    assert client.get('/api/chatgpt-plan/models').status_code == 200
    status = client.get('/api/chatgpt-plan').json()
    assert status['connected'] and not status['needs_verification']
    assert all(secret not in json.dumps(status) for secret in ('ACCESS-TOKEN-FICTITIOUS','REFRESH-TOKEN-FICTITIOUS','IDENTITY-FICTITIOUS'))
    assert calls == [RESOURCE+'/models']
    client.headers.pop('X-CSRF-Token')
    assert client.post('/api/chatgpt-plan/import', json={'packet':packet}).status_code == 403
    client.cookies.clear()
    assert client.get('/api/chatgpt-plan').status_code == 401
