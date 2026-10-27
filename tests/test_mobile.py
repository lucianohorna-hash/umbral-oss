import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from narrative.api import create_app


def test_mobile_assets_are_served_and_service_worker_is_not_stale(tmp_path):
    client = TestClient(create_app(tmp_path / "mobile.sqlite3"))

    worker = client.get("/sw.js")
    assert worker.status_code == 200
    assert worker.headers["cache-control"] == "no-cache"
    assert worker.headers["service-worker-allowed"] == "/"

    manifest_response = client.get("/static/manifest.webmanifest")
    assert manifest_response.status_code == 200
    manifest = json.loads(manifest_response.text)
    assert manifest["start_url"] == manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert {icon["src"] for icon in manifest["icons"]} == {
        "/static/icon-192.png",
        "/static/icon-512.png",
    }

    mobile = client.get("/static/mobile.js")
    assert mobile.status_code == 200
    assert "fetch('/health'" in mobile.text
    assert "Este chequeo no confirma tu sesión" in mobile.text
    assert "navigator.serviceWorker.register" not in mobile.text


def test_service_worker_caches_only_explicit_public_shell_paths():
    text = (Path(__file__).parents[1] / "narrative/static/sw.js").read_text(encoding="utf-8")
    match = re.search(r"const ASSETS = new Set\(\[(.*?)\]\);", text, re.S)
    assert match, "service worker needs a literal public shell allowlist"
    assets = set(re.findall(r"'([^']+)'", match.group(1)))
    assert assets == {
        "/",
        "/static/style.css",
        "/static/app.js",
        "/static/auth.js",
        "/static/mobile.js",
        "/static/chatgpt-plan.js",
        "/static/manifest.webmanifest",
        "/static/icon-192.png",
        "/static/icon-512.png",
    }
    assert "request.method !== 'GET'" in text
    assert "url.origin !== self.location.origin" in text
    assert "!ASSETS.has(url.pathname)" in text
    assert "/api/" not in text
    assert "/health" not in text


def test_mobile_revalidates_shell_and_loads_versioned_narration_scripts(tmp_path):
    client = TestClient(create_app(tmp_path / 'shell.sqlite3'))
    shell=client.get('/')
    assert shell.headers['cache-control'] == 'no-cache'
    for name in ('app.js','chatgpt-plan.js'):
        assert '/static/'+name+'?v=20261027-mobile2' in shell.text
        assert client.get('/static/'+name).headers['cache-control'] == 'no-cache'
