import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from narrative.db import Database
from narrative.mcp_bridge import NarrativeBridge, create_server, main
from narrative.schemas import Acceptance, ProposalInput, RecordInput, TurnInput, Wizard
from narrative.service import DomainError, Engine


@pytest.fixture
def bridge_game(tmp_path):
    database = Database(tmp_path / 'bridge.sqlite3')
    engine = Engine(database)
    setup = engine.wizard(Wizard(universe_name='Ejemplo ficticio MCP', campaign_name='Prueba', protagonist_name='Lua'))
    bridge = NarrativeBridge(engine, setup['universe_id'])
    yield bridge, engine, setup
    database.engine.dispose()


def fact(setup, **data):
    return RecordInput(kind='fact', name='Dato ficticio', world_id=setup['world_id'],
                       campaign_id=setup['campaign_id'], data={'statement': 'El puente está cerrado.', **data})


def accept(engine, setup, proposal):
    version = engine.snapshot(setup['universe_id'])['universe']['version']
    return engine.accept(proposal['id'], Acceptance(expected_version=version,
                         decision='Validación expresa del usuario en esta prueba.', acknowledge_warnings=True))


def test_narrator_save_is_atomic_pending_and_has_original_source(bridge_game):
    bridge, engine, setup = bridge_game
    before = engine.snapshot(setup['universe_id'])
    result = bridge.save_narrator_draft(setup['campaign_id'], 'Quizá exista una torre.', 1, 'scene-1')
    after = engine.snapshot(setup['universe_id'])
    assert result['canonical'] is False
    assert result['turn']['role'] == 'narrator'
    assert result['proposal']['status'] == 'pending'
    assert result['proposal']['record']['data']['certainty'] == 'unknown'
    assert after['records'] == before['records']
    assert after['revisions'] == before['revisions']
    assert len(after['sources']) == len(before['sources']) + 1
    assert result['turn']['proposal_id'] == result['proposal']['id']
    assert result['proposal']['record']['source_ref']['turn_id'] == result['turn']['id']


def test_repeated_save_survives_restart_without_duplicate_turn_or_proposal(bridge_game):
    bridge, engine, setup = bridge_game
    args = (setup['campaign_id'], 'La escena propuesta.', 1, 'retry-1')
    first = bridge.save_narrator_draft(*args)
    database = Database(engine.db.path)
    try:
        resumed = NarrativeBridge(Engine(database), setup['universe_id'])
        again = resumed.save_narrator_draft(*args)
        assert again['replayed'] is True
        assert again['turn'] == first['turn']
        assert len(engine.turns(setup['campaign_id'])['turns']) == 1
        assert len(engine.snapshot(setup['universe_id'])['proposals']) == 1
    finally:
        database.engine.dispose()


def test_conflicting_retry_does_not_overwrite_saved_text(bridge_game):
    bridge, engine, setup = bridge_game
    bridge.save_narrator_draft(setup['campaign_id'], 'Primera escena.', 1, 'same')
    with pytest.raises(DomainError) as error:
        bridge.save_narrator_draft(setup['campaign_id'], 'Texto diferente.', 1, 'same')
    assert error.value.status == 409
    assert engine.turns(setup['campaign_id'])['turns'][0]['content'] == 'Primera escena.'


def test_concurrent_retries_create_one_transaction(bridge_game):
    bridge, engine, setup = bridge_game
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: bridge.save_narrator_draft(setup['campaign_id'], 'Una escena.', 1, 'parallel'), range(2)))
    assert sorted(r['replayed'] for r in results) == [False, True]
    assert len(engine.turns(setup['campaign_id'])['turns']) == 1
    assert len(engine.snapshot(setup['universe_id'])['proposals']) == 1


def test_stdio_cli_negotiates_protocol_and_reads_same_database(bridge_game):
    mcp = pytest.importorskip('mcp')
    bridge, engine, setup = bridge_game
    params = mcp.StdioServerParameters(command=sys.executable,
                 args=['-m', 'narrative.mcp_bridge', '--db', str(engine.db.path), '--universe', setup['universe_id']],
                 cwd=Path(__file__).resolve().parents[1], env={'PYTHONUTF8': '1'})

    async def exercise():
        async with mcp.Client(params, read_timeout_seconds=15) as client:
            catalog = await client.list_tools()
            assert len(catalog.tools) == 10
            result = await client.call_tool('list_campaigns')
            assert not result.is_error
            assert result.structured_content['campaigns'][0]['id'] == setup['campaign_id']
            memory = await client.call_tool('search_memory', dict(query='recuerdo', campaign_id=setup['campaign_id']))
            assert not memory.is_error
            assert set(memory.structured_content) == {'query', 'total', 'next_offset', 'results'}
            saved = await client.call_tool('save_narrator_draft', dict(campaign_id=setup['campaign_id'],
                         content='Escena por stdio: aún es un borrador.', expected_version=1, request_id='stdio-1'))
            assert not saved.is_error
            assert saved.structured_content['canonical_write'] is False
    asyncio.run(exercise())
    assert len(engine.turns(setup['campaign_id'])['turns']) == 1
    assert engine.snapshot(setup['universe_id'])['universe']['version'] == 1
    assert len(engine.snapshot(setup['universe_id'])['proposals']) == 1


def test_stale_narration_or_proposal_never_leaves_partial_source(bridge_game):
    bridge, engine, setup = bridge_game
    pending = engine.propose(setup['universe_id'], ProposalInput(title='Dato', record=fact(setup)))
    accept(engine, setup, pending)
    before = deepcopy(engine.snapshot(setup['universe_id']))
    with pytest.raises(DomainError) as error:
        bridge.save_narrator_draft(setup['campaign_id'], 'Obsoleta.', 1, 'stale')
    assert error.value.status == 409
    with pytest.raises(DomainError):
        bridge.propose_record(setup['campaign_id'], 'Obsoleto', fact(setup), 1, 'stale-structured')
    assert engine.snapshot(setup['universe_id']) == before
    assert engine.turns(setup['campaign_id'])['turns'] == []


def test_scoped_bridge_cannot_read_or_write_another_universe(bridge_game):
    bridge, engine, setup = bridge_game
    other = engine.wizard(Wizard(campaign_name='Otro universo', protagonist_name='Sol'))
    other_pending = engine.propose(other['universe_id'], ProposalInput(title='Otro', record=fact(other)))
    assert {x['id'] for x in bridge.list_campaigns()['campaigns']} == {setup['campaign_id']}
    for operation in [lambda: bridge.read_campaign_context(other['campaign_id'], 'narrator'),
                      lambda: bridge.recent_turns(other['campaign_id']),
                      lambda: bridge.save_narrator_draft(other['campaign_id'], 'Intrusión.', 1, 'bad'),
                      lambda: bridge.propose_record(other['campaign_id'], 'Intrusión', fact(other), 1, 'bad'),
                      lambda: bridge.review_issues(other_pending['id'])]:
        with pytest.raises(DomainError) as error:
            operation()
        assert error.value.status == 404
    assert engine.turns(other['campaign_id'])['turns'] == []


def test_character_context_never_includes_secret_or_private_transcript(bridge_game):
    bridge, engine, setup = bridge_game
    pending = engine.propose(setup['universe_id'], ProposalInput(title='Secreto',
                 record=fact(setup, statement='La reina es inmortal.', certainty='confirmed')))
    secret = accept(engine, setup, pending)['record']
    bridge.save_narrator_draft(setup['campaign_id'], 'Nota privada: la reina lo oculta.', 2, 'secret')
    visible = bridge.read_campaign_context(setup['campaign_id'])
    assert secret['id'] not in {x['id'] for x in visible['records']}
    assert 'inmortal' not in json.dumps(visible)
    assert 'reina lo oculta' not in json.dumps(visible)
    assert secret['id'] in {x['id'] for x in bridge.read_campaign_context(setup['campaign_id'], 'narrator')['records']}
    assert bridge.recent_turns(setup['campaign_id'])['audience'] == 'narrator'


def test_structured_proposal_is_idempotent_and_preserves_uncertainty(bridge_game):
    bridge, engine, setup = bridge_game
    record = fact(setup, certainty='rumor')
    result = bridge.propose_record(setup['campaign_id'], 'Rumor oído', record, 1, 'rumor')
    again = bridge.propose_record(setup['campaign_id'], 'Rumor oído', record, 1, 'rumor')
    assert result['proposal'] == again['proposal']
    assert again['replayed']
    assert engine.snapshot(setup['universe_id'])['universe']['version'] == 1
    assert result['proposal']['record']['data']['certainty'] == 'rumor'
    accepted = accept(engine, setup, result['proposal'])
    assert accepted['record']['data']['certainty'] == 'rumor'
    replay = bridge.propose_record(setup['campaign_id'], 'Rumor oído', record, 1, 'rumor')
    assert replay['replayed']
    assert replay['proposal']['status'] == 'accepted'
    assert replay['canonical'] is True
    assert replay['canonical_write'] is False
    assert len(engine.snapshot(setup['universe_id'])['proposals']) == 1


def test_model_cannot_fabricate_user_decision(bridge_game):
    bridge, engine, setup = bridge_game
    record = RecordInput(kind='event', name='Acción inventada', world_id=setup['world_id'],
                         campaign_id=setup['campaign_id'], data=dict(summary='El héroe decide.',
                         certainty='confirmed', day=0, actions=[dict(type='protagonist_decision',
                         character_id=setup['protagonist_id'], decision='Me caso.', authorized_turn_id='inventado')]))
    result = bridge.propose_record(setup['campaign_id'], 'Acción inventada', record, 1, 'fake-decision')
    assert any(issue['severity'] in ['error', 'critical'] for issue in result['proposal']['issues'])
    assert engine.turns(setup['campaign_id'])['turns'] == []
    with pytest.raises(DomainError):
        accept(engine, setup, result['proposal'])
    assert not hasattr(bridge, 'add_turn')


def test_review_is_read_only_and_does_not_rebase(bridge_game):
    bridge, engine, setup = bridge_game
    result = bridge.propose_record(setup['campaign_id'], 'Primero', fact(setup), 1, 'first')
    other = engine.propose(setup['universe_id'], ProposalInput(title='Segundo', record=fact(setup, statement='Otro.')))
    accept(engine, setup, other)
    before = deepcopy(engine.snapshot(setup['universe_id']))
    check = bridge.review_issues(result['proposal']['id'])
    assert check['stale'] is True
    assert check['base_version'] == 1
    assert check['current_version'] == 2
    assert engine.snapshot(setup['universe_id']) == before


def test_recent_turns_bounded_and_ordered(bridge_game):
    bridge, engine, setup = bridge_game
    for text in ['Uno', 'Dos', 'Tres']:
        engine.add_turn(setup['campaign_id'], TurnInput(role='user', content=text))
    assert [x['content'] for x in bridge.recent_turns(setup['campaign_id'], 2)['turns']] == ['Dos', 'Tres']
    with pytest.raises(DomainError):
        bridge.recent_turns(setup['campaign_id'], 51)


def test_cli_has_no_public_http_transport_or_cloud_password_bypass():
    with pytest.raises(SystemExit) as error:
        main(['--universe', 'example', '--transport', 'streamable-http'])
    assert error.value.code == 2
    with pytest.raises(SystemExit) as error:
        main(['--universe', 'example', '--db', 'postgresql://user:password@host/db'])
    assert error.value.code == 2


def test_sdk_protocol_catalog_and_write_tool(bridge_game):
    mcp = pytest.importorskip('mcp', reason='Integración opcional; instalar requirements.chatgpt.txt para probar el protocolo.')
    bridge, engine, setup = bridge_game
    server = create_server(bridge)

    async def exercise():
        async with mcp.Client(server, raise_exceptions=True) as client:
            catalog = await client.list_tools()
            names = {x.name for x in catalog.tools}
            assert names == {'list_campaigns', 'read_campaign_context', 'search_memory', 'read_memory',
                             'recent_turns', 'save_narrator_draft', 'save_player_message', 'propose_record', 'review_issues',
                             'record_schema'}
            result = await client.call_tool('save_narrator_draft', dict(campaign_id=setup['campaign_id'],
                         content='Escena a través del protocolo.', expected_version=1, request_id='protocol-1'))
            assert not result.is_error
            assert result.structured_content['canonical'] is False
            searched = await client.call_tool('search_memory', dict(query='escena', audience='narrator'))
            assert not searched.is_error
            proposal_id = result.structured_content['proposal']['id']
            opened = await client.call_tool('read_memory', dict(origin='proposal', item_id=proposal_id,
                                      audience='narrator'))
            assert not opened.is_error
            assert opened.structured_content['status'] == 'pending'
            assert opened.structured_content['canonical'] is False
            bad = await client.call_tool('save_narrator_draft', dict(campaign_id=setup['campaign_id'],
                         content='Reintento alterado.', expected_version=1, request_id='protocol-1'))
            assert bad.is_error
            assert 'request_id' in bad.content[0].text
            missing_version = await client.call_tool('save_narrator_draft', dict(campaign_id=setup['campaign_id'],
                                      content='Falta la versión.', request_id='missing-version'))
            assert missing_version.is_error
    asyncio.run(exercise())
    assert engine.snapshot(setup['universe_id'])['universe']['version'] == 1
    assert len(engine.turns(setup['campaign_id'])['turns']) == 1


def established_receptionist(engine, setup):
    """A fictional fixture whose identity must survive narrator draft writes."""
    proposal = engine.propose(setup['universe_id'], ProposalInput(
        title='Recepcionista ficticia para probar continuidad',
        record=RecordInput(kind='character', name='Elara', world_id=setup['world_id'],
                           data={'aliases': ['Señora Elara'],
                                 'personality': {'temperamento': 'Altanera'},
                                 'psychology': {'confianza': 'Reservada'}})))
    return accept(engine, setup, proposal)['record']


@pytest.mark.parametrize('portrayal,expected_issue', [
    ({'name': 'Mira'}, 'cast_name'),
    ({'name': 'Elara', 'personality': {'temperamento': 'Siempre amable'}}, 'cast_personality'),
], ids=['renamed_npc', 'changed_personality'])
def test_bridge_inconsistent_cast_is_saved_as_draft_but_cannot_become_canon(
        bridge_game, portrayal, expected_issue):
    bridge, engine, setup = bridge_game
    npc = established_receptionist(engine, setup)
    before = deepcopy(engine.snapshot(setup['universe_id']))
    result = bridge.save_narrator_draft(
        setup['campaign_id'], 'La viajera regresa al gremio ficticio.',
        before['universe']['version'], 'invalid-cast',
        cast=[{'character_id': npc['id'], **portrayal}])
    assert result['canonical_write'] is False
    assert result['proposal']['status'] == 'pending'
    assert any(issue['code'] == expected_issue and issue['severity'] == 'error'
               for issue in result['proposal']['issues'])
    with pytest.raises(DomainError) as error:
        accept(engine, setup, result['proposal'])
    assert error.value.status == 422
    after = engine.snapshot(setup['universe_id'])
    assert after['records'] == before['records']
    assert after['revisions'] == before['revisions']
    assert after['universe']['version'] == before['universe']['version']
    assert len(engine.turns(setup['campaign_id'])['turns']) == 1


def test_bridge_retry_with_changed_cast_cannot_overwrite_original_portrayal(bridge_game):
    bridge, engine, setup = bridge_game
    npc = established_receptionist(engine, setup)
    cast = [{'character_id': npc['id'], 'name': 'Elara',
             'personality': {'temperamento': 'Altanera'}}]
    args = (setup['campaign_id'], 'Regreso ficticio al gremio.', 2, 'cast-retry')
    first = bridge.save_narrator_draft(*args, cast=cast)
    original = deepcopy(engine.snapshot(setup['universe_id']))
    altered = deepcopy(cast)
    altered[0]['personality']['temperamento'] = 'Siempre amable'
    with pytest.raises(DomainError) as error:
        bridge.save_narrator_draft(*args, cast=altered)
    assert error.value.status == 409
    assert engine.snapshot(setup['universe_id']) == original
    assert len(engine.turns(setup['campaign_id'])['turns']) == 1
    retry = bridge.save_narrator_draft(*args, cast=cast)
    assert retry['replayed'] is True
    assert retry['proposal'] == first['proposal']


def test_bridge_cast_source_and_npc_anchors_survive_restart_without_changing_canon(bridge_game):
    bridge, engine, setup = bridge_game
    npc = established_receptionist(engine, setup)
    before = deepcopy(engine.snapshot(setup['universe_id']))
    cast = [{'character_id': npc['id'], 'name': 'Señora Elara',
             'personality': {'temperamento': 'Altanera'}}]
    saved = bridge.save_narrator_draft(setup['campaign_id'], 'Una escena ficticia por revisar.',
                                      2, 'cast-restart', cast=cast)
    database = Database(engine.db.path)
    try:
        resumed_engine = Engine(database)
        resumed = NarrativeBridge(resumed_engine, setup['universe_id'])
        after = resumed_engine.snapshot(setup['universe_id'])
        assert after['records'] == before['records']
        assert after['revisions'] == before['revisions']
        proposal = next(row for row in after['proposals'] if row['id'] == saved['proposal']['id'])
        assert proposal['record']['data']['cast'] == cast
        assert proposal['record']['source_ref']['turn_id'] == saved['turn']['id']
        assert proposal['record']['source_id'] in {row['id'] for row in after['sources']}
        context = resumed.read_campaign_context(setup['campaign_id'], 'narrator')
        anchor = next(row for row in context['continuity_anchors'] if row['character_id'] == npc['id'])
        assert anchor['name'] == 'Elara'
        assert anchor['personality'] == npc['data']['personality']
        assert anchor['psychology'] == npc['data']['psychology']
        assert 'continuity_anchors' not in resumed.read_campaign_context(setup['campaign_id'], 'character')
    finally:
        database.engine.dispose()
