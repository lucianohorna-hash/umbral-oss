"""Fictional regressions for characterization changes through event effects."""
from copy import deepcopy
import json

import pytest

from narrative.db import Database
from narrative.schemas import Acceptance, ProposalInput, RecordInput, Wizard
from narrative.service import DomainError, Engine


@pytest.fixture
def game(tmp_path):
    database = Database(tmp_path / 'npc-effects.sqlite3')
    engine = Engine(database)
    setup = engine.wizard(Wizard(universe_name='Auditoría ficticia de efectos',
                                 campaign_name='Una continuidad ficticia',
                                 protagonist_name='Lua', personality='Reservada'))
    game = {'engine': engine, 'setup': setup}
    game['npc'] = accept(game, propose(game, 'character', 'Elara', {
        'born_day': -30,
        'personality': {'temperamento': 'Altanera'},
        'psychology': {'trauma': 'Miedo persistente a los incendios',
                       'confianza': 'Reservada'},
    }))['record']
    yield game
    game['engine'].db.engine.dispose()
    database.engine.dispose()


def snapshot(game):
    return game['engine'].snapshot(game['setup']['universe_id'])


def current_npc(game):
    return next(row for row in snapshot(game)['records'] if row['id'] == game['npc']['id'])


def propose(game, kind, name, data, *, target=None):
    setup = game['setup']
    return game['engine'].propose(setup['universe_id'], ProposalInput(
        title=name, operation='replace' if target else 'create', target_id=target,
        record=RecordInput(kind=kind, name=name, world_id=setup['world_id'],
                           campaign_id=setup['campaign_id'] if kind == 'event' else None,
                           data=data)))


def accept(game, draft, *, authorize=False):
    return game['engine'].accept(draft['id'], Acceptance(
        expected_version=snapshot(game)['universe']['version'],
        decision='El usuario revisa expresamente este escenario de prueba ficticio.',
        acknowledge_warnings=True, authorize_character_change=authorize))


def blocked(game, draft, *, authorize=True):
    before = deepcopy(snapshot(game))
    with pytest.raises(DomainError) as failure:
        accept(game, draft, authorize=authorize)
    assert failure.value.status == 422
    after = snapshot(game)
    assert after['records'] == before['records']
    assert after['revisions'] == before['revisions']
    assert after['universe']['version'] == before['universe']['version']


def evidence(game, *, certainty='confirmed', involved=True):
    return accept(game, propose(game, 'event', 'Una experiencia ficticia de Elara', {
        'summary': 'Elara afronta un incendio acompañada y empieza a recuperar seguridad.',
        'certainty': certainty, 'day': 0,
        'participants': [game['npc']['id'] if involved else game['setup']['protagonist_id']],
    }))['record']


def emotion(game, *, change_basis=None, changes=None):
    action = {'type': 'emotion', 'character_id': game['npc']['id'],
              'reason': 'La experiencia citada justifica el cambio propuesto.',
              'changes': {'trauma': 'El miedo persiste, pero ahora acepta ayuda.'}
                         if changes is None else changes}
    if change_basis is not None:
        action['change_basis'] = change_basis
    return propose(game, 'event', 'Evolución psicológica ficticia', {
        'summary': 'Se propone registrar el aprendizaje de Elara.',
        'certainty': 'confirmed', 'day': 0, 'actions': [action],
    })


def evolution_basis(game, causes):
    return {'mode': 'evolution', 'based_on_revision': current_npc(game)['revision'],
            'reason': 'Cambio gradual motivado por la experiencia documentada.',
            'evidence_ids': [row['id'] for row in causes]}


def test_emotion_reason_alone_cannot_erase_established_trauma(game):
    draft = emotion(game, changes={'trauma': None})
    blocked(game, draft)
    assert current_npc(game)['data']['psychology']['trauma'] == 'Miedo persistente a los incendios'


@pytest.mark.parametrize('certainty,involved', [
    ('rumor', True), ('confirmed', False),
], ids=['unconfirmed_cause', 'cause_does_not_involve_npc'])
def test_emotional_evolution_rejects_unconfirmed_or_unrelated_evidence(game, certainty, involved):
    cause = evidence(game, certainty=certainty, involved=involved)
    blocked(game, emotion(game, change_basis=evolution_basis(game, [cause])))


def test_emotional_evolution_with_no_evidence_cannot_replace_established_state(game):
    blocked(game, emotion(game, change_basis=evolution_basis(game, [])))


def test_emotional_evolution_requires_specific_authorization_and_survives_restart(game):
    original = deepcopy(current_npc(game))
    cause = evidence(game)
    basis = evolution_basis(game, [cause])
    draft = emotion(game, change_basis=basis)
    assert any(issue['code'] == 'protected_character_change' for issue in draft['issues'])
    blocked(game, draft, authorize=False)
    accepted = accept(game, draft, authorize=True)
    path = game['engine'].db.path
    game['engine'].db.engine.dispose()
    game['engine'] = Engine(Database(path))
    npc = current_npc(game)
    assert npc['name'] == original['name']
    assert npc['data']['personality'] == original['data']['personality']
    assert npc['data']['psychology'] == {
        **original['data']['psychology'],
        'trauma': 'El miedo persiste, pero ahora acepta ayuda.',
    }
    event = next(row for row in snapshot(game)['records'] if row['id'] == accepted['record']['id'])
    assert event['data']['actions'][0]['change_basis'] == basis
    assert snapshot(game)['revisions'][0]['proposal_id'] == draft['id']


def test_emotional_evolution_cannot_reuse_basis_after_another_character_change(game):
    cause = evidence(game)
    draft = emotion(game, change_basis=evolution_basis(game, [cause]))
    npc = current_npc(game)
    accept(game, propose(game, 'character', npc['name'],
                         {**deepcopy(npc['data']), 'balance': 5}, target=npc['id']))
    reviewed = game['engine'].review(draft['id'])
    assert reviewed['base_version'] == snapshot(game)['universe']['version']
    blocked(game, reviewed)


def test_confirmed_scene_cast_can_establish_involvement_in_evolution(game):
    npc = current_npc(game)
    cause = accept(game, propose(game, 'event', 'Experiencia con reparto explícito', {
        'summary': 'Elara acepta ayuda para afrontar su miedo en este escenario ficticio.',
        'certainty': 'confirmed', 'day': 0,
        'cast': [{'character_id': npc['id'], 'name': npc['name']}],
    }))['record']
    draft = emotion(game, change_basis=evolution_basis(game, [cause]))
    blocked(game, draft, authorize=False)
    accept(game, draft, authorize=True)
    assert current_npc(game)['data']['psychology']['trauma'] == 'El miedo persiste, pero ahora acepta ayuda.'


@pytest.mark.parametrize('case', ['other_world', 'before_birth'])
def test_cast_only_cannot_place_npc_in_an_incompatible_world_or_before_birth(game, case):
    identity, name, day = game['npc']['id'], game['npc']['name'], 0
    if case == 'other_world':
        elsewhere = game['engine'].wizard(Wizard(
            universe_id=game['setup']['universe_id'], campaign_name='Otro calendario ficticio',
            world_name='Otro mundo ficticio', protagonist_name='Sira'))
        identity, name = elsewhere['protagonist_id'], 'Sira'
    else:
        day = -31
    draft = propose(game, 'event', 'Reparto incoherente', {
        'summary': 'Un personaje se presenta en la escena.', 'certainty': 'confirmed',
        'day': day, 'cast': [{'character_id': identity, 'name': name}],
    })
    # The cast must be checked even when participants/actions are empty.
    assert draft['record']['data']['participants'] == []
    blocked(game, draft)


def test_character_context_omits_private_character_change_justification(game):
    setup = game['setup']
    secret = accept(game, propose(game, 'fact', 'Causa reservada ficticia', {
        'statement': 'La reina inmortal intervino en secreto.', 'certainty': 'confirmed',
        'visibility': 'narrator',
    }))['record']
    hero = next(row for row in snapshot(game)['records'] if row['id'] == setup['protagonist_id'])
    private_reason = 'La reina inmortal impuso una identidad que Lua todavía desconoce.'
    draft = propose(game, 'character', 'Lua Venn', {
        **deepcopy(hero['data']),
        'change_basis': {'mode': 'correction', 'reason': private_reason,
                         'based_on_revision': hero['revision'], 'evidence_ids': [secret['id']]},
    }, target=hero['id'])
    accept(game, draft, authorize=True)
    visible = game['engine'].context(setup['campaign_id'], 'character')
    personal = next(row for row in visible['records'] if row['id'] == hero['id'])
    assert 'change_basis' not in personal['data']
    assert private_reason not in json.dumps(visible, ensure_ascii=False)
    assert secret['id'] not in json.dumps(visible, ensure_ascii=False)
    narrator = game['engine'].context(setup['campaign_id'], 'narrator')
    narrator_hero = next(row for row in narrator['records'] if row['id'] == hero['id'])
    assert narrator_hero['data']['change_basis']['reason'] == private_reason
    assert narrator_hero['data']['change_basis']['evidence_ids'] == [secret['id']]
