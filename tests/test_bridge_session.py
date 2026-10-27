"""Fictional sessions exercise the same bridge and owner review used by the web."""
from copy import deepcopy

import pytest

from narrative.db import Database
from narrative.mcp_bridge import NarrativeBridge
from narrative.schemas import Acceptance, PlayerMessageReview, RecordInput, TurnInput, Wizard
from narrative.service import DomainError, Engine


@pytest.fixture
def session_game(tmp_path):
    db = Database(tmp_path / 'session.db')
    engine = Engine(db)
    setup = engine.wizard(Wizard(universe_name='Sesión ficticia', campaign_name='Placas', protagonist_name='Vera'))
    yield NarrativeBridge(engine, setup['universe_id']), engine, setup
    db.engine.dispose()


def approve(engine, setup, proposal):
    return engine.accept(proposal['id'], Acceptance(
        expected_version=engine.snapshot(setup['universe_id'])['universe']['version'],
        decision='Revisión expresa de esta prueba ficticia.', acknowledge_warnings=True))


def detail(setup, reference):
    return RecordInput(kind='detail', name='Placa ficticia', world_id=setup['world_id'],
        campaign_id=setup['campaign_id'], source_ref=reference,
        data=dict(statement='Hay una placa de metal.', certainty='confirmed',
                  features={'material': 'metal'}, tags=['placa']))


@pytest.mark.parametrize('case,code', [
    ('missing', 'detail_turn_source'), ('wrong_excerpt', 'detail_excerpt'),
    ('other_universe', 'detail_turn_source'), ('other_campaign', 'detail_turn_source'),
    ('nested', 'detail_excerpt'), ('malformed', 'source_reference'),
    ('deep', 'source_reference_depth'),
])
def test_bridge_wrapped_detail_references_cannot_bypass_review(session_game, case, code):
    bridge, engine, setup = session_game
    scene = bridge.save_narrator_draft(setup['campaign_id'], 'Hay una placa de metal.', 1, 'source')
    reference = {'turn_id': scene['turn']['id'], 'excerpt': 'placa de metal'}
    if case == 'missing':
        reference['turn_id'] = 'not-a-turn'
    elif case in ('other_campaign', 'other_universe'):
        scope = dict(universe_id=setup['universe_id'], world_id=setup['world_id']) if case == 'other_campaign' else {}
        other = engine.wizard(Wizard(campaign_name='Otra campaña ficticia', protagonist_name='Otra viajera', **scope))
        other_scene = engine.add_turn(other['campaign_id'], TurnInput(
            role='narrator', content='Hay una placa de metal.'))
        reference['turn_id'] = other_scene['id']
    elif case == 'malformed':
        reference = {'original_reference': 'not-a-reference-object'}
    elif case == 'deep':
        for _ in range(17):
            reference = {'original_reference': reference}
    else:
        reference['excerpt'] = 'placa de oro'
        if case == 'nested':
            reference = {'producer': 'mcp', 'original_reference': reference}
    result = bridge.propose_record(setup['campaign_id'], 'Detalle a contrastar',
        detail(setup, reference), engine.snapshot(setup['universe_id'])['universe']['version'], 'detail-' + case)
    assert code in {issue['code'] for issue in result['proposal']['issues']}
    before = engine.snapshot(setup['universe_id'])
    with pytest.raises(DomainError) as rejected:
        approve(engine, setup, result['proposal'])
    assert rejected.value.status == 422
    after = engine.snapshot(setup['universe_id'])
    assert after['records'] == before['records']
    assert after['universe']['version'] == before['universe']['version']


def test_session_resumes_with_exact_messages_sources_and_private_details(session_game):
    bridge, engine, setup = session_game
    cid, hero = setup['campaign_id'], setup['protagonist_id']
    player_args = dict(campaign_id=cid, protagonist_id=hero,
        content='PRUEBA FICTICIA: quiero examinar la placa.', expected_version=1, request_id='player')
    player = bridge.save_player_message(**player_args)['turn']
    engine.review_player_message(cid, player['id'], PlayerMessageReview(
        decision='confirm', expected_version=1, content=player['content']))
    text = 'PRUEBA FICTICIA. Hay una placa de metal. Su significado sigue desconocido.'
    narration = bridge.save_narrator_draft(cid, text, 1, 'narration')
    approve(engine, setup, narration['proposal'])
    record = detail(setup, {'turn_id': narration['turn']['id'], 'excerpt': 'placa de metal'})
    candidate = bridge.propose_record(cid, 'Conservar la observación, no su significado', record, 2, 'detail')
    kept = approve(engine, setup, candidate['proposal'])['record']
    before = deepcopy(engine.snapshot(setup['universe_id']))

    # A second connection represents re-entry from another device. It must read
    # committed data without relying on the first process's in-memory state.
    other_db = Database(engine.db.path)
    try:
        resumed = NarrativeBridge(Engine(other_db), setup['universe_id'])
        assert resumed.read_campaign_context(cid, 'narrator')['version'] == 3
        turns = resumed.recent_turns(cid)['turns']
        assert [(t['role'], t['content']) for t in turns] == [('user', player_args['content']), ('narrator', text)]
        retry = resumed.save_player_message(**player_args)
        assert retry['replayed'] and retry['attribution_confirmed']
        hits = resumed.search_memory('placa de metal', 'narrator', cid)['results']
        assert any(hit['id'] == kept['id'] and hit['canonical'] for hit in hits)
        assert resumed.search_memory('placa de metal', 'character', cid)['total'] == 0
        remembered = resumed.read_memory('record', kept['id'], 'narrator', cid)
        assert remembered['payload']['data']['statement'] == 'Hay una placa de metal.'
        assert remembered['payload']['data']['connections'] == []
        assert engine.snapshot(setup['universe_id']) == before
    finally:
        other_db.engine.dispose()
