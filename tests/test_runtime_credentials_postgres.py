"""Opt-in test: fake session only, no model calls or canonical changes.

Uses UMBRAL_TEST_DATABASE_URL privately. Database() applies pending Alembic
migrations. Prefer a dedicated test database: the temporary portable backup
contains its canonical data. No real tokens are needed. Cleanup deletes only
this test's unique qa_ session name.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from narrative.db import Database, RuntimeSecret, Universe, Source, Record, Proposal, Revision, Turn
from narrative.runtime_credentials import RuntimeCredentialStore


def canon_digest(db):
    rows = {}
    with db.session() as session:
        for model in (Universe, Source, Record, Proposal, Revision, Turn):
            rows[model.__tablename__] = sorted(
                (dict(row) for row in session.execute(select(model.__table__)).mappings()),
                key=lambda row: row['id'])
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@pytest.mark.skipif(not os.getenv('UMBRAL_TEST_DATABASE_URL'), reason='Requires private PostgreSQL test configuration')
def test_postgres_session_refresh_serialization_privacy_and_canon_isolation(tmp_path, monkeypatch):
    url = os.environ['UMBRAL_TEST_DATABASE_URL']
    db = Database(url)
    assert db.is_postgres
    baseline = canon_digest(db)
    name = 'qa_' + uuid4().hex
    secret = b'fictional-runtime-key-only-' + b'x' * 32
    databases = [db]
    try:
        with db.session() as session:
            assert session.scalar(text("SELECT relrowsecurity FROM pg_class WHERE oid = 'narrative_core.runtime_secrets'::regclass")) is True
            roles = set(session.scalars(text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')")))
            for role in roles:
                assert session.scalar(text("SELECT has_table_privilege(:role, 'narrative_core.runtime_secrets', 'SELECT,INSERT,UPDATE,DELETE')"), {'role': role}) is False
        stores = [RuntimeCredentialStore(db, secret, name)]
        with stores[0].lease() as lease:
            host = lease.read()['host_id']
        for _ in range(2):
            other = Database(url)
            databases.append(other)
            stores.append(RuntimeCredentialStore(other, secret, name))
        def update(index):
            with stores[index % 3].lease() as lease:
                profile = lease.read()
                assert profile['host_id'] == host
                profile['refresh_token'] = 'FICTITIOUS-ROTATION-' + str(profile.get('counter', 0) + 1)
                profile['counter'] = profile.get('counter', 0) + 1
                lease.save(profile)
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(update, range(6)))
        for database in databases:
            database.engine.dispose()
        with RuntimeCredentialStore(db, secret, name).lease() as lease:
            assert lease.read()['counter'] == 6
            assert lease.read()['refresh_token'] == 'FICTITIOUS-ROTATION-6'
        from narrative.self_hosted_plan import SelfHostedPlanNarrator
        from narrative.chatgpt_plan import AUTH, RESOURCE
        import httpx
        store = RuntimeCredentialStore(db, secret, name)
        with store.lease() as lease:
            lease.save(dict(lease.read(), client_id='oaiapp_fictitious', access_token='FICTITIOUS-ACCESS',
                scopes=['resource.invoke','chatgpt.tokens.use.direct'], expires=0, model='small-test'))
            with db.engine.begin() as other_connection:
                assert other_connection.scalar(text('SELECT pg_try_advisory_xact_lock(1160396)')) is True
        original_client = httpx.Client
        catalogue_available = False
        refreshes = []
        def handler(request):
            if str(request.url) == AUTH + '/api/accounts/oauth/token':
                refreshes.append(True)
                return httpx.Response(200, json=dict(access_token='FICTITIOUS-NEW-ACCESS',
                    refresh_token='FICTITIOUS-NEW-REFRESH', expires_in=3600,
                    scope='resource.invoke chatgpt.tokens.use.direct'))
            assert str(request.url) == RESOURCE + '/models'
            assert request.headers['Authorization'] == 'Bearer FICTITIOUS-NEW-ACCESS'
            return httpx.Response(200, json={'models':[{'slug':'small-test','visibility':'list'}]}) if catalogue_available else httpx.Response(503)
        monkeypatch.setattr('narrative.chatgpt_plan.httpx.Client',
            lambda **kw: original_client(transport=httpx.MockTransport(handler), **kw))
        narrator = SelfHostedPlanNarrator(store, 'https://fictitious.example')
        with pytest.raises(httpx.HTTPStatusError):
            narrator.models()
        with store.lease() as lease:
            assert lease.read()['refresh_token'] == 'FICTITIOUS-NEW-REFRESH'
        catalogue_available = True
        assert narrator.models()[0]['id'] == 'small-test'
        assert narrator.status()['connected'] is True
        assert len(refreshes) == 1
        with db.session() as session:
            ciphertext = session.get(RuntimeSecret, name).sealed
            assert 'FICTITIOUS-' not in ciphertext
        backup = db.backup(tmp_path / 'portable-without-runtime.db')
        restored = Database(backup)
        try:
            with restored.session() as session:
                assert not list(session.scalars(select(RuntimeSecret)))
            assert ciphertext.encode() not in backup.read_bytes()
        finally:
            restored.engine.dispose()
        assert canon_digest(db) == baseline
    finally:
        with db.session(True) as session:
            session.execute(delete(RuntimeSecret).where(RuntimeSecret.name == name))
        for database in databases:
            database.engine.dispose()
