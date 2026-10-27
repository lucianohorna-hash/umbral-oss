from copy import deepcopy

import pytest

from narrative.db import Database
from narrative.mcp_bridge import NarrativeBridge
from narrative.schemas import Acceptance, ProposalInput, RecordInput, TurnInput, Wizard
from narrative.service import DomainError, Engine


@pytest.fixture
def memory_game(tmp_path):
    database = Database(tmp_path / 'memory.sqlite3')
    engine = Engine(database)
    setup = engine.wizard(Wizard(universe_name='Archivo ficticio', campaign_name='Placa azul',
                                 protagonist_name='Lua'))
    bridge = NarrativeBridge(engine, setup['universe_id'])
    yield bridge, engine, setup
    database.engine.dispose()


def add_detail(engine, setup, name, statement, *, visibility='shared', known_by=None, campaign_id=None):
    request = ProposalInput(title='Detalle ficticio: ' + name, record=RecordInput(
        kind='detail', name=name, world_id=setup['world_id'], campaign_id=campaign_id,
        data=dict(statement=statement, certainty='confirmed', visibility=visibility,
                  known_by=known_by or [])))
    return engine.propose(setup['universe_id'], request)


def accept(engine, setup, proposal):
    version = engine.snapshot(setup['universe_id'])['universe']['version']
    return engine.accept(proposal['id'], Acceptance(expected_version=version,
                         decision='Validación ficticia de la prueba.', acknowledge_warnings=True))


def test_character_memory_is_campaign_scoped_and_hides_private_details(memory_game):
    bridge, engine, setup = memory_game
    known = add_detail(engine, setup, 'Placa de cobre', 'La placa tiene un símbolo azul con tres líneas.',
                       known_by=[setup['protagonist_id']], campaign_id=setup['campaign_id'])
    accepted = accept(engine, setup, known)
    secret = add_detail(engine, setup, 'Clave secreta', 'La clave es VIOLETA-731.', visibility='narrator',
                        campaign_id=setup['campaign_id'])
    accept(engine, setup, secret)

    found = bridge.search_memory('simbolo azul', campaign_id=setup['campaign_id'])
    assert found['query'] == 'simbolo azul'
    assert found['total'] == 1
    assert found['next_offset'] is None
    assert found['results'][0]['id'] == accepted['record']['id']
    assert 'VIOLETA-731' not in str(found)
    detail = bridge.read_memory('record', accepted['record']['id'], campaign_id=setup['campaign_id'])
    assert detail['canonical'] is True
    assert 'símbolo azul' in detail['text']

    with pytest.raises(DomainError) as missing_campaign:
        bridge.search_memory('placa')
    assert missing_campaign.value.status == 422

    other = engine.wizard(Wizard(universe_id=setup['universe_id'], campaign_name='Otra campaña',
                                 protagonist_name='Sol'))
    elsewhere = add_detail(engine, other, 'Placa de plata', 'Un símbolo azul con tres líneas.',
                           known_by=[other['protagonist_id']], campaign_id=other['campaign_id'])
    elsewhere = accept(engine, other, elsewhere)
    assert elsewhere['record']['id'] not in {
        row['id'] for row in bridge.search_memory('símbolo azul', campaign_id=setup['campaign_id'])['results']
    }
    with pytest.raises(DomainError) as scoped_read:
        bridge.read_memory('record', elsewhere['record']['id'], campaign_id=setup['campaign_id'])
    assert scoped_read.value.status == 404


def test_narrator_can_read_private_turn_and_source_without_leaking_to_character(memory_game):
    bridge, engine, setup = memory_game
    turn = engine.add_turn(setup['campaign_id'], TurnInput(role='narrator',
                             content='Nota privada ficticia: las tres rayas podrían ser una pista.'))
    narrator_results = bridge.search_memory('tres rayas', audience='narrator')
    match = next(row for row in narrator_results['results'] if row['origin'] == 'turn')
    opened = bridge.read_memory('turn', match['id'], audience='narrator')
    assert opened['id'] == turn['id']
    assert 'podrían ser una pista' in opened['text']
    proposal = next(row for row in engine.snapshot(setup['universe_id'])['proposals']
                    if row['id'] == turn['proposal_id'])
    source = bridge.read_memory('source', proposal['record']['source_id'], audience='narrator')
    assert 'tres rayas podrían ser una pista' in source['text']
    assert bridge.search_memory('pista', campaign_id=setup['campaign_id'])['total'] == 0

    other = engine.wizard(Wizard(campaign_name='Universo ajeno', protagonist_name='Nix'))
    private_other_turn = engine.add_turn(other['campaign_id'], TurnInput(role='narrator', content='Secreto ajeno.'))
    with pytest.raises(DomainError) as error:
        bridge.read_memory('turn', private_other_turn['id'], audience='narrator')
    assert error.value.status == 404
    with pytest.raises(DomainError) as campaign_error:
        bridge.read_memory('turn', turn['id'], campaign_id=other['campaign_id'])
    assert campaign_error.value.status == 404


def test_reading_pending_proposal_never_approves_or_changes_canon(memory_game):
    bridge, engine, setup = memory_game
    proposal = add_detail(engine, setup, 'Símbolo dudoso', 'Quizá el dibujo represente una puerta.',
                          campaign_id=setup['campaign_id'])
    before = deepcopy(engine.snapshot(setup['universe_id']))

    opened = bridge.read_memory('proposal', proposal['id'], audience='narrator')

    after = engine.snapshot(setup['universe_id'])
    assert opened['status'] == 'pending'
    assert opened['canonical'] is False
    assert after == before
    assert next(row for row in after['proposals'] if row['id'] == proposal['id'])['status'] == 'pending'


def test_archived_record_is_retained_as_archived_memory(memory_game):
    bridge, engine, setup = memory_game
    proposal = add_detail(engine, setup, 'Placa archivada', 'Una placa con un símbolo repetido.',
                          known_by=[setup['protagonist_id']], campaign_id=setup['campaign_id'])
    record = accept(engine, setup, proposal)['record']
    current_version = engine.snapshot(setup['universe_id'])['universe']['version']
    engine.revert(setup['universe_id'], current_version, 'Retirada ficticia de la placa.')
    before = deepcopy(engine.snapshot(setup['universe_id']))

    opened = bridge.read_memory('record', record['id'], audience='narrator')

    assert opened['status'] == 'archived'
    assert opened['canonical'] is False
    assert 'símbolo repetido' in opened['text']
    assert engine.snapshot(setup['universe_id']) == before
