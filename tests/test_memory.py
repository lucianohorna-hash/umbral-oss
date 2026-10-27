from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from narrative.api import create_app
from narrative.db import Database
from narrative.schemas import Acceptance, ProposalInput, RecordInput, TurnInput, Wizard
from narrative.service import DomainError, Engine


@pytest.fixture
def game(tmp_path):
    db = Database(tmp_path / 'memory.sqlite3')
    engine = Engine(db)
    setup = engine.wizard(Wizard(campaign_name='Viaje', protagonist_name='Iria'))
    yield engine, setup
    db.engine.dispose()


def draft(e, s, kind='detail', data=None, **fields):
    name = fields.pop('name', 'Detalle conservado')
    return e.propose(s['universe_id'], ProposalInput(title=name,
        record=RecordInput(kind=kind, name=name, world_id=fields.pop('world_id', s['world_id']),
                           campaign_id=fields.pop('campaign_id', s['campaign_id']),
                           data=data or {'statement': 'Una placa metálica pequeña con un símbolo azul.', 'certainty': 'confirmed'},
                           **{k: fields.pop(k) for k in ['source_ref', 'source_id'] if k in fields}), **fields))


def accept(e, s, proposal):
    version = e.snapshot(s['universe_id'])['universe']['version']
    return e.accept(proposal['id'], Acceptance(expected_version=version, decision='Validación explícita.', acknowledge_warnings=True))['record']


def detail(e, s, **data):
    return accept(e, s, draft(e, s, data={'statement': 'Una placa metálica pequeña con un símbolo azul.', 'certainty': 'confirmed', **data}))


def test_small_detail_survives_long_conversation_restart_and_backup(game, tmp_path):
    e, s = game
    saved = detail(e, s, known_by=[s['protagonist_id']], features={'material': 'metal', 'símbolo': 'espiral azul'})
    for i in range(90):
        e.add_turn(s['campaign_id'], TurnInput(role='user', content=f'Conversación posterior {i} sobre otras cosas.'))
    path = e.db.path
    e.db.engine.dispose()
    restarted = Engine(Database(path))
    results = restarted.search_memory(s['universe_id'], 'PLACA METALICA')
    assert saved['id'] in {r['id'] for r in results['results']}
    read = restarted.read_memory(s['universe_id'], 'record', saved['id'])
    assert read['payload']['data'] == saved['data']
    assert 'placa metálica pequeña' in read['content']
    context = restarted.context(s['campaign_id'], 'narrator')
    assert saved['id'] in {r['id'] for r in context['records']}
    backup = restarted.db.backup(tmp_path / 'restored.sqlite3')
    restored = Engine(Database(backup))
    assert restored.search_memory(s['universe_id'], 'PLACA METALICA') == results
    assert len(restored.turns(s['campaign_id'])['turns']) == 90
    restored.db.engine.dispose()
    restarted.db.engine.dispose()


def test_matching_features_do_not_merge_or_create_connections(game):
    e, s = game
    data = {'features': {'material': 'metal', 'símbolo': 'espiral'}, 'tags': ['placa']}
    first, second = detail(e, s, **data), detail(e, s, **data)
    assert first['id'] != second['id']
    assert first['data']['connections'] == second['data']['connections'] == []
    search = e.search_memory(s['universe_id'], 'espiral', limit=1000)
    assert {first['id'], second['id']} <= {r['id'] for r in search['results'] if r['origin'] == 'record'}
    assert e.search_memory(s['universe_id'], 'remolino')['total'] == 0
    nonadjacent = e.search_memory(s['universe_id'], 'placa SIMBOLO', limit=1000)
    assert {first['id'], second['id']} <= {r['id'] for r in nonadjacent['results']}
    for result in nonadjacent['results']:
        assert result['fragments']


def test_hypothesis_connection_preserves_its_certainty(game):
    e, s = game
    first = detail(e, s)
    second = detail(e, s, connections=[{'target_id': first['id'], 'relation': 'podría compartir origen'}])
    assert second['data']['certainty'] == 'confirmed'
    assert second['data']['connections'][0]['certainty'] == 'hypothesis'
    assert e.read_memory(s['universe_id'], 'record', second['id'])['payload']['data']['connections'][0]['certainty'] == 'hypothesis'


def test_connection_confirmation_requires_confirmed_relevant_evidence(game):
    e, s = game
    first, second = detail(e, s), detail(e, s)
    def connection(evidence):
        return draft(e, s, operation='replace', target_id=first['id'],
                     data={**first['data'], 'connections': [{'target_id': second['id'], 'relation': 'mismo origen', 'certainty': 'confirmed', 'evidence_ids': evidence}]})
    assert 'detail_connection_evidence' in {i['code'] for i in connection([])['issues']}
    unrelated = accept(e, s, draft(e, s, kind='fact', data={'statement': 'Se parecen.', 'certainty': 'confirmed'}))
    assert 'detail_connection_evidence' in {i['code'] for i in connection([unrelated['id']])['issues']}
    uncertain = accept(e, s, draft(e, s, kind='fact', data={'statement': 'Quizá mismo origen.', 'certainty': 'hypothesis'},
                                 source_ref={'detail_ids': [first['id'], second['id']]}))
    assert 'detail_connection_evidence' in {i['code'] for i in connection([uncertain['id']])['issues']}
    evidence = accept(e, s, draft(e, s, kind='fact', data={'statement': 'El fabricante confirma el origen de ambas placas.', 'certainty': 'confirmed'},
                                source_ref={'detail_ids': [first['id'], second['id']]}))
    updated = accept(e, s, connection([evidence['id']]))
    assert updated['data']['connections'][0]['certainty'] == 'confirmed'


@pytest.mark.parametrize('change', [
    {'place_id': 'missing'}, {'item_id': 'missing'}, {'evidence_ids': ['missing']},
    {'connections': [{'target_id': 'missing', 'relation': 'parecido'}]},
])
def test_detail_reference_validation(game, change):
    e, s = game
    p = draft(e, s, data={'statement': 'Detalle', **change})
    assert 'invalid_reference' in {i['code'] for i in p['issues']}
    with pytest.raises(DomainError):
        accept(e, s, p)


def test_detail_world_and_universe_isolation(game):
    e, s = game
    other = e.wizard(Wizard(campaign_name='Otra', protagonist_name='Otra persona'))
    secret = detail(e, other, statement='Clave aislada.')
    p = draft(e, s, data={'statement': 'Detalle', 'connections': [{'target_id': secret['id'], 'relation': 'supuesto enlace'}]})
    assert 'invalid_reference' in {i['code'] for i in p['issues']}
    assert e.search_memory(s['universe_id'], 'Clave aislada')['total'] == 0
    with pytest.raises(DomainError):
        e.read_memory(s['universe_id'], 'record', secret['id'])
    another_world = e.wizard(Wizard(universe_id=s['universe_id'], campaign_name='Otro mundo', protagonist_name='Otro'))
    place = accept(e, another_world, draft(e, another_world, kind='place', data={'description': 'Lugar remoto'}))
    p = draft(e, s, data={'statement': 'Detalle aquí', 'place_id': place['id']})
    assert 'detail_world' in {i['code'] for i in p['issues']}


def test_character_memory_uses_known_projection_without_private_links(game):
    e, s = game
    secret = detail(e, s, statement='Clave secreta del templo.')
    visible = detail(e, s, known_by=[s['protagonist_id']], tags=['etiqueta privada'], features={'símbolo': 'espiral violeta'},
                     evidence_ids=[secret['id']], connections=[{'target_id': secret['id'], 'relation': 'vínculo secreto'}])
    e.add_turn(s['campaign_id'], TurnInput(role='narrator', content='Conversación privada con la clave secreta del templo.'))
    before = e.snapshot(s['universe_id'])
    restricted = e.search_memory(s['universe_id'], '', 'character', s['campaign_id'], limit=1000)
    assert {r['origin'] for r in restricted['results']} == {'record'}
    assert secret['id'] not in {r['id'] for r in restricted['results']}
    read = e.read_memory(s['universe_id'], 'record', visible['id'], 'character', s['campaign_id'])
    text = json.dumps(read, ensure_ascii=False)
    assert all(private not in text for private in [secret['id'], 'vínculo secreto', 'etiqueta privada', 'source_ref'])
    assert read['payload']['data']['features'] == {'símbolo': 'espiral violeta'}
    assert visible['id'] in {r['id'] for r in e.search_memory(s['universe_id'], 'ESPIRAL VIOLETA', 'character', s['campaign_id'])['results']}
    assert read['source_id'] is None
    assert e.search_memory(s['universe_id'], 'clave secreta', 'character', s['campaign_id'])['total'] == 0
    assert e.snapshot(s['universe_id']) == before
    with pytest.raises(DomainError) as error:
        e.search_memory(s['universe_id'], '', 'character')
    assert error.value.status == 422
    source_id = secret['source_id']
    with pytest.raises(DomainError) as error:
        e.read_memory(s['universe_id'], 'source', source_id, 'character', s['campaign_id'])
    assert error.value.status == 404


def test_detail_knowledge_and_reveal_never_promote_hypothesis(game):
    e, s = game
    saved = detail(e, s, certainty='hypothesis')
    knowledge = accept(e, s, draft(e, s, kind='knowledge', data={'character_id': s['protagonist_id'], 'fact_id': saved['id'], 'belief': 'suspects', 'learned_from': 'Vio una placa.'}))
    own = e.read_memory(s['universe_id'], 'record', saved['id'], 'character', s['campaign_id'])
    assert own['certainty'] == 'hypothesis'
    assert own['payload']['data']['belief'] == 'suspects'
    event = draft(e, s, kind='event', data={'summary': 'Revelación', 'certainty': 'confirmed', 'day': 0,
                  'actions': [{'type': 'reveal', 'character_id': s['protagonist_id'], 'fact_id': saved['id'], 'learned_from': 'Observación directa'}]})
    accept(e, s, event)
    assert e.read_memory(s['universe_id'], 'record', saved['id'])['certainty'] == 'hypothesis'


def test_archival_statuses_exact_fragments_and_pagination(game):
    e, s = game
    text = 'Una lámpara roja.\nAl lado, la placa metálica pequeña con un símbolo azul.\nNadie preguntó por ella.'
    turn = e.add_turn(s['campaign_id'], TurnInput(role='narrator', content=text))
    pending = draft(e, s, data={'statement': 'placa metálica pendiente'})
    rejected = draft(e, s, data={'statement': 'placa metálica rechazada'})
    e.reject(rejected['id'], 'No ocurrió.')
    saved = detail(e, s)
    version = e.snapshot(s['universe_id'])['universe']['version']
    e.revert(s['universe_id'], version, 'Archivar la última incorporación.')
    before = e.snapshot(s['universe_id'])
    all_results = e.search_memory(s['universe_id'], 'PLACA METALICA', limit=1000)
    assert {'record', 'turn', 'source', 'proposal'} <= {r['origin'] for r in all_results['results']}
    results = {(r['origin'], r['id']): r for r in all_results['results']}
    assert results['proposal', pending['id']]['status'] == 'pending'
    assert results['proposal', rejected['id']]['status'] == 'rejected'
    assert results['record', saved['id']]['status'] == 'archived'
    assert results['record', saved['id']]['canonical'] is False
    assert results['turn', turn['id']]['canonical'] is False
    assert results['turn', turn['id']]['excerpt'] in text
    assert e.read_memory(s['universe_id'], 'turn', turn['id'])['content'] == text
    collected, offset = [], 0
    while True:
        page = e.search_memory(s['universe_id'], 'PLACA METALICA', offset=offset, limit=2)
        collected.extend(page['results'])
        if page['next_offset'] is None:
            break
        offset = page['next_offset']
    assert collected == all_results['results']
    assert len(collected) == all_results['total']
    assert e.search_memory(s['universe_id'], 'PLACA METALICA', offset=10000)['results'] == []
    assert e.snapshot(s['universe_id']) == before


def test_turn_provenance_requires_exact_excerpt_and_matching_scope(game):
    e, s = game
    turn = e.add_turn(s['campaign_id'], TurnInput(role='user', content='Veo una placa de metal junto a la puerta.'))
    valid = draft(e, s, source_ref={'turn_id': turn['id'], 'excerpt': 'placa de metal'})
    assert not [i for i in valid['issues'] if i['severity'] == 'error']
    wrong = draft(e, s, source_ref={'turn_id': turn['id'], 'excerpt': 'placa de oro'})
    assert 'detail_excerpt' in {i['code'] for i in wrong['issues']}
    other = e.wizard(Wizard(campaign_name='Ajena', protagonist_name='Ajeno'))
    p = draft(e, other, source_ref={'turn_id': turn['id'], 'excerpt': 'placa de metal'})
    assert 'detail_turn_source' in {i['code'] for i in p['issues']}
    for invalid in ['', None, [], {}]:
        p = draft(e, s, source_ref={'turn_id': invalid})
        assert 'detail_turn_source' in {i['code'] for i in p['issues']}


def test_character_keeps_knowledge_across_campaigns_and_real_provenance(game):
    e, s = game
    other = e.wizard(Wizard(universe_id=s['universe_id'], world_id=s['world_id'], protagonist_id=s['protagonist_id'], campaign_name='Continuación', protagonist_name='Iria'))
    saved = detail(e, other, known_by=[s['protagonist_id']])
    results = e.search_memory(s['universe_id'], 'placa simbolo', 'character', s['campaign_id'])
    match = next(r for r in results['results'] if r['id'] == saved['id'])
    assert match['campaign_id'] == other['campaign_id']
    assert e.read_memory(s['universe_id'], 'record', saved['id'], 'character', s['campaign_id'])['payload']['campaign_id'] == other['campaign_id']


@pytest.mark.parametrize('kwargs', [{'offset': -1}, {'limit': 0}, {'audience': 'invalid'}, {'limit': True}])
def test_invalid_search_parameters(game, kwargs):
    e, s = game
    with pytest.raises(DomainError) as error:
        e.search_memory(s['universe_id'], **kwargs)
    assert error.value.status == 422


def test_memory_http_routes_return_full_content_and_enforce_privacy(tmp_path):
    app = create_app(tmp_path / 'api-memory.sqlite3')
    e = app.state.engine
    s = e.wizard(Wizard(campaign_name='API', protagonist_name='Persona'))
    saved = detail(e, s, known_by=[s['protagonist_id']])
    with TestClient(app) as client:
        path = f"/api/universes/{s['universe_id']}/memory"
        result = client.get(path, params={'q': 'PLACA METALICA', 'limit': 1000}).json()
        assert saved['id'] in {r['id'] for r in result['results']}
        assert client.get(path + '/record/' + saved['id']).json()['payload']['data'] == saved['data']
        assert client.get(path, params={'audience': 'character'}).status_code == 422
        assert client.get(path + '/source/' + saved['source_id'], params={'audience': 'character', 'campaign_id': s['campaign_id']}).status_code == 404
        assert client.get(path, params={'limit': 0}).status_code == 422
    app.state.db.engine.dispose()
