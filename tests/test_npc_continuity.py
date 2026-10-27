"""Regression scenarios: established NPCs survive absence and long campaigns.

Every name and scene in this module is a fictional test fixture, not imported canon.
"""
from copy import deepcopy

import pytest

from narrative.db import Database, Revision
from narrative.schemas import Acceptance, Advance, ProposalInput, RecordInput, TurnInput, Wizard
from narrative.service import DomainError, Engine


def snapshot(game):
    return game['engine'].snapshot(game['setup']['universe_id'])


def records(game):
    return {row['id']: row for row in snapshot(game)['records']}


def version(game):
    return snapshot(game)['universe']['version']


def propose(game, kind, name, data, *, target=None, world_id=None):
    setup = game['setup']
    return game['engine'].propose(setup['universe_id'], ProposalInput(
        title=name,
        operation='replace' if target else 'create',
        target_id=target,
        record=RecordInput(
            kind=kind, name=name, world_id=world_id or setup['world_id'],
            campaign_id=setup['campaign_id'] if kind == 'event' and world_id is None else None,
            data=data,
        ),
    ))


def accept(game, draft, *, authorize=False, decision='Validación explícita de este escenario ficticio.'):
    return game['engine'].accept(draft['id'], Acceptance(
        expected_version=version(game), decision=decision,
        acknowledge_warnings=True, authorize_character_change=authorize,
    ))


def create(game, kind, name, data, **kwargs):
    return accept(game, propose(game, kind, name, data, **kwargs))['record']


def assert_blocked(game, draft, *, authorize=True):
    before, prior_version = records(game), version(game)
    with pytest.raises(DomainError) as failure:
        accept(game, draft, authorize=authorize)
    assert failure.value.status == 422
    assert records(game) == before
    assert version(game) == prior_version


def change(game, *, name=None, **changed_data):
    npc = records(game)[game['receptionist']['id']]
    return propose(game, 'character', name or npc['name'],
                   {**deepcopy(npc['data']), **changed_data}, target=npc['id'])


def basis(game, *, mode='correction', evidence_ids=None, based_on_revision=None):
    npc = records(game)[game['receptionist']['id']]
    return dict(mode=mode, reason='La fuente y la decisión humana justifican este cambio.',
                based_on_revision=npc['revision'] if based_on_revision is None else based_on_revision,
                evidence_ids=evidence_ids or [])


@pytest.fixture
def game(tmp_path):
    db = Database(tmp_path / 'npc-continuity.sqlite3')
    engine = Engine(db)
    setup = engine.wizard(Wizard(universe_name='Prueba ficticia de permanencia',
                                 campaign_name='Regreso al gremio', protagonist_name='Viajera ficticia'))
    result = {'engine': engine, 'setup': setup}
    guild = create(result, 'place', 'Gremio ficticio del Río', {})
    receptionist = create(result, 'character', 'Elara', {
        'aliases': ['Señora Elara'],
        'location_id': guild['id'],
        'personality': {'temperamento': 'Altanera', 'valores': 'Disciplina'},
        'psychology': {'confianza_hacia_la_viajera': 'Reservada', 'preocupacion': 'Su hermano está ausente.'},
        'roles': [{'place_id': guild['id'], 'label': 'Recepcionista del gremio', 'exclusive': True}],
        'routine': 'Atiende las solicitudes del gremio.',
    })
    result.update(guild=guild, receptionist=receptionist)
    yield result
    result['engine'].db.engine.dispose()
    db.engine.dispose()


def test_receptionist_survives_absence_many_turns_and_restart(game):
    original = deepcopy(game['receptionist'])
    engine, setup = game['engine'], game['setup']
    for number in range(12):
        engine.add_turn(setup['campaign_id'], TurnInput(role='user', content=f'Nota de viaje ficticia {number}.'))
        engine.add_turn(setup['campaign_id'], TurnInput(role='narrator', content=f'Escena de viaje pendiente {number}.'))
    advance = engine.advance(setup['universe_id'], Advance(world_id=setup['world_id'], days=14))
    accept(game, advance)
    path = engine.db.path
    engine.db.engine.dispose()
    game['engine'] = Engine(Database(path))
    current = records(game)[original['id']]
    assert current['name'] == 'Elara'
    assert current['data'] == original['data']
    assert records(game)[setup['world_id']]['data']['current_day'] == 14
    assert len(game['engine'].turns(setup['campaign_id'])['turns']) == 24
    narrator = game['engine'].context(setup['campaign_id'], 'narrator')
    remembered = next(row for row in narrator['records'] if row['id'] == original['id'])
    assert remembered['name'] == 'Elara'
    assert remembered['data']['personality']['temperamento'] == 'Altanera'
    assert remembered['data']['roles'] == original['data']['roles']
    assert remembered['data']['psychology'] == original['data']['psychology']
    anchor = next(row for row in narrator['continuity_anchors'] if row['character_id'] == original['id'])
    assert anchor['name'] == original['name']
    assert anchor['personality'] == original['data']['personality']
    assert anchor['psychology'] == original['data']['psychology']
    player_view = game['engine'].context(setup['campaign_id'], 'character')
    assert 'continuity_anchors' not in player_view
    assert original['id'] not in {row['id'] for row in player_view['records']}


def test_scene_cannot_rename_receptionist_but_established_alias_is_allowed(game):
    identity = game['receptionist']['id']
    data = {'summary': 'La viajera regresa al mismo mostrador.', 'certainty': 'confirmed', 'day': 0,
            'cast': [{'character_id': identity, 'name': 'Mira'}]}
    wrong = propose(game, 'event', 'Regreso con nombre equivocado', data)
    assert_blocked(game, wrong)
    correct = propose(game, 'event', 'Regreso con alias establecido', {
        **data, 'cast': [{'character_id': identity, 'name': 'Señora Elara',
                         'personality': {'temperamento': 'Altanera'}}],
    })
    accept(game, correct)
    assert records(game)[identity]['name'] == 'Elara'


@pytest.mark.parametrize('traits', [
    {'temperamento': 'Siempre amable'},
    {'ambicion_nunca_establecida': 'Ser reina'},
], ids=['changed_trait', 'invented_trait'])
def test_scene_cannot_assert_changed_or_unestablished_personality(game, traits):
    draft = propose(game, 'event', 'Caracterización incoherente', {
        'summary': 'Una descripción propuesta del saludo.', 'certainty': 'confirmed', 'day': 0,
        'cast': [{'character_id': game['receptionist']['id'], 'name': 'Elara', 'personality': traits}],
    })
    assert_blocked(game, draft)


def test_different_name_cannot_silently_replace_exclusive_guild_receptionist(game):
    draft = propose(game, 'character', 'Nueva recepcionista inventada', {
        'location_id': game['guild']['id'],
        'roles': [{'place_id': game['guild']['id'], 'label': '  RECEPCIONISTA   DEL GREMIO  ', 'exclusive': True}],
    })
    assert_blocked(game, draft)
    assert len([row for row in records(game).values() if row['kind'] == 'character']) == 2


@pytest.mark.parametrize('field,value', [
    ('name', 'Mira'), ('aliases', ['Un alias inventado']),
    ('personality', {'temperamento': 'Siempre amable'}),
    ('psychology', {}), ('roles', []),
], ids=['name', 'aliases', 'personality', 'psychology', 'roles'])
def test_established_character_fields_require_change_basis(game, field, value):
    draft = change(game, **{field: value})
    assert_blocked(game, draft)


def test_generic_warning_acknowledgement_does_not_authorize_personality_change(game):
    draft = change(game, personality={'temperamento': 'Siempre amable'}, change_basis=basis(game))
    protected = [issue for issue in draft['issues'] if issue['code'] == 'protected_character_change']
    assert protected and protected[0]['severity'] == 'warning'
    assert_blocked(game, draft, authorize=False)


@pytest.mark.parametrize('evidence_kind', ['rumor', 'unrelated', 'future', 'other_world'])
def test_evolution_rejects_evidence_that_does_not_establish_this_change(game, evidence_kind):
    evidence_data = {'summary': 'Una experiencia propuesta.', 'certainty': 'confirmed', 'day': 0,
                     'participants': [game['receptionist']['id']]}
    options = {}
    if evidence_kind == 'rumor':
        evidence_data['certainty'] = 'rumor'
    elif evidence_kind == 'unrelated':
        evidence_data['participants'] = [game['setup']['protagonist_id']]
    elif evidence_kind == 'future':
        evidence_data['day'] = 100
    else:
        other = game['engine'].wizard(Wizard(
            universe_id=game['setup']['universe_id'], campaign_name='Otra campaña ficticia',
            protagonist_name='Otro personaje ficticio', world_name='Mundo ajeno',
        ))
        options['world_id'] = other['world_id']
        evidence_data['participants'] = [other['protagonist_id']]
    evidence = create(game, 'event', 'Evidencia insuficiente', evidence_data, **options)
    draft = change(game, personality={'temperamento': 'Siempre amable'},
                   change_basis=basis(game, mode='evolution', evidence_ids=[evidence['id']]))
    assert_blocked(game, draft)


def test_supported_evolution_requires_explicit_permission_and_preserves_identity(game):
    original = deepcopy(game['receptionist'])
    evidence = create(game, 'event', 'Elara reconsidera su trato', {
        'summary': 'Elara escucha a una aprendiz y reconoce el daño de su brusquedad. Decide practicar la paciencia.',
        'certainty': 'confirmed', 'day': 0, 'participants': [original['id']],
    })
    updated_traits = {**original['data']['personality'], 'temperamento': 'Altanera, pero más paciente con aprendices'}
    draft = change(game, personality=updated_traits,
                   change_basis=basis(game, mode='evolution', evidence_ids=[evidence['id']]))
    assert_blocked(game, draft, authorize=False)
    accepted = accept(game, draft, authorize=True)['record']
    assert accepted['id'] == original['id']
    assert accepted['name'] == original['name']
    assert accepted['data']['personality'] == updated_traits
    assert accepted['data']['psychology'] == original['data']['psychology']
    assert accepted['data']['roles'] == original['data']['roles']
    assert accepted['data']['change_basis']['evidence_ids'] == [evidence['id']]


def test_explicit_user_correction_is_audited_and_reversible(game):
    original = deepcopy(game['receptionist'])
    draft = change(game, name='Elara Venn', change_basis=basis(game))
    decision = 'Corrijo explícitamente el nombre: la fuente establece Elara Venn.'
    accepted = accept(game, draft, authorize=True, decision=decision)
    with game['engine'].db.session() as session:
        revision = session.get(Revision, accepted['revision']['id'])
        assert revision.before[original['id']]['name'] == 'Elara'
        assert revision.after[original['id']]['name'] == 'Elara Venn'
        assert revision.summary == decision
        assert revision.proposal_id == draft['id']
    game['engine'].revert(game['setup']['universe_id'], version(game), 'La corrección fue revisada y retirada por el usuario.')
    restored = records(game)[original['id']]
    assert restored['name'] == original['name']
    assert restored['data'] == original['data']
    assert len(snapshot(game)['revisions']) == accepted['version'] + 1


def test_review_cannot_rebase_character_change_basis_after_another_edit(game):
    old_revision = game['receptionist']['revision']
    pending = change(game, name='Elara Venn', change_basis=basis(game, based_on_revision=old_revision))
    accept(game, change(game, balance=5))
    assert records(game)[game['receptionist']['id']]['revision'] == old_revision + 1
    reviewed = game['engine'].review(pending['id'])
    assert reviewed['base_version'] == version(game)
    assert_blocked(game, reviewed)
