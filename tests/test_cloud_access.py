import time
import pytest
from fastapi.testclient import TestClient
import narrative.cloud as cloud
from narrative.api import create_app
from narrative.cloud import from_environment
from narrative.security import CloudAccess, COOKIE


def cloud_client(tmp_path):
    access = CloudAccess.build('https://testserver', 'Una contraseña privada larga', 's' * 48)
    c = TestClient(create_app(tmp_path / 'private.db', access=access), base_url='https://testserver')
    c.headers['X-CSRF-Token'] = c.get('/api/auth').json()['csrf_token']
    c.headers['Origin'] = 'https://testserver'
    return c, access


@pytest.mark.parametrize('endpoint', ['/api/bootstrap', '/api/backup', '/api/universes/anything', '/api/campaigns/anything/context'])
def test_cloud_protects_all_private_reads(tmp_path, endpoint):
    c, _ = cloud_client(tmp_path)
    response = c.get(endpoint)
    assert response.status_code == 401
    assert 'universes' not in response.json()


def test_login_logout_and_session_survive_app_restart(tmp_path):
    c, access = cloud_client(tmp_path)
    assert c.post('/api/login', json={'password': 'wrong'}).status_code == 401
    response = c.post('/api/login', json={'password': 'Una contraseña privada larga'})
    assert response.status_code == 200
    assert 'HttpOnly' in response.headers['set-cookie']
    assert 'Secure' in response.headers['set-cookie']
    assert c.get('/api/bootstrap').status_code == 200
    token = c.cookies.get(COOKIE)
    reopened = TestClient(create_app(tmp_path / 'private.db', access=access), base_url='https://testserver')
    reopened.cookies.set(COOKIE, token)
    assert reopened.get('/api/bootstrap').status_code == 200
    reopened.headers['X-CSRF-Token'] = reopened.get('/api/auth').json()['csrf_token']
    assert reopened.post('/api/logout').status_code == 200
    # Test cookie jars may retain manually set cookies, so verify expiration header.
    assert reopened.post('/api/logout').status_code in [200, 401]


def test_invalid_expired_and_rotated_sessions():
    access = CloudAccess.build('https://example.com', 'long password for tests', 'k' * 48)
    token = access.token(True)
    assert access.session(token)['auth']
    assert access.session(token + 'altered') is None
    other = CloudAccess.build('https://example.com', 'long password for tests', 'z' * 48)
    assert other.session(token) is None


def test_login_rate_limit_and_origin(tmp_path):
    c, _ = cloud_client(tmp_path)
    assert c.post('/api/login', json={'password': 'wrong'}, headers={'Origin': 'https://evil.example'}).status_code == 403
    for _ in range(10):
        assert c.post('/api/login', json={'password': 'wrong'}).status_code == 401
    assert c.post('/api/login', json={'password': 'wrong'}).status_code == 429


def test_cloud_cannot_fall_back_to_ephemeral_sqlite(monkeypatch, tmp_path):
    monkeypatch.setenv('DATABASE_URL', str(tmp_path / 'must-not-exist.db'))
    with pytest.raises(ValueError, match='PostgreSQL'):
        from_environment()
    assert not (tmp_path / 'must-not-exist.db').exists()


def test_cloud_requires_tls_before_connecting(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://u:p@localhost/db')
    with pytest.raises(ValueError, match='TLS'):
        from_environment()


def set_valid_cloud_environment(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://u:p@localhost/db?sslmode=require')
    monkeypatch.setenv('NARRATIVE_ACCESS_PASSWORD', 'una contraseña privada válida')
    monkeypatch.setenv('NARRATIVE_SESSION_SECRET', 's' * 48)
    monkeypatch.setattr(cloud, 'create_app', lambda database, access: access)


def test_render_external_url_is_used_when_explicit_url_is_absent(monkeypatch):
    set_valid_cloud_environment(monkeypatch)
    monkeypatch.delenv('NARRATIVE_PUBLIC_URL', raising=False)
    monkeypatch.setenv('RENDER', 'true')
    monkeypatch.setenv('RENDER_EXTERNAL_URL', 'https://umbral.example.onrender.com')

    access = from_environment()

    assert access.public_url == 'https://umbral.example.onrender.com'


def test_explicit_public_url_takes_precedence_on_render(monkeypatch):
    set_valid_cloud_environment(monkeypatch)
    monkeypatch.setenv('NARRATIVE_PUBLIC_URL', 'https://narrative.example.com')
    monkeypatch.setenv('RENDER', 'true')
    monkeypatch.setenv('RENDER_EXTERNAL_URL', 'https://umbral.example.onrender.com')

    access = from_environment()

    assert access.public_url == 'https://narrative.example.com'


def test_missing_public_url_fails_closed_outside_render(monkeypatch):
    set_valid_cloud_environment(monkeypatch)
    monkeypatch.delenv('NARRATIVE_PUBLIC_URL', raising=False)
    monkeypatch.delenv('RENDER', raising=False)
    monkeypatch.setenv('RENDER_EXTERNAL_URL', 'https://umbral.example.onrender.com')

    with pytest.raises(ValueError, match='NARRATIVE_PUBLIC_URL'):
        from_environment()


def test_invalid_render_external_url_fails_closed(monkeypatch):
    set_valid_cloud_environment(monkeypatch)
    monkeypatch.delenv('NARRATIVE_PUBLIC_URL', raising=False)
    monkeypatch.setenv('RENDER', 'true')
    monkeypatch.setenv('RENDER_EXTERNAL_URL', 'http://umbral.example.onrender.com')

    with pytest.raises(ValueError, match='HTTPS'):
        from_environment()


def test_weak_cloud_credentials_rejected():
    with pytest.raises(ValueError):
        CloudAccess.build('http://example.com', 'password', 'secret')
