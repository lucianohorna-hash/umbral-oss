"""Opt-in checks against a real PostgreSQL database (including Supabase).

Set UMBRAL_TEST_DATABASE_URL privately, then run:
    python -m pytest tests/test_postgres_integration.py -q

Install the project's cloud and dev extras first. The normal Database migration
initializes narrative_core. A single new, clearly marked QA demo universe is
created; no existing universe is selected or changed. Cleanup deletes only rows
belonging to that new UUID, in foreign-key order. A forcibly interrupted run may
leave that explicitly named QA universe for inspection.

The backup is a complete database copy in pytest's temporary directory, so use a
private machine and treat it as confidential if the database contains real data.
Connection credentials are never included in test messages.
"""

import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text

from narrative.db import Database, Proposal, Record, Revision, Source, Turn, Universe
from narrative.schemas import Acceptance, ProposalInput, RecordInput, TurnInput, Wizard
from narrative.service import DomainError, Engine
from narrative.mcp_bridge import NarrativeBridge
from narrative.schemas import PlayerMessageReview


MODELS = (Universe, Source, Record, Proposal, Revision, Turn)


def test_postgres_player_attribution_survives_reconnect_and_backup(postgres_qa, tmp_path):
    db, universe_id = postgres_qa
    engine = Engine(db)
    setup = engine.wizard(Wizard(universe_id=universe_id, campaign_name='QA mensajes externos', protagonist_name='Lua QA'))
    version = engine.snapshot(universe_id)['universe']['version']
    args = dict(campaign_id=setup['campaign_id'], protagonist_id=setup['protagonist_id'],
                content='PRUEBA FICTICIA: observo la placa, sin tocarla.', expected_version=version, request_id='pg-player')
    bridge = NarrativeBridge(engine, universe_id)
    original = bridge.save_player_message(**args)
    assert original['turn']['role'] == 'player_pending'
    engine.review_player_message(setup['campaign_id'], original['turn']['id'], PlayerMessageReview(
        content=args['content'], expected_version=version, decision='confirm'))
    rows = _rows(db, universe_id)
    db.engine.dispose()
    reopened = Database(os.environ['UMBRAL_TEST_DATABASE_URL'])
    try:
        replay = NarrativeBridge(Engine(reopened), universe_id).save_player_message(**args)
        assert replay['replayed'] and replay['attribution_confirmed']
        assert replay['turn']['content'] == args['content']
        assert _rows(reopened, universe_id) == rows
        copy = Database(reopened.backup(tmp_path / 'qa-player-backup.sqlite3'))
        try:
            assert _rows(copy, universe_id) == rows
        finally:
            copy.engine.dispose()
    finally:
        reopened.engine.dispose()


def _rows(db, universe_id):
    """Compare every persisted column, independent of backend row ordering."""
    with db.session() as session:
        return {
            model.__tablename__: [dict(row) for row in session.execute(
                select(model.__table__).where(
                    model.id == universe_id if model is Universe
                    else model.universe_id == universe_id
                ).order_by(model.id)
            ).mappings()]
            for model in MODELS
        }


@pytest.fixture
def postgres_qa():
    if not os.environ.get('UMBRAL_TEST_DATABASE_URL'):
        pytest.skip('Define UMBRAL_TEST_DATABASE_URL para habilitar PostgreSQL real.')
    if not os.environ['UMBRAL_TEST_DATABASE_URL'].startswith(
        ('postgres://', 'postgresql://', 'postgresql+psycopg://')
    ):
        pytest.fail('UMBRAL_TEST_DATABASE_URL debe identificar PostgreSQL.', pytrace=False)

    # Do not retain a URL string in fixture locals or expose connection errors
    # (pytest can otherwise render credentials in a traceback).
    try:
        db = Database(os.environ['UMBRAL_TEST_DATABASE_URL'])
    except Exception:
        pytest.fail(
            'No se pudo inicializar PostgreSQL. Verifica en privado la conexión '
            'y la instalación del extra cloud; no compartas las credenciales.',
            pytrace=False,
        )
    universe_id = None
    try:
        with db.session() as session:
            assert db.is_postgres
            assert session.scalar(text('SELECT current_schema()')) == 'narrative_core'
        engine = Engine(db)
        universe = engine.create_universe(
            'QA PostgreSQL integración ' + str(uuid4()),
            description='Datos ficticios y temporales creados solo por test_postgres_integration.',
            is_demo=True,
        )
        universe_id = universe['id']
        yield db, universe_id
    finally:
        try:
            if universe_id is not None:
                # All children are newly created by this scenario. Never delete
                # by a name, demo flag, shared schema, or unscoped table predicate.
                with db.session(True) as session:
                    for model in (Turn, Revision, Proposal, Record, Source):
                        session.execute(delete(model).where(model.universe_id == universe_id))
                    session.execute(delete(Universe).where(Universe.id == universe_id))
        finally:
            db.engine.dispose()


def _accept_detail(engine, setup, name, **data):
    version = engine.snapshot(setup['universe_id'])['universe']['version']
    proposal = engine.propose(setup['universe_id'], ProposalInput(
        title=name,
        expected_version=version,
        record=RecordInput(
            kind='detail', name=name, world_id=setup['world_id'],
            campaign_id=setup['campaign_id'], data=data,
        ),
    ))
    return engine.accept(proposal['id'], Acceptance(
        expected_version=version,
        decision='Validación explícita de datos ficticios QA.',
        acknowledge_warnings=True,
    ))['record']


def test_postgres_restart_retry_privacy_and_sqlite_backup(postgres_qa, tmp_path):
    db, universe_id = postgres_qa
    engine = Engine(db)
    setup = engine.wizard(Wizard(
        universe_id=universe_id, campaign_name='QA persistencia PostgreSQL',
        protagonist_name='Iria QA', world_name='Mundo QA ficticio',
    ))
    assert engine.snapshot(universe_id)['universe']['is_demo'] is True
    request = TurnInput(
        role='narrator', content='La placa metálica tiene una espiral azul.\nNadie conoce su origen.',
        cast=[], request_id=str(uuid4()),
        expected_version=engine.snapshot(universe_id)['universe']['version'],
    )
    original_turn = engine.add_turn(setup['campaign_id'], request)
    secret = _accept_detail(
        engine, setup, 'QA clave oculta',
        statement='La clave secreta del templo es zafiro.', certainty='confirmed',
    )
    visible = _accept_detail(
        engine, setup, 'QA placa metálica',
        statement='Quizá la placa metálica pertenezca al templo.', certainty='hypothesis',
        known_by=[setup['protagonist_id']],
        features={'material': 'metal', 'símbolo': 'espiral azul'},
        tags=['etiqueta privada QA'], evidence_ids=[secret['id']],
        connections=[{'target_id': secret['id'], 'relation': 'vínculo secreto QA'}],
    )
    expected_rows = _rows(db, universe_id)
    assert expected_rows['universes'][0]['version'] > original_turn['canon_version']
    original_read = engine.read_memory(universe_id, 'record', visible['id'])
    original_search = engine.search_memory(universe_id, 'PLACA METALICA', limit=1000)
    assert visible['id'] in {row['id'] for row in original_search['results']}

    # Dispose the original pool; the second Database/Engine must reconnect and
    # obtain committed rows rather than retaining in-memory session state.
    db.engine.dispose()
    reopened_db = Database(os.environ['UMBRAL_TEST_DATABASE_URL'])
    try:
        restarted = Engine(reopened_db)
        assert _rows(reopened_db, universe_id) == expected_rows
        assert restarted.read_memory(universe_id, 'record', visible['id']) == original_read
        assert restarted.search_memory(universe_id, 'PLACA METALICA', limit=1000) == original_search
        assert restarted.add_turn(setup['campaign_id'], request) == original_turn
        assert _rows(reopened_db, universe_id) == expected_rows
        with pytest.raises(DomainError) as conflict:
            restarted.add_turn(setup['campaign_id'], request.model_copy(update={'request_id': str(uuid4())}))
        assert conflict.value.status == 409
        assert _rows(reopened_db, universe_id) == expected_rows

        projected = restarted.read_memory(
            universe_id, 'record', visible['id'], 'character', setup['campaign_id'],
        )
        assert projected['certainty'] == 'hypothesis'
        assert projected['payload']['data']['features'] == visible['data']['features']
        assert original_read['payload']['data']['connections'][0]['certainty'] == 'hypothesis'
        serialized = json.dumps(projected, ensure_ascii=False)
        assert all(private not in serialized for private in (
            secret['id'], 'vínculo secreto QA', 'etiqueta privada QA', 'source_ref',
        ))
        assert restarted.search_memory(
            universe_id, 'clave secreta', 'character', setup['campaign_id'],
        )['total'] == 0
        with pytest.raises(DomainError) as hidden:
            restarted.read_memory(universe_id, 'record', secret['id'], 'character', setup['campaign_id'])
        assert hidden.value.status == 404
        assert _rows(reopened_db, universe_id) == expected_rows

        backup = reopened_db.backup(tmp_path / 'qa-postgres-backup.sqlite3')
        restored_db = Database(backup)
        try:
            restored = Engine(restored_db)
            assert not restored_db.is_postgres
            assert _rows(restored_db, universe_id) == expected_rows
            assert restored.read_memory(universe_id, 'record', visible['id']) == original_read
            assert restored.search_memory(universe_id, 'PLACA METALICA', limit=1000) == original_search
            assert restored.turns(setup['campaign_id']) == restarted.turns(setup['campaign_id'])
            assert restored.read_memory(
                universe_id, 'record', visible['id'], 'character', setup['campaign_id'],
            ) == projected
        finally:
            restored_db.engine.dispose()
    finally:
        reopened_db.engine.dispose()
