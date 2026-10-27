"""A model can preserve attributed text, but cannot prove player authorship."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from narrative.db import Database, Source
from narrative.mcp_bridge import NarrativeBridge
from narrative.schemas import Acceptance, PlayerMessageReview, RecordInput, Wizard
from narrative.service import DomainError, Engine


@pytest.fixture
def game(tmp_path):
    db = Database(tmp_path / 'player.db')
    engine = Engine(db)
    setup = engine.wizard(Wizard(universe_name='Prueba ficticia', campaign_name='Prueba', protagonist_name='Lua'))
    yield NarrativeBridge(engine, setup['universe_id']), engine, setup
    db.engine.dispose()


def save(game, **overrides):
    bridge, _, setup = game
    args = dict(campaign_id=setup['campaign_id'], protagonist_id=setup['protagonist_id'],
                content='  Examino la placa.\n¿Tiene tres marcas? 🗝  ', expected_version=1, request_id='player-1')
    return bridge.save_player_message(**{**args, **overrides})


def review(game, turn, decision='confirm', **overrides):
    _, engine, setup = game
    return engine.review_player_message(setup['campaign_id'], turn['id'], PlayerMessageReview(
        **{**dict(decision=decision, expected_version=1, content=turn['content']), **overrides}))


def test_exact_text_source_and_no_canon_or_proposal(game):
    bridge, engine, setup = game
    before = engine.snapshot(setup['universe_id'])
    result = save(game)
    after = engine.snapshot(setup['universe_id'])
    turn = result['turn']
    assert turn['role'] == 'player_pending' and turn['proposal_id'] is None
    assert result['attribution_confirmed'] is result['canonical'] is result['canonical_write'] is False
    assert before['records'] == after['records'] and before['revisions'] == after['revisions']
    assert before['proposals'] == after['proposals']
    with engine.db.session() as session:
        source = session.get(Source, turn['provenance']['source_id'])
        assert source.content == turn['content'] == '  Examino la placa.\n¿Tiene tres marcas? 🗝  '
    assert bridge.recent_turns(setup['campaign_id'])['turns'] == [turn]
    assert bridge.search_memory('tres marcas', 'character', setup['campaign_id'])['total'] == 0
    assert bridge.search_memory('tres marcas', 'narrator', setup['campaign_id'])['total'] == 2


def test_retry_restart_concurrency_and_backup_preserve_receipt(game, tmp_path):
    bridge, engine, setup = game
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: save(game), range(4)))
    assert sum(not r['replayed'] for r in results) == 1
    assert len({r['turn']['id'] for r in results}) == 1
    backup = engine.db.backup(tmp_path / 'copy.db')
    restored = Database(backup)
    try:
        result = save((NarrativeBridge(Engine(restored), setup['universe_id']), Engine(restored), setup))
        assert result['replayed'] is True
        assert result['turn'] == results[0]['turn']
    finally:
        restored.engine.dispose()


@pytest.mark.parametrize('change', [{'content': 'Me caso.'}, {'expected_version': 2}, {'protagonist_id': 'different'}])
def test_changed_retry_never_overwrites(game, change):
    first = save(game)
    with pytest.raises(DomainError) as caught:
        save(game, **change)
    assert caught.value.status == 409
    assert save(game)['turn'] == first['turn']


@pytest.mark.parametrize('change', [{'expected_version': 0}, {'protagonist_id': 'other'}])
def test_invalid_fresh_message_is_atomic(game, change):
    _, engine, setup = game
    before = engine.snapshot(setup['universe_id'])
    with pytest.raises(DomainError):
        save(game, **change)
    assert engine.turns(setup['campaign_id'])['turns'] == []
    assert engine.snapshot(setup['universe_id']) == before


def test_other_universe_is_inaccessible(game):
    _, engine, setup = game
    other = engine.wizard(Wizard(universe_name='Separado', campaign_name='Otra', protagonist_name='Sol'))
    with pytest.raises(DomainError) as caught:
        save(game, campaign_id=other['campaign_id'], protagonist_id=other['protagonist_id'])
    assert caught.value.status == 404
    assert engine.turns(other['campaign_id'])['turns'] == []


def decision_proposal(game, turn):
    bridge, _, setup = game
    record = RecordInput(kind='event', name='Decisión propuesta', world_id=setup['world_id'],
        campaign_id=setup['campaign_id'], data=dict(summary='Una posible decisión.', certainty='confirmed', day=0,
        actions=[dict(type='protagonist_decision', character_id=setup['protagonist_id'],
                      authorized_turn_id=turn['id'], decision=turn['content'])]))
    return bridge.propose_record(setup['campaign_id'], 'Decisión propuesta', record, 1, 'decision-1')['proposal']


@pytest.mark.parametrize('decision', ['confirm', 'reject'])
def test_model_cannot_authorize_actions_owner_review_is_traced_and_idempotent(game, decision):
    bridge, engine, setup = game
    turn = save(game)['turn']
    proposal = decision_proposal(game, turn)
    assert any(i['code'] == 'player_agency' for i in proposal['issues'])
    with pytest.raises(DomainError):
        engine.accept(proposal['id'], Acceptance(expected_version=1, decision='No basta.', acknowledge_warnings=True))
    result = review(game, turn, decision)
    assert result['role'] == ('user' if decision == 'confirm' else 'player_rejected')
    assert result['provenance']['review']['actor'] == 'user'
    assert result['provenance']['source_id'] == turn['provenance']['source_id']
    with engine.db.session() as session:
        audit = json.loads(session.get(Source, result['provenance']['review']['source_id']).content)
        assert audit['content'] == turn['content'] and audit['decision'] == decision
    assert review(game, turn, decision) == result
    assert save(game)['turn'] == result  # Retry after review cannot reset attribution.
    issues = bridge.review_issues(proposal['id'])['issues']
    assert any(i['code'] == 'player_agency' for i in issues) == (decision == 'reject')
    assert engine.snapshot(setup['universe_id'])['universe']['version'] == 1
    assert engine.snapshot(setup['universe_id'])['proposals'][0]['status'] == 'pending'
    with pytest.raises(DomainError):
        review(game, turn, 'reject' if decision == 'confirm' else 'confirm')


@pytest.mark.parametrize('change', [{'content': 'Otro texto'}, {'expected_version': 0}])
def test_review_requires_the_displayed_text_and_current_version(game, change):
    turn = save(game)['turn']
    with pytest.raises(DomainError):
        review(game, turn, **change)
    assert save(game)['turn']['role'] == 'player_pending'


def test_migration_preserves_old_turns_and_adds_empty_provenance(game):
    _, engine, setup = game
    # Simulate an existing v4 file, then use the ordinary startup migration.
    engine.db.engine.dispose()
    with sqlite3.connect(engine.db.path) as connection:
        connection.execute('DROP TABLE runtime_secrets')
        connection.execute('ALTER TABLE turns DROP COLUMN provenance')
        connection.execute("UPDATE alembic_version SET version_num='0004'")
        connection.execute('INSERT INTO turns VALUES(?,?,?,?,?,?,?,?,?)',
            ('old', setup['universe_id'], setup['campaign_id'], setup['protagonist_id'], 'user', 'Texto previo', None, 1, '2026-01-01'))
    restored = Database(engine.db.path)
    try:
        turn = Engine(restored).turns(setup['campaign_id'])['turns'][0]
        assert turn['content'] == 'Texto previo' and turn['provenance'] == {}
    finally:
        restored.engine.dispose()


def test_optional_narrator_does_not_treat_pending_or_rejected_text_as_human_choice(monkeypatch):
    from narrative.providers import CompatibleHTTPNarrator
    import httpx
    seen = []

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, *, headers, json):
            seen.extend(json['messages'])
            return httpx.Response(200, json={'choices': [{'message': {'content': 'Borrador'}}]}, request=httpx.Request('POST', url))

    monkeypatch.setattr(httpx, 'Client', Client)
    turns = [dict(role='player_pending', content='PENDIENTE'), dict(role='player_rejected', content='RECHAZADO'),
             dict(role='user', content='CONFIRMADO'), dict(role='narrator', content='NARRACIÓN')]
    assert CompatibleHTTPNarrator('https://example.test/v1', 'fake').narrate({}, turns) == 'Borrador'
    assert seen[-2:] == [{'role': 'user', 'content': 'CONFIRMADO'}, {'role': 'assistant', 'content': 'NARRACIÓN'}]
    assert 'PENDIENTE' not in str(seen) and 'RECHAZADO' not in str(seen)
