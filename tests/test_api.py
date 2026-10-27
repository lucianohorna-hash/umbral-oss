from pathlib import Path
import sqlite3
from fastapi.testclient import TestClient
from narrative.api import create_app
from narrative.db import Database
from narrative.service import Engine
from narrative.schemas import Wizard


def client(tmp_path, provider=None):
    instance = TestClient(create_app(tmp_path / 'api.sqlite3', provider=provider))
    bootstrap = instance.get('/api/bootstrap').json()
    instance.headers['X-CSRF-Token'] = bootstrap['csrf_token']
    return instance


def wizard(c):
    response = c.post('/api/wizard', json=dict(universe_name='Nuevo universo', campaign_name='Desde cero', protagonist_name='Ada', mode='discover'))
    assert response.status_code == 200, response.text
    return response.json()


def test_new_game_save_and_resume_after_restart(tmp_path):
    c = client(tmp_path)
    setup = wizard(c)
    route = f"/api/campaigns/{setup['campaign_id']}/turns"
    assert c.post(route, json={'role': 'user', 'content': 'Decido observar.'}).status_code == 200
    response = c.post(route, json={'role': 'narrator', 'content': 'Propuesta: junto al camino hay una torre.'})
    assert response.status_code == 200, response.text
    assert response.json()['proposal_id']
    c.close()
    reopened = TestClient(create_app(tmp_path / 'api.sqlite3'))
    turns = reopened.get(route).json()['turns']
    assert [t['role'] for t in turns] == ['user', 'narrator']
    snapshot = reopened.get(f"/api/universes/{setup['universe_id']}").json()
    assert len(snapshot['proposals']) == 1
    assert not [r for r in snapshot['records'] if r['kind'] == 'event']


def test_csrf_and_origin_reject_untrusted_site(tmp_path):
    c = client(tmp_path)
    c.headers.pop('X-CSRF-Token')
    assert c.post('/api/wizard', json={}).status_code == 403
    c.headers['X-CSRF-Token'] = c.get('/api/bootstrap').json()['csrf_token']
    assert c.post('/api/wizard', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert c.get('/api/bootstrap', headers={'Host': 'evil.example'}).status_code == 403


def test_sqlite_backup_contains_full_history_and_passes_integrity(tmp_path):
    c = client(tmp_path)
    setup = wizard(c)
    response = c.get('/api/backup')
    assert response.status_code == 200
    backup = tmp_path / 'download.sqlite3'
    backup.write_bytes(response.content)
    with sqlite3.connect(backup) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('SELECT count(*) FROM revisions').fetchone()[0] == 1
    restored = Engine(Database(backup))
    assert restored.snapshot(setup['universe_id'])['universe']['name'] == 'Nuevo universo'


def test_same_world_multiple_campaigns_and_independent_universes(tmp_path):
    c = client(tmp_path)
    first = wizard(c)
    second = c.post('/api/wizard', json=dict(universe_id=first['universe_id'], world_id=first['world_id'], protagonist_id=first['protagonist_id'], campaign_name='Segunda etapa', protagonist_name='No crear otro', mode='discover')).json()
    assert second['protagonist_id'] == first['protagonist_id']
    state = c.get(f"/api/universes/{first['universe_id']}").json()
    assert len([r for r in state['records'] if r['kind'] == 'character']) == 1
    assert len([r for r in state['records'] if r['kind'] == 'campaign']) == 2
    third = wizard(c)
    assert third['universe_id'] != first['universe_id']


class FakeNarrator:
    name = 'test-provider'
    def narrate(self, context, turns):
        assert context['audience'] == 'narrator'
        return 'Propuesta de escena; todavía no validada.'


def test_model_can_only_create_draft(tmp_path):
    c = client(tmp_path, FakeNarrator())
    setup = wizard(c)
    response = c.post(f"/api/campaigns/{setup['campaign_id']}/generate", json={'expected_version': 1})
    assert response.status_code == 200, response.text
    state = c.get(f"/api/universes/{setup['universe_id']}").json()
    assert state['universe']['version'] == 1
    assert len(state['proposals']) == 1
    assert not [r for r in state['records'] if r['kind'] == 'event']


def test_narrator_cast_is_persisted_and_incorrect_identity_blocks_acceptance(tmp_path):
    c = client(tmp_path)
    setup = wizard(c)
    route = f"/api/campaigns/{setup['campaign_id']}/turns"
    response = c.post(route, json={
        'role': 'narrator', 'content': 'Escena ficticia que declara una identidad equivocada.',
        'cast': [{'character_id': setup['protagonist_id'], 'name': 'Otro nombre inventado'}],
    })
    assert response.status_code == 200, response.text
    proposal_id = response.json()['proposal_id']
    c.close()
    reopened = client(tmp_path)
    state = reopened.get(f"/api/universes/{setup['universe_id']}").json()
    draft = next(p for p in state['proposals'] if p['id'] == proposal_id)
    assert draft['record']['data']['cast'][0]['name'] == 'Otro nombre inventado'
    assert any(i['code'] == 'cast_name' for i in draft['issues'])
    result = reopened.post(f'/api/proposals/{proposal_id}/accept', json={
        'expected_version': state['universe']['version'],
        'decision': 'Intento ficticio de aceptación general.', 'acknowledge_warnings': True,
        'authorize_character_change': True,
    })
    assert result.status_code == 422
    after = reopened.get(f"/api/universes/{setup['universe_id']}").json()
    assert after['records'] == state['records']
    assert after['universe']['version'] == state['universe']['version']


def test_player_turn_cannot_include_narrator_cast_metadata(tmp_path):
    c = client(tmp_path)
    setup = wizard(c)
    route = f"/api/campaigns/{setup['campaign_id']}/turns"
    response = c.post(route, json={'role': 'user', 'content': 'Decido observar.',
                                  'cast': [{'character_id': setup['protagonist_id'], 'name': 'Ada'}]})
    assert response.status_code == 422
    assert c.get(route).json()['turns'] == []


def test_manual_mode_has_no_network_requirement(tmp_path):
    c = client(tmp_path)
    setup = wizard(c)
    response = c.post(f"/api/campaigns/{setup['campaign_id']}/generate", json={'expected_version': 1})
    assert response.status_code == 422
    assert 'manual' in response.json()['detail']


def test_invalid_typed_payload_is_rejected(tmp_path):
    c = client(tmp_path)
    setup = wizard(c)
    response = c.post(f"/api/universes/{setup['universe_id']}/proposals", json=dict(title='Falso saldo', record=dict(kind='character', name='Test', data={'balance': -1})))
    assert response.status_code == 422


def test_export_preserves_unicode_and_marks_drafts(tmp_path):
    c = client(tmp_path)
    setup = wizard(c)
    response = c.get(f"/api/universes/{setup['universe_id']}/export.md")
    assert response.status_code == 200
    assert 'Versión del canon: 1' in response.text
    assert 'borradores no se exportan como canon' in response.text
