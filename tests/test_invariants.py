from copy import deepcopy
import pytest
from narrative.db import Database
from narrative.schemas import Acceptance, Advance, ProposalInput, RecordInput, TurnInput, Wizard
from narrative.service import DomainError, Engine


@pytest.fixture
def game(tmp_path):
    db = Database(tmp_path / "canon.sqlite3")
    engine = Engine(db)
    setup = engine.wizard(Wizard(universe_name="Universo ficticio de pruebas", campaign_name="Campaña ficticia", protagonist_name="Heroína ficticia"))
    yield engine, setup
    db.engine.dispose()


def state(e, s): return e.snapshot(s["universe_id"])
def records(e, s): return {r["id"]: r for r in state(e, s)["records"]}
def version(e, s): return state(e, s)["universe"]["version"]


def proposal(e, s, kind, data, **fields):
    name = fields.pop("name", "Registro ficticio")
    world_id = fields.pop("world_id", s["world_id"])
    campaign_id = fields.pop("campaign_id", None)
    return e.propose(s["universe_id"], ProposalInput(title=name, record=RecordInput(kind=kind, name=name, world_id=world_id, campaign_id=campaign_id, data=data), **fields))


def accept(e, s, pending):
    return e.accept(pending["id"], Acceptance(expected_version=version(e, s), decision="Validación humana explícita para esta prueba.", acknowledge_warnings=True))


def create(e, s, kind, data, **fields): return accept(e, s, proposal(e, s, kind, data, **fields))["record"]
def npc(e, s, **data): return create(e, s, "character", data, name="NPC ficticio")


def event(e, s, actions=None, **data):
    return proposal(e, s, "event", {"summary": "Acontecimiento ficticio", "certainty": "confirmed", "day": 0, "actions": actions or [], **data}, campaign_id=s["campaign_id"])


def blocked(e, s, pending, code=None):
    if code: assert code in {i["code"] for i in pending["issues"]}
    before, v = records(e, s), version(e, s)
    with pytest.raises(DomainError) as error: accept(e, s, pending)
    assert error.value.status == 422
    assert records(e, s) == before
    assert version(e, s) == v


def test_discovery_wizard_does_not_invent_history(game):
    e, s = game
    snapshot = state(e, s)
    assert sorted(r["kind"] for r in snapshot["records"]) == ["campaign", "character", "world"]
    current = records(e, s)
    assert current[s["world_id"]]["data"]["established"] is False
    assert current[s["protagonist_id"]]["data"]["age"] is None
    assert current[s["protagonist_id"]]["data"]["location_id"] is None
    assert len(snapshot["sources"]) == len(snapshot["revisions"]) == 1


def test_defined_wizard_preserves_user_declarations(game):
    e, _ = game
    s = e.wizard(Wizard(campaign_name="Definida", protagonist_name="Brisa", mode="defined", world_name="Mundo ficticio", world_rules="La magia consume agua.", personality="Paciente."))
    current = records(e, s)
    assert current[s["world_id"]]["data"]["established"] is True
    assert [r["data"]["statement"] for r in current.values() if r["kind"] == "rule"] == ["La magia consume agua."]
    assert not [r for r in current.values() if r["kind"] == "event"]


def test_universe_references_are_isolated(game):
    e, first = game
    second = e.wizard(Wizard(campaign_name="Separada", protagonist_name="Otro héroe"))
    blocked(e, first, proposal(e, first, "relationship", {"from_id": first["protagonist_id"], "to_id": second["protagonist_id"]}), "invalid_reference")
    with pytest.raises(DomainError):
        e.wizard(Wizard(universe_id=first["universe_id"], world_id=second["world_id"], campaign_name="Inválida", protagonist_name="X"))
    assert len(records(e, first)) == 3


def test_draft_review_and_rejection_do_not_change_material_state(game):
    e, s = game
    actor = npc(e, s, balance=10)
    before = records(e, s)
    pending = event(e, s, [{"type": "spend", "character_id": actor["id"], "amount": 4}])
    e.review(pending["id"])
    e.reject(pending["id"], "No sucedió.")
    assert records(e, s) == before


@pytest.mark.parametrize("certainty", ["rumor", "hypothesis", "interpretation", "refuted", "unknown", "contradiction"])
def test_acceptance_never_promotes_epistemic_status(game, certainty):
    e, s = game
    assert create(e, s, "fact", {"statement": "Quizá existe una isla.", "certainty": certainty})["data"]["certainty"] == certainty


def test_uncertain_event_cannot_spend_resources(game):
    e, s = game
    actor = npc(e, s, balance=10)
    blocked(e, s, event(e, s, [{"type": "spend", "character_id": actor["id"], "amount": 3}], certainty="rumor"), "uncertain_effects")


def test_narrator_secret_requires_explicit_discovery(game):
    e, s = game
    fact = create(e, s, "fact", {"statement": "Secreto del narrador.", "certainty": "confirmed", "visibility": "narrator"})
    assert fact["id"] not in {r["id"] for r in e.context(s["campaign_id"])["records"]}
    assert fact["id"] in {r["id"] for r in e.context(s["campaign_id"], "narrator")["records"]}
    blocked(e, s, event(e, s, [{"type": "mention", "character_id": s["protagonist_id"], "fact_id": fact["id"]}]), "unknown_secret")
    accept(e, s, event(e, s, [{"type": "reveal", "character_id": s["protagonist_id"], "fact_id": fact["id"], "learned_from": "Lee una carta autenticada."}]))
    assert fact["id"] in {r["id"] for r in e.context(s["campaign_id"])["records"]}


def test_private_state_does_not_leak(game):
    e, s = game
    observer = npc(e, s, psychology={"hidden_affection": "En secreto."})
    fact = create(e, s, "fact", {"statement": "Clave privada.", "certainty": "confirmed"})
    knowledge = create(e, s, "knowledge", {"character_id": observer["id"], "fact_id": fact["id"], "belief": "knows", "learned_from": "Carta privada."})
    visible = {r["id"] for r in e.context(s["campaign_id"])["records"]}
    assert not visible.intersection({observer["id"], fact["id"], knowledge["id"]})


def test_character_suspicion_does_not_reveal_objective_truth(game):
    e, s = game
    fact = create(e, s, "fact", {"statement": "La llave es falsa.", "certainty": "confirmed"})
    create(e, s, "knowledge", {"character_id": s["protagonist_id"], "fact_id": fact["id"], "belief": "suspects", "learned_from": "Una marca extraña."})
    visible = next(r for r in e.context(s["campaign_id"])["records"] if r["id"] == fact["id"])
    assert visible["data"]["certainty"] == "hypothesis"


def test_rumor_can_be_believed_but_not_known_as_confirmed(game):
    e, s = game
    fact = create(e, s, "fact", {"statement": "Se rumorea una isla.", "certainty": "rumor"})
    data = {"character_id": s["protagonist_id"], "fact_id": fact["id"], "belief": "knows", "learned_from": "Un vecino."}
    blocked(e, s, proposal(e, s, "knowledge", data), "belief_not_fact")
    create(e, s, "knowledge", {**data, "belief": "believes"})
    assert records(e, s)[fact["id"]]["data"]["certainty"] == "rumor"


def test_uninformed_person_cannot_reveal_secret(game):
    e, s = game
    observer = npc(e, s)
    fact = create(e, s, "fact", {"statement": "Clave privada.", "certainty": "confirmed"})
    blocked(e, s, event(e, s, [{"type": "reveal", "character_id": s["protagonist_id"], "from_id": observer["id"], "fact_id": fact["id"], "learned_from": "Lo cuenta."}]), "uninformed_source")


def test_protagonist_requires_exact_user_turn(game):
    e, s = game
    action = {"type": "protagonist_decision", "character_id": s["protagonist_id"], "decision": "Decido marcharme."}
    blocked(e, s, event(e, s, [action]), "player_agency")
    action["authorized_turn_id"] = e.add_turn(s["campaign_id"], TurnInput(role="user", content="Decido marcharme."))["id"]
    accept(e, s, event(e, s, [action]))
    blocked(e, s, event(e, s, [{**action, "decision": "Decido casarme."}]), "player_agency")


def test_unrelated_user_turn_does_not_authorize_spending(game):
    e, s = game
    hero = records(e, s)[s["protagonist_id"]]
    create(e, s, "character", {**hero["data"], "balance": 20}, name=hero['name'], operation="replace", target_id=hero["id"])
    turn = e.add_turn(s["campaign_id"], TurnInput(role="user", content="Observo el cielo."))
    blocked(e, s, event(e, s, [{"type": "spend", "character_id": hero["id"], "amount": 10, "authorized_turn_id": turn["id"], "decision": "Compro una espada."}]), "player_agency")


def test_spending_and_multi_action_failure_are_atomic(game):
    e, s = game
    actor = npc(e, s, balance=10)
    accept(e, s, event(e, s, [{"type": "spend", "character_id": actor["id"], "amount": 4}]))
    assert records(e, s)[actor["id"]]["data"]["balance"] == 6
    blocked(e, s, event(e, s, [{"type": "spend", "character_id": actor["id"], "amount": 3}, {"type": "spend", "character_id": actor["id"], "amount": 4}]), "insufficient_funds")


def test_transferred_object_cannot_be_transferred_twice(game):
    e, s = game
    first, second = npc(e, s), npc(e, s)
    item = create(e, s, "item", {"owner_id": first["id"]})
    action = {"type": "transfer", "item_id": item["id"], "from_id": first["id"], "to_id": second["id"]}
    accept(e, s, event(e, s, [action]))
    assert records(e, s)[item["id"]]["data"]["owner_id"] == second["id"]
    blocked(e, s, event(e, s, [action]), "ownership")


def test_item_owner_must_be_in_item_world(game):
    e, s = game
    other = create(e, s, "world", {}, world_id=None)
    blocked(e, s, proposal(e, s, "item", {"owner_id": s["protagonist_id"]}, world_id=other["id"]), "owner_world")


def test_transfer_requires_compatible_known_locations(game):
    e, s = game
    one, two = create(e, s, "place", {}), create(e, s, "place", {})
    sender = npc(e, s, location_id=one["id"])
    recipient = npc(e, s, location_id=two["id"])
    item = create(e, s, "item", {"owner_id": sender["id"]})
    action = {"type": "transfer", "item_id": item["id"], "from_id": sender["id"], "to_id": recipient["id"]}
    blocked(e, s, event(e, s, [action]), "transfer_location")
    accept(e, s, event(e, s, [{"type": "move", "character_id": recipient["id"], "from_id": two["id"], "to_id": one["id"]}, action]))
    assert records(e, s)[item["id"]]["data"]["owner_id"] == recipient["id"]


def test_interaction_actors_must_be_in_event_world(game):
    e, s = game
    other = create(e, s, "world", {}, world_id=None)
    a = create(e, s, "character", {}, world_id=other["id"])
    b = create(e, s, "character", {}, world_id=other["id"])
    blocked(e, s, event(e, s, [{"type": "interaction", "from_id": a["id"], "to_id": b["id"]}]), "actor_world")


def test_action_actor_cannot_bypass_birth_check_by_omitting_participants(game):
    e, s = game
    actor = npc(e, s, born_day=0, balance=10)
    blocked(e, s, event(e, s, [{"type": "spend", "character_id": actor["id"], "amount": 1}], day=-1), "before_birth")


def test_movement_requires_actual_origin(game):
    e, s = game
    one, two = create(e, s, "place", {}), create(e, s, "place", {})
    actor = npc(e, s, location_id=one["id"])
    blocked(e, s, event(e, s, [{"type": "move", "character_id": actor["id"], "from_id": two["id"], "to_id": one["id"]}]), "location_mismatch")
    accept(e, s, event(e, s, [{"type": "move", "character_id": actor["id"], "from_id": one["id"], "to_id": two["id"]}]))
    assert records(e, s)[actor["id"]]["data"]["location_id"] == two["id"]


def test_local_causality_does_not_assume_universal_calendar(game):
    e, s = game
    cause = accept(e, s, event(e, s, day=10))["record"]
    blocked(e, s, event(e, s, day=9, predecessors=[cause["id"]]), "causal_order")
    other = create(e, s, "world", {}, world_id=None)
    distant = create(e, s, "event", {"summary": "Otro calendario.", "certainty": "confirmed", "day": 1000}, world_id=other["id"])
    assert accept(e, s, event(e, s, day=1, predecessors=[distant["id"]]))["record"]["data"]["day"] == 1


def test_causal_cycles_are_rejected(game):
    e, s = game
    first = accept(e, s, event(e, s))["record"]
    second = accept(e, s, event(e, s, predecessors=[first["id"]]))["record"]
    blocked(e, s, proposal(e, s, "event", {**first["data"], "predecessors": [second["id"]]}, operation="replace", target_id=first["id"]), "causal_cycle")


def test_character_cannot_participate_before_birth(game):
    e, s = game
    actor = npc(e, s, born_day=0)
    blocked(e, s, event(e, s, day=-1, participants=[actor["id"]]), "before_birth")


def test_object_cannot_be_used_before_manufacture(game):
    e, s = game
    actor = npc(e, s)
    item = create(e, s, "item", {"owner_id": actor["id"], "created_day": 0})
    blocked(e, s, event(e, s, [{"type": "use_item", "character_id": actor["id"], "item_id": item["id"], "claimed_days": 100}], day=3), "item_age")


def test_relationships_are_directed_and_persistent(game):
    e, s = game
    a, b = npc(e, s), npc(e, s)
    ab = create(e, s, "relationship", {"from_id": a["id"], "to_id": b["id"], "dimensions": {"trust": "Alta", "attraction": "Reservada"}, "explanation": "Historia."})
    ba = create(e, s, "relationship", {"from_id": b["id"], "to_id": a["id"], "dimensions": {"trust": "Baja"}, "explanation": "Otra historia."})
    create(e, s, "relationship", {**ab["data"], "dimensions": {"trust": "Media", "attraction": "Reservada"}, "explanation": "Discusión."}, operation="replace", target_id=ab["id"])
    assert records(e, s)[ba["id"]]["data"]["dimensions"] == {"trust": "Baja"}
    accept(e, s, e.advance(s["universe_id"], Advance(world_id=s["world_id"], days=14)))
    assert records(e, s)[ab["id"]]["data"]["dimensions"]["attraction"] == "Reservada"
    blocked(e, s, event(e, s, [{"type": "interaction", "from_id": a["id"], "to_id": b["id"], "as_strangers": True}], day=14), "forgotten_relationship")


def test_time_does_not_erase_emotions_or_injuries(game):
    e, s = game
    actor = npc(e, s, psychology={"affection": "Reservado"}, routine="Trabaja en su taller.", injuries=["Herida abierta"])
    blocked(e, s, event(e, s, [{"type": "emotion", "character_id": actor["id"], "changes": {"affection": "Indiferente"}}]), "emotion_cause")
    pending = e.advance(s["universe_id"], Advance(world_id=s["world_id"], days=14))
    assert records(e, s)[s["world_id"]]["data"]["current_day"] == 0
    accepted = accept(e, s, pending)["record"]
    assert accepted["data"]["actions"] == []
    assert accepted["data"]["simulation"]["intervals"][0]["development"] == "routine"
    current = records(e, s)[actor["id"]]["data"]
    assert current["psychology"] == {"affection": "Reservado"}
    assert current["injuries"] == ["Herida abierta"]


def test_simulation_defers_goals_and_excludes_protagonist(game):
    e, s = game
    goal, unknown = npc(e, s, goals=["Viajar."]), npc(e, s)
    pending = e.advance(s["universe_id"], Advance(world_id=s["world_id"], days=7, relevant_character_ids=[goal["id"], unknown["id"], s["protagonist_id"]]))
    intervals = {i["character_id"]: i for i in pending["record"]["data"]["simulation"]["intervals"]}
    assert intervals[goal["id"]]["development"] == "deferred"
    assert intervals[unknown["id"]]["development"] == "undeveloped"
    assert s["protagonist_id"] not in intervals


def test_stale_proposal_requires_fresh_review(game):
    e, s = game
    pending = proposal(e, s, "fact", {"statement": "Antiguo."})
    create(e, s, "fact", {"statement": "Nuevo."})
    with pytest.raises(DomainError) as error: accept(e, s, pending)
    assert error.value.status == 409
    e.review(pending["id"])
    assert accept(e, s, pending)["record"]["data"]["statement"] == "Antiguo."


def test_concurrent_acceptances_cannot_overwrite_same_canon_version(game):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    e, s = game
    drafts = [proposal(e, s, "fact", {"statement": name}) for name in ["Primero.", "Segundo."]]
    expected = version(e, s)
    barrier = Barrier(2)

    def attempt(draft):
        barrier.wait(timeout=10)
        try:
            return e.accept(draft["id"], Acceptance(expected_version=expected, decision="Revisión concurrente explícita.", acknowledge_warnings=True))["version"]
        except DomainError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, drafts))
    assert sorted(outcomes) == sorted([expected + 1, 409])
    snapshot = state(e, s)
    assert snapshot["universe"]["version"] == expected + 1
    assert len([r for r in snapshot["records"] if r["kind"] == "fact"]) == 1
    assert sorted(p["status"] for p in snapshot["proposals"]) == ["accepted", "pending"]


def test_reversion_restores_all_effects_and_preserves_audit(game):
    e, s = game
    a, b = npc(e, s, balance=20), npc(e, s)
    item = create(e, s, "item", {"owner_id": a["id"]})
    before = records(e, s)
    accepted = accept(e, s, event(e, s, [{"type": "spend", "character_id": a["id"], "amount": 7}, {"type": "transfer", "from_id": a["id"], "to_id": b["id"], "item_id": item["id"]}, {"type": "injury", "character_id": a["id"], "injury": "Corte."}], day=2))
    previous = version(e, s)
    e.revert(s["universe_id"], previous, "Interpretación equivocada.")
    after = records(e, s)
    assert accepted["record"]["id"] not in after
    assert set(after) == set(before)
    for identity in before: assert after[identity]["data"] == before[identity]["data"]
    assert version(e, s) == previous + 1
    with pytest.raises(DomainError): e.revert(s["universe_id"], previous, "Obsoleta.")


def test_world_rules_and_capabilities_are_enforced(game):
    e, s = game
    actor = npc(e, s, capabilities=["teleport"])
    create(e, s, "rule", {"statement": "No teleportar.", "forbidden_abilities": ["teleport"]})
    blocked(e, s, event(e, s, [{"type": "ability", "character_id": actor["id"], "ability": "teleport"}]), "world_rule")
    blocked(e, s, event(e, s, [{"type": "ability", "character_id": actor["id"], "ability": "resurrect"}]), "unestablished_ability")


def test_protagonist_switch_keeps_history_and_knowledge_boundaries(game):
    e, s = game
    original = s["protagonist_id"]
    turn = e.add_turn(s["campaign_id"], TurnInput(role="user", content="Mi primera decisión."))
    hero = npc(e, s, psychology={"fear": "Conservado"})
    fact = create(e, s, "fact", {"statement": "Recuerdo personal.", "certainty": "confirmed", "known_by": [original]})
    e.switch_protagonist(s["campaign_id"], hero["id"], version(e, s), "Otra perspectiva.")
    saved = e.turns(s["campaign_id"])
    assert saved["turns"][0]["id"] == turn["id"]
    assert saved["turns"][0]["protagonist_id"] == original
    assert saved["campaign"]["data"]["protagonist_id"] == hero["id"]
    assert original in records(e, s)
    assert fact["id"] not in {r["id"] for r in e.context(s["campaign_id"])["records"]}


def test_reopening_preserves_canon_sources_drafts_and_turns(game):
    e, s = game
    create(e, s, "fact", {"statement": "Persistente.", "certainty": "confirmed"})
    proposal(e, s, "fact", {"statement": "Pendiente.", "certainty": "hypothesis"})
    e.add_turn(s["campaign_id"], TurnInput(role="user", content="Continúo mañana."))
    before = deepcopy(state(e, s))
    e.db.engine.dispose()
    db = Database(e.db.path)
    reopened = Engine(db)
    try:
        assert state(reopened, s) == before
        assert reopened.turns(s["campaign_id"])["turns"][0]["content"] == "Continúo mañana."
    finally:
        db.engine.dispose()
