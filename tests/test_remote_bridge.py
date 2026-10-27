"""Private HTTP bridge boundaries; all worlds and secrets here are fictional."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from narrative.api import create_app
from narrative.db import BridgeGrant, Database
from narrative.remote_bridge import RemoteBridge, TOOLS
from narrative.schemas import Acceptance, ProposalInput, RecordInput, Wizard
from narrative.security import CloudAccess
from narrative.service import Engine


@pytest.fixture
def game(tmp_path):
    path = tmp_path / 'bridge.db'
    access = CloudAccess.build('https://testserver', 'Contraseña ficticia para la prueba', 'x' * 48)
    app = create_app(path, access=access)
    owner = TestClient(app, base_url='https://testserver')
    owner.headers['X-CSRF-Token'] = owner.get('/api/auth').json()['csrf_token']
    assert owner.post('/api/login', json={'password': 'Contraseña ficticia para la prueba'}).status_code == 200
    owner.headers['X-CSRF-Token'] = owner.get('/api/bootstrap').json()['csrf_token']
    setup = app.state.engine.wizard(Wizard(universe_name='Prueba ficticia remota', campaign_name='Gremio de prueba', protagonist_name='Lua'))
    response = owner.post('/api/bridge/grants', json={'universe_id': setup['universe_id'], 'label': 'Conector ficticio'})
    assert response.status_code == 200
    credential = response.json()
    remote = TestClient(app, base_url='https://testserver')
    remote.headers['Authorization'] = 'Bearer ' + credential['token']
    yield dict(app=app, owner=owner, remote=remote, access=access, path=path,
               engine=app.state.engine, setup=setup, credential=credential)
    app.state.db.engine.dispose()


def call(game, name, **arguments):
    return game['remote'].post('/bridge/tools/call', json={'name': name, 'arguments': arguments})


def approve_fact(game, statement):
    engine, setup = game['engine'], game['setup']
    version = engine.snapshot(setup['universe_id'])['universe']['version']
    proposal = engine.propose(setup['universe_id'], ProposalInput(title='Dato ficticio',
        expected_version=version, record=RecordInput(kind='fact', name='Dato ficticio',
            world_id=setup['world_id'], campaign_id=setup['campaign_id'],
            data={'statement': statement, 'certainty': 'confirmed'})))
    return engine.accept(proposal['id'], Acceptance(expected_version=version,
        decision='Aprobación humana explícita para datos ficticios.', acknowledge_warnings=True))


def test_catalog_is_exactly_the_ten_scoped_read_draft_tools(game):
    response = game['remote'].get('/bridge/tools/list')
    assert response.status_code == 200
    data = response.json()
    assert len(data['tools']) == 10
    assert {item['name'] for item in data['tools']} == set(TOOLS)
    assert all(item['inputSchema']['additionalProperties'] is False for item in data['tools'])
    assert all(item['annotations']['destructiveHint'] is False for item in data['tools'])
    assert 'Every write here is pending human review' in data['instructions']
    assert response.headers['Cache-Control'] == 'no-store'
    assert call(game, 'list_campaigns').json()['universe_id'] == game['setup']['universe_id']


def test_player_message_requires_separate_owner_review(game):
    setup = game['setup']
    args = dict(campaign_id=setup['campaign_id'], protagonist_id=setup['protagonist_id'],
                content='Me quedo aquí.\nNo acepto el contrato.', expected_version=1, request_id='human-text')
    response = call(game, 'save_player_message', **args)
    assert response.status_code == 200
    turn = response.json()['turn']
    assert turn['role'] == 'player_pending'
    assert call(game, 'save_player_message', **args, role='user').status_code == 422
    assert call(game, 'save_player_message', **args, attribution_confirmed=True).status_code == 422
    assert call(game, 'review_player_message', campaign_id=setup['campaign_id']).status_code == 404
    path = f"/api/campaigns/{setup['campaign_id']}/turns/{turn['id']}/review-player"
    body = dict(decision='confirm', expected_version=1, content=args['content'])
    assert game['remote'].post(path, json=body).status_code == 401
    assert game['owner'].post(path, json=body, headers={'X-CSRF-Token': ''}).status_code == 403
    result = game['owner'].post(path, json=body)
    assert result.status_code == 200 and result.json()['role'] == 'user'
    assert call(game, 'save_player_message', **args).json()['turn'] == result.json()


def test_only_hash_stored_and_token_returned_exactly_once(game):
    token = game['credential']['token']
    with game['app'].state.db.session() as session:
        row = session.get(BridgeGrant, game['credential']['grant']['id'])
        assert row.token_hash == hashlib.sha256(token.split('.')[1].encode()).hexdigest()
        assert token not in json.dumps(row.__dict__, default=str)
    listed = game['owner'].get('/api/bridge/grants')
    assert listed.status_code == 200
    assert token not in listed.text
    assert 'token_hash' not in listed.text and '"token"' not in listed.text
    assert listed.json()['grants'][0]['active'] is True


@pytest.mark.parametrize('authorization', ['', 'Bearer invalid', 'Bearer umbral_bg_00000000-0000-0000-0000-000000000000.' + 'x' * 43])
def test_missing_or_invalid_token_cannot_use_owner_cookie_instead(game, authorization):
    response = game['owner'].get('/bridge/tools/list', headers={'Authorization': authorization})
    assert response.status_code == 401
    assert 'tools' not in response.json()


def test_wrong_secret_revocation_and_expiry_all_fail_closed(game):
    original = game['remote'].headers['Authorization']
    game['remote'].headers['Authorization'] = original[:-1] + ('x' if original[-1] != 'x' else 'y')
    assert call(game, 'list_campaigns').status_code == 401
    game['remote'].headers['Authorization'] = original
    identity = game['credential']['grant']['id']
    with game['app'].state.db.session(True) as session:
        session.get(BridgeGrant, identity).expires_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert call(game, 'list_campaigns').status_code == 401
    with game['app'].state.db.session(True) as session:
        session.get(BridgeGrant, identity).expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert game['owner'].post('/api/bridge/grants/' + identity + '/revoke').status_code == 200
    assert call(game, 'list_campaigns').status_code == 401
    assert game['owner'].get('/api/bridge/grants').json()['grants'][0]['active'] is False


def test_grants_require_owner_cookie_and_csrf_and_cannot_be_minted_by_machine(game):
    body = {'universe_id': game['setup']['universe_id'], 'label': 'No autorizado'}
    assert game['remote'].get('/api/bridge/grants').status_code == 401
    assert game['remote'].post('/api/bridge/grants', json=body).status_code == 401
    assert game['owner'].post('/api/bridge/grants', json=body, headers={'X-CSRF-Token': ''}).status_code == 403
    identity = game['credential']['grant']['id']
    assert game['remote'].post('/api/bridge/grants/' + identity + '/revoke').status_code == 401
    assert game['owner'].post('/api/bridge/grants/' + identity + '/revoke', headers={'X-CSRF-Token': ''}).status_code == 403
    for method in ('create_grant', 'accept', 'reject', 'revert', 'add_turn', 'switch_protagonist', 'engine.accept', '__getattribute__'):
        assert call(game, method).status_code == 404


def test_host_origin_methods_and_unknown_paths_are_not_csrf_loopholes(game):
    assert game['remote'].get('/bridge/tools/list', headers={'Host': 'evil.example'}).status_code == 403
    assert game['remote'].get('/bridge/tools/list', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert game['remote'].get('/bridge/tools/list', headers={'Origin': 'https://testserver'}).status_code == 200
    assert game['remote'].post('/bridge/tools/list').status_code == 405
    assert game['remote'].get('/bridge/tools/call').status_code == 405
    assert game['remote'].post('/bridge/tools/call/').status_code == 404
    assert game['remote'].post('/bridge/grants').status_code == 404
    assert game['remote'].post('/api/universes', json={'name': 'Intrusión'}).status_code == 401


def test_remote_transport_is_disabled_without_private_cloud_access(tmp_path):
    app = create_app(tmp_path / 'local.db')
    client = TestClient(app)
    client.headers['X-CSRF-Token'] = client.get('/api/bootstrap').json()['csrf_token']
    assert client.get('/bridge/tools/list').status_code == 404
    assert client.get('/api/bridge/grants').status_code == 404
    assert client.post('/api/bridge/grants', json={'universe_id': 'none', 'label': 'No'}).status_code == 404


def test_other_universe_context_and_write_are_inaccessible(game):
    other = game['engine'].wizard(Wizard(universe_name='Otro universo ficticio', campaign_name='Otra', protagonist_name='Sol'))
    assert call(game, 'read_campaign_context', campaign_id=other['campaign_id'], audience='narrator').status_code == 404
    assert call(game, 'recent_turns', campaign_id=other['campaign_id']).status_code == 404
    assert call(game, 'search_memory', query='Sol', audience='narrator', campaign_id=other['campaign_id']).status_code == 404
    assert call(game, 'save_narrator_draft', campaign_id=other['campaign_id'], content='Intrusión', expected_version=1, request_id='intrusion').status_code == 404
    assert call(game, 'list_campaigns', universe_id=other['universe_id']).status_code == 422
    assert game['engine'].turns(other['campaign_id'])['turns'] == []


def test_character_projection_never_reveals_secret_or_private_transcript(game):
    approve_fact(game, 'La clave secreta es zafiro.')
    campaign_id = game['setup']['campaign_id']
    assert call(game, 'save_narrator_draft', campaign_id=campaign_id, content='Nota privada sobre zafiro.', expected_version=2, request_id='secreto').status_code == 200
    character = call(game, 'read_campaign_context', campaign_id=campaign_id).json()
    assert 'zafiro' not in json.dumps(character)
    assert 'zafiro' in json.dumps(call(game, 'read_campaign_context', campaign_id=campaign_id, audience='narrator').json())
    search = call(game, 'search_memory', query='zafiro', campaign_id=campaign_id).json()
    assert search['results'] == []
    assert call(game, 'search_memory', query='zafiro').status_code == 422


def test_draft_survives_restart_idempotently_and_stale_drafts_do_not_write(game):
    args = dict(campaign_id=game['setup']['campaign_id'], content='La placa de metal tiene una espiral.', expected_version=1, request_id='same-scene')
    first = call(game, 'save_narrator_draft', **args)
    assert first.status_code == 200
    assert first.json()['canonical_write'] is False and first.json()['proposal']['status'] == 'pending'
    reopened = create_app(game['path'], access=game['access'])
    try:
        remote = TestClient(reopened, base_url='https://testserver')
        remote.headers['Authorization'] = game['remote'].headers['Authorization']
        again = remote.post('/bridge/tools/call', json={'name': 'save_narrator_draft', 'arguments': args})
        assert again.json()['replayed'] is True
        assert again.json()['turn'] == first.json()['turn']
    finally:
        reopened.state.db.engine.dispose()
    assert call(game, 'save_narrator_draft', **{**args, 'content': 'Texto cambiado.'}).status_code == 409
    approve_fact(game, 'Dato posterior ficticio.')
    assert call(game, 'save_narrator_draft', **{**args, 'request_id': 'stale-new-scene'}).status_code == 409
    assert len(game['engine'].turns(args['campaign_id'])['turns']) == 1
    assert game['engine'].snapshot(game['setup']['universe_id'])['universe']['version'] == 2


def test_structured_proposal_and_schema_preserve_uncertainty_without_acceptance(game):
    setup = game['setup']
    args = dict(campaign_id=setup['campaign_id'], title='Rumor ficticio', expected_version=1,
        request_id='rumor', record=dict(kind='fact', name='Rumor', world_id=setup['world_id'],
        campaign_id=setup['campaign_id'], data={'statement': 'Quizá exista una torre.', 'certainty': 'rumor'}))
    response = call(game, 'propose_record', **args)
    assert response.status_code == 200
    assert response.json()['proposal']['record']['data']['certainty'] == 'rumor'
    assert response.json()['canonical_write'] is False
    assert call(game, 'propose_record', **args).json()['replayed'] is True
    identity = response.json()['proposal']['id']
    assert call(game, 'review_issues', proposal_id=identity).json()['status'] == 'pending'
    assert call(game, 'record_schema', kind='detail').json()['kind'] == 'detail'


@pytest.mark.parametrize('arguments', [{'limit': True}, {'offset': '0'}, {'limit': 101}, {'unknown': 'private-input-value'}])
def test_strict_argument_validation_never_echoes_input(game, arguments):
    response = call(game, 'list_campaigns', **arguments)
    assert response.status_code == 422
    assert 'private-input-value' not in response.text


def test_body_size_invalid_json_and_top_level_fields_are_bounded(game):
    remote = game['remote']
    assert remote.post('/bridge/tools/call', content=b'{' + b'x' * 260_000).status_code == 413
    assert remote.post('/bridge/tools/call', content=b'not json').status_code == 422
    assert remote.post('/bridge/tools/call', json={'name': 'list_campaigns', 'arguments': {}, 'url': 'https://private'}).status_code == 422
    assert call(game, 'save_narrator_draft', campaign_id=game['setup']['campaign_id'], content='x' * 50001, expected_version=1, request_id='large').status_code == 422
    # No Content-Length is provided by this streamed request.
    chunks = (b'x' * 65_001 for _ in range(4))
    assert remote.post('/bridge/tools/call', content=chunks).status_code == 413
    invalid_cast = call(game, 'save_narrator_draft', campaign_id=game['setup']['campaign_id'],
        content='Una escena.', expected_version=1, request_id='cast-invalid',
        cast=[{'character_id': game['setup']['protagonist_id'], 'private_unknown_field': 'PRIVATE CAST DATA'}])
    assert invalid_cast.status_code == 422
    assert 'PRIVATE CAST DATA' not in invalid_cast.text


def test_unexpected_exceptions_do_not_leak_driver_or_credential_details(game, monkeypatch):
    def fail(*args):
        raise RuntimeError('postgresql://user:PRIVATE_PASSWORD@host/private/file.db')
    monkeypatch.setattr(RemoteBridge, 'invoke', fail)
    response = call(game, 'list_campaigns')
    assert response.status_code == 500
    assert 'PRIVATE_PASSWORD' not in response.text and 'postgresql' not in response.text
    monkeypatch.setattr(RemoteBridge, 'authenticate', fail)
    response = game['remote'].get('/bridge/tools/list')
    assert response.status_code == 503
    assert 'PRIVATE_PASSWORD' not in response.text


def test_portable_backup_preserves_canon_but_cannot_reactivate_grants(game, tmp_path):
    token = game['credential']['token']
    secret_hash = hashlib.sha256(token.split('.')[1].encode()).hexdigest()
    source = game['engine'].snapshot(game['setup']['universe_id'])
    destination = game['app'].state.db.backup(tmp_path / 'copy.db')
    with sqlite3.connect(destination) as copied:
        assert copied.execute('SELECT count(*) FROM bridge_grants').fetchone()[0] == 0
    assert token.encode() not in destination.read_bytes()
    assert secret_hash.encode() not in destination.read_bytes()
    restored = Database(destination)
    try:
        assert Engine(restored).snapshot(game['setup']['universe_id']) == source
        with restored.session() as session:
            assert list(session.scalars(select(BridgeGrant))) == []
    finally:
        restored.engine.dispose()
    # Export does not revoke or alter the active original connection.
    assert call(game, 'list_campaigns').status_code == 200


def test_migration_upgrades_existing_canon_without_modifying_records(tmp_path):
    # Recreate the pre-bridge schema in a fresh disposable test database only.
    path = tmp_path / 'upgrade.db'
    old = Database(path)
    engine = Engine(old)
    setup = engine.wizard(Wizard(universe_name='Migración ficticia', campaign_name='Prueba', protagonist_name='Lua'))
    before = engine.snapshot(setup['universe_id'])
    with old.session(True) as session:
        session.execute(text('DROP TABLE runtime_secrets'))
        session.execute(text('DROP TABLE bridge_grants'))
        session.execute(text('ALTER TABLE turns DROP COLUMN provenance'))
        session.execute(text("UPDATE alembic_version SET version_num = '0003'"))
    old.engine.dispose()
    upgraded = Database(path)
    try:
        assert Engine(upgraded).snapshot(setup['universe_id']) == before
        with upgraded.session() as session:
            assert session.scalar(text('SELECT version_num FROM alembic_version')) == '0006'
            assert list(session.scalars(select(BridgeGrant))) == []
    finally:
        upgraded.engine.dispose()
