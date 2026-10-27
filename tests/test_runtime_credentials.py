from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest
from sqlalchemy import select
from narrative.db import Database, RuntimeSecret
from narrative.runtime_credentials import RuntimeCredentialStore
from narrative.service import Engine
from narrative.schemas import Wizard


SECRET = b'fictional-test-key-only-' + b'x' * 32


def test_encrypted_session_survives_restart_without_becoming_canon(tmp_path):
    path = tmp_path / 'runtime.db'
    db = Database(path)
    engine = Engine(db)
    setup = engine.wizard(Wizard(universe_name='Ficticio', campaign_name='Prueba',
        protagonist_name='Ada', mode='discover'))
    before = engine.snapshot(setup['universe_id'])
    store = RuntimeCredentialStore(db, SECRET)
    with store.lease() as lease:
        profile = lease.read()
        host = profile['host_id']
        profile.update(refresh_token='PRIVATE-REFRESH-MARKER', access_token='PRIVATE-ACCESS-MARKER')
        lease.save(profile)
    with pytest.raises(ValueError):
        lease.read()
    db.engine.dispose()
    reopened = Database(path)
    with RuntimeCredentialStore(reopened, SECRET).lease() as lease:
        profile = lease.read()
        assert profile['host_id'] == host
        assert profile['refresh_token'] == 'PRIVATE-REFRESH-MARKER'
    with reopened.session() as session:
        row = session.get(RuntimeSecret, 'chatgpt-personal')
        assert 'PRIVATE-' not in row.sealed
    assert Engine(reopened).snapshot(setup['universe_id']) == before


def test_wrong_key_and_corrupt_ciphertext_fail_without_overwriting(tmp_path):
    db = Database(tmp_path / 'runtime.db')
    store = RuntimeCredentialStore(db, SECRET)
    with store.lease() as lease:
        host = lease.read()['host_id']
    with db.session() as session:
        original = session.get(RuntimeSecret, 'chatgpt-personal').sealed
    with pytest.raises(ValueError, match='No se reemplazará'):
        with RuntimeCredentialStore(db, b'wrong' * 12).lease():
            pytest.fail('Wrong key must not open a lease')
    with db.session(True) as session:
        row = session.get(RuntimeSecret, 'chatgpt-personal')
        assert row.host_id == host and row.sealed == original
        row.sealed = 'CORRUPT-CIPHERTEXT'
    with pytest.raises(ValueError, match='No se reemplazará'):
        with store.lease():
            pytest.fail('Corrupt state must not be replaced')
    with db.session() as session:
        assert session.get(RuntimeSecret, 'chatgpt-personal').sealed == 'CORRUPT-CIPHERTEXT'


def test_exception_rolls_back_and_foreign_host_cannot_replace_server_identity(tmp_path):
    store = RuntimeCredentialStore(Database(tmp_path / 'runtime.db'), SECRET)
    with store.lease() as lease:
        profile = lease.read()
    with pytest.raises(RuntimeError):
        with store.lease() as lease:
            lease.save(dict(profile, refresh_token='should-rollback'))
            raise RuntimeError('simulated interrupted operation')
    with pytest.raises(ValueError):
        with store.lease() as lease:
            lease.save(dict(profile, host_id='urn:uuid:foreign-host'))
    with store.lease() as lease:
        assert lease.read() == profile


def test_independent_store_instances_serialize_refresh_updates(tmp_path):
    path = tmp_path / 'runtime.db'
    stores = [RuntimeCredentialStore(Database(path), SECRET) for _ in range(4)]
    def increment(index):
        with stores[index % 4].lease() as lease:
            profile = lease.read()
            profile['counter'] = profile.get('counter', 0) + 1
            lease.save(profile)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(increment, range(12)))
    with stores[0].lease() as lease:
        assert lease.read()['counter'] == 12


def test_portable_backup_never_contains_a_reusable_runtime_session(tmp_path):
    db = Database(tmp_path / 'runtime.db')
    store = RuntimeCredentialStore(db, SECRET)
    with store.lease() as lease:
        profile = lease.read()
        lease.save(dict(profile, refresh_token='PRIVATE-REFRESH-MARKER'))
    with db.session() as session:
        ciphertext = session.get(RuntimeSecret, 'chatgpt-personal').sealed.encode()
    backup = db.backup(tmp_path / 'portable.db')
    with sqlite3.connect(backup) as connection:
        assert connection.execute('SELECT count(*) FROM runtime_secrets').fetchone()[0] == 0
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert ciphertext not in backup.read_bytes()
    assert b'PRIVATE-REFRESH-MARKER' not in backup.read_bytes()
    with store.lease() as lease:
        assert lease.read()['refresh_token'] == 'PRIVATE-REFRESH-MARKER'


def test_invalid_key_or_oversize_profile_does_not_write_partial_state(tmp_path):
    db = Database(tmp_path / 'runtime.db')
    with pytest.raises(ValueError):
        RuntimeCredentialStore(db, b'short')
    with db.session() as session:
        assert not list(session.scalars(select(RuntimeSecret)))
    store = RuntimeCredentialStore(db, SECRET)
    with store.lease() as lease:
        profile = lease.read()
    with pytest.raises(ValueError):
        with store.lease() as lease:
            lease.save(dict(profile, refresh_token='x' * 200001))
    with store.lease() as lease:
        assert lease.read() == profile
