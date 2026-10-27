from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from narrative.api import create_app
from narrative.db import Proposal, Source, Turn


def make_client(path):
    client = TestClient(create_app(path))
    client.headers["X-CSRF-Token"] = client.get("/api/bootstrap").json()["csrf_token"]
    return client


def make_campaign(client, name="Reintentos"):
    response = client.post("/api/wizard", json={
        "universe_name": "Universo de prueba",
        "campaign_name": name,
        "protagonist_name": "Ada",
        "mode": "discover",
    })
    assert response.status_code == 200, response.text
    return response.json()


def database_counts(client, universe_id):
    engine = client.app.state.engine
    with engine.db.session() as session:
        turns = session.scalars(select(Turn).where(Turn.universe_id == universe_id)).all()
        sources = session.scalars(select(Source).where(Source.universe_id == universe_id)).all()
        proposals = session.scalars(select(Proposal).where(Proposal.universe_id == universe_id)).all()
    return turns, sources, proposals


def count_snapshot(counts):
    return tuple(len(rows) for rows in counts)


def advance_canon(client, universe_id, world_id):
    proposal = client.post(f"/api/universes/{universe_id}/proposals", json={
        "title": "Regla de prueba",
        "record": {
            "kind": "rule",
            "name": "Regla de prueba",
            "world_id": world_id,
            "data": {"statement": "El cielo conserva su color.", "certainty": "confirmed"},
        },
    })
    assert proposal.status_code == 200, proposal.text
    current_version = client.get(f"/api/universes/{universe_id}").json()["universe"]["version"]
    accepted = client.post(f"/api/proposals/{proposal.json()['id']}/accept", json={
        "expected_version": current_version,
        "decision": "Añadida para probar la repetición.",
        "acknowledge_warnings": True,
    })
    assert accepted.status_code == 200, accepted.text


def test_replayed_narrator_turn_returns_original_after_restart_and_canon_advance(tmp_path):
    path = tmp_path / "retry.sqlite3"
    client = make_client(path)
    setup = make_campaign(client)
    before_turn = count_snapshot(database_counts(client, setup["universe_id"]))
    route = f"/api/campaigns/{setup['campaign_id']}/turns"
    request_id = str(uuid4())
    payload = {
        "role": "narrator",
        "content": "La puerta queda entreabierta.",
        "cast": [],
        "request_id": request_id,
        "expected_version": 1,
    }

    original = client.post(route, json=payload)
    assert original.status_code == 200, original.text
    original_turn = original.json()
    assert original_turn["proposal_id"]
    after_original = count_snapshot(database_counts(client, setup["universe_id"]))
    assert after_original[0] == 1
    assert after_original[1] == before_turn[1] + 1  # Una fuente nueva para la intervención del narrador.
    assert after_original[2] == 1
    advance_canon(client, setup["universe_id"], setup["world_id"])
    after_canon_advance = count_snapshot(database_counts(client, setup["universe_id"]))
    assert client.get(f"/api/universes/{setup['universe_id']}").json()["universe"]["version"] > original_turn["canon_version"]
    client.close()

    reopened = make_client(path)
    replay = reopened.post(route, json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json() == original_turn
    turns, sources, proposals = database_counts(reopened, setup["universe_id"])
    assert len([turn for turn in turns if turn.campaign_id == setup["campaign_id"]]) == 1
    assert count_snapshot((turns, sources, proposals)) == after_canon_advance
    assert after_canon_advance[1] == after_original[1] + 1  # Solo la fuente del cambio de canon.
    assert after_original[1] == before_turn[1] + 1
    assert after_canon_advance[2] == after_original[2] + 1
    assert original_turn["canon_version"] == 1


def test_concurrent_retries_create_one_turn_source_and_proposal(tmp_path):
    client = make_client(tmp_path / "parallel.sqlite3")
    setup = make_campaign(client)
    before = count_snapshot(database_counts(client, setup["universe_id"]))
    route = f"/api/campaigns/{setup['campaign_id']}/turns"
    payload = {
        "role": "narrator",
        "content": "Una figura espera bajo el arco.",
        "cast": [],
        "request_id": str(uuid4()),
        "expected_version": 1,
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(route, json=payload), range(2)))

    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    turns, sources, proposals = database_counts(client, setup["universe_id"])
    after = count_snapshot((turns, sources, proposals))
    assert after == (before[0] + 1, before[1] + 1, before[2] + 1)


def test_reusing_request_id_with_changed_input_conflicts_and_stale_first_write_is_rejected(tmp_path):
    client = make_client(tmp_path / "conflicts.sqlite3")
    setup = make_campaign(client)
    route = f"/api/campaigns/{setup['campaign_id']}/turns"
    request_id = str(uuid4())
    base = {
        "role": "narrator",
        "content": "El reloj se detiene.",
        "cast": [],
        "request_id": request_id,
        "expected_version": 1,
    }
    first = client.post(route, json=base)
    assert first.status_code == 200, first.text
    after_first = count_snapshot(database_counts(client, setup["universe_id"]))

    variants = [
        {**base, "content": "El reloj sigue andando."},
        {**base, "role": "user"},
        {**base, "cast": [{"character_id": setup["protagonist_id"], "name": "Ada"}]},
        {**base, "expected_version": 2},
    ]
    for variant in variants:
        response = client.post(route, json=variant)
        assert response.status_code == 409, response.text

    stale = client.post(route, json={
        "role": "user",
        "content": "Esta solicitud llega tarde.",
        "request_id": str(uuid4()),
        "expected_version": 0,
    })
    assert stale.status_code == 409, stale.text
    turns, sources, proposals = database_counts(client, setup["universe_id"])
    assert count_snapshot((turns, sources, proposals)) == after_first


def test_new_request_ids_and_other_campaign_are_independent_and_legacy_input_still_works(tmp_path):
    client = make_client(tmp_path / "independent.sqlite3")
    first_setup = make_campaign(client, "Primera")
    second_setup_response = client.post("/api/wizard", json={
        "universe_id": first_setup["universe_id"],
        "world_id": first_setup["world_id"],
        "protagonist_id": first_setup["protagonist_id"],
        "campaign_name": "Segunda",
        "protagonist_name": "Ada",
        "mode": "discover",
    })
    assert second_setup_response.status_code == 200, second_setup_response.text
    second_setup = second_setup_response.json()
    before = count_snapshot(database_counts(client, first_setup["universe_id"]))
    content = "Decido esperar junto a la ventana."
    shared_request_id = str(uuid4())

    first = client.post(f"/api/campaigns/{first_setup['campaign_id']}/turns", json={
        "role": "user", "content": content, "request_id": shared_request_id,
    })
    second = client.post(f"/api/campaigns/{second_setup['campaign_id']}/turns", json={
        "role": "user", "content": content, "request_id": shared_request_id,
    })
    distinct = client.post(f"/api/campaigns/{first_setup['campaign_id']}/turns", json={
        "role": "user", "content": content, "request_id": str(uuid4()),
    })
    legacy = client.post(f"/api/campaigns/{first_setup['campaign_id']}/turns", json={
        "role": "user", "content": "Una petición antigua sin identificador.",
    })
    assert all(response.status_code == 200 for response in [first, second, distinct, legacy])
    assert len({first.json()["id"], second.json()["id"], distinct.json()["id"], legacy.json()["id"]}) == 4
    turns, sources, proposals = database_counts(client, first_setup["universe_id"])
    assert len(turns) == 4
    assert count_snapshot((turns, sources, proposals)) == (before[0] + 4, before[1], before[2])


def test_user_cast_metadata_is_rejected_even_with_request_id(tmp_path):
    client = make_client(tmp_path / "user-cast.sqlite3")
    setup = make_campaign(client)
    response = client.post(f"/api/campaigns/{setup['campaign_id']}/turns", json={
        "role": "user",
        "content": "Decido observar.",
        "cast": [{"character_id": setup["protagonist_id"], "name": "Ada"}],
        "request_id": str(uuid4()),
    })
    assert response.status_code == 422
    assert client.get(f"/api/campaigns/{setup['campaign_id']}/turns").json()["turns"] == []


def test_user_retry_after_protagonist_switch_returns_original_hero_without_new_turn(tmp_path):
    client = make_client(tmp_path / "switch-retry.sqlite3")
    setup = make_campaign(client, "Perspectiva original")
    route = f"/api/campaigns/{setup['campaign_id']}/turns"
    payload = {
        "role": "user",
        "content": "Decido escuchar antes de entrar.",
        "request_id": str(uuid4()),
        "expected_version": 1,
    }
    original = client.post(route, json=payload)
    assert original.status_code == 200, original.text
    original_turn = original.json()

    other_campaign = client.post("/api/wizard", json={
        "universe_id": setup["universe_id"],
        "world_id": setup["world_id"],
        "campaign_name": "Perspectiva secundaria",
        "protagonist_name": "Brisa",
        "mode": "discover",
    })
    assert other_campaign.status_code == 200, other_campaign.text
    new_hero_id = other_campaign.json()["protagonist_id"]
    current_version = client.get(f"/api/universes/{setup['universe_id']}").json()["universe"]["version"]
    switched = client.post(f"/api/campaigns/{setup['campaign_id']}/protagonist", json={
        "character_id": new_hero_id,
        "expected_version": current_version,
        "reason": "Cambio de perspectiva autorizado para la prueba.",
    })
    assert switched.status_code == 200, switched.text

    replay = client.post(route, json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json() == original_turn
    assert replay.json()["protagonist_id"] == setup["protagonist_id"]
    turns, _, _ = database_counts(client, setup["universe_id"])
    assert len([turn for turn in turns if turn.campaign_id == setup["campaign_id"]]) == 1


def test_narrator_cast_retry_after_character_name_correction_returns_original_proposal(tmp_path):
    client = make_client(tmp_path / "cast-correction.sqlite3")
    setup = make_campaign(client)
    before_turn = count_snapshot(database_counts(client, setup["universe_id"]))
    route = f"/api/campaigns/{setup['campaign_id']}/turns"
    payload = {
        "role": "narrator",
        "content": "Ada cruza el patio en silencio.",
        "cast": [{"character_id": setup["protagonist_id"], "name": "Ada"}],
        "request_id": str(uuid4()),
        "expected_version": 1,
    }
    original = client.post(route, json=payload)
    assert original.status_code == 200, original.text
    original_turn = original.json()
    original_proposal = original_turn["proposal_id"]
    after_turn = count_snapshot(database_counts(client, setup["universe_id"]))

    snapshot = client.get(f"/api/universes/{setup['universe_id']}").json()
    hero = next(record for record in snapshot["records"] if record["id"] == setup["protagonist_id"])
    changed_data = {
        **hero["data"],
        "change_basis": {
            "mode": "correction",
            "reason": "Corrijo el nombre para que coincida con la identidad establecida.",
            "based_on_revision": hero["revision"],
            "evidence_ids": [],
        },
    }
    correction = client.post(f"/api/universes/{setup['universe_id']}/proposals", json={
        "title": "Corrección del nombre del personaje",
        "operation": "replace",
        "target_id": hero["id"],
        "expected_version": snapshot["universe"]["version"],
        "record": {
            "kind": "character",
            "name": "Ada Venn",
            "world_id": hero["world_id"],
            "data": changed_data,
        },
    })
    assert correction.status_code == 200, correction.text
    correction_acceptance = client.post(f"/api/proposals/{correction.json()['id']}/accept", json={
        "expected_version": snapshot["universe"]["version"],
        "decision": "Corrijo el nombre establecido a Ada Venn.",
        "acknowledge_warnings": True,
        "authorize_character_change": True,
    })
    assert correction_acceptance.status_code == 200, correction_acceptance.text
    after_correction = count_snapshot(database_counts(client, setup["universe_id"]))

    replay = client.post(route, json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json() == original_turn
    assert replay.json()["proposal_id"] == original_proposal
    turns, sources, proposals = database_counts(client, setup["universe_id"])
    assert count_snapshot((turns, sources, proposals)) == after_correction
    assert after_correction == (after_turn[0], after_turn[1] + 1, after_turn[2] + 1)
    assert after_turn[1] == before_turn[1] + 1  # Una fuente narrada; el reintento no crea otra.
