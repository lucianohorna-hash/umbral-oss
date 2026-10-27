"""Private single-owner cloud access. Separate from narrative domain and future memberships."""
from dataclasses import dataclass
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from urllib.parse import urlsplit

COOKIE = '__Host-umbral'


@dataclass
class CloudAccess:
    public_url: str
    secret: bytes
    password_digest: bytes

    @classmethod
    def build(cls, public_url, password, secret):
        parsed = urlsplit(public_url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.path not in ['', '/'] or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('NARRATIVE_PUBLIC_URL debe ser un origen HTTPS sin rutas ni credenciales.')
        if len(password) < 16 or len(secret) < 32:
            raise ValueError('La nube requiere contraseña de al menos 16 caracteres y secreto de sesión de al menos 32.')
        key = secret.encode('utf-8')
        digest = hashlib.scrypt(password.encode('utf-8'), salt=key[:32], n=16384, r=8, p=1)
        return cls(public_url.rstrip('/'), key, digest)

    def password_matches(self, password):
        if len(password) > 1024:
            return False
        digest = hashlib.scrypt(password.encode('utf-8'), salt=self.secret[:32], n=16384, r=8, p=1)
        return hmac.compare_digest(digest, self.password_digest)

    def token(self, authenticated=False):
        payload = dict(auth=authenticated, expires=int(time.time()) + (86400 * 7 if authenticated else 900), nonce=secrets.token_hex(16))
        data = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode().rstrip('=')
        return data + '.' + hmac.new(self.secret, data.encode(), hashlib.sha256).hexdigest()

    def session(self, token):
        try:
            if not token or len(token) > 1024:
                return None
            data, signature = token.split('.')
            if not hmac.compare_digest(signature, hmac.new(self.secret, data.encode(), hashlib.sha256).hexdigest()):
                return None
            payload = json.loads(base64.urlsafe_b64decode(data + '=' * (-len(data) % 4)))
            return payload if isinstance(payload.get('auth'), bool) and payload['expires'] > time.time() else None
        except (ValueError, KeyError, TypeError):
            return None

    def csrf(self, token):
        return hmac.new(self.secret, ('csrf:' + (token or '')).encode(), hashlib.sha256).hexdigest()


class LoginLimiter:
    def __init__(self):
        self.attempts = {}
        self.lock = threading.Lock()

    def allow(self, key):
        with self.lock:
            now = time.monotonic()
            self.attempts = {k: v for k, v in self.attempts.items() if v[1] > now}
            count, deadline = self.attempts.get(key, (0, now + 600))
            if count >= 10 or len(self.attempts) >= 1024:
                return False
            self.attempts[key] = (count + 1, deadline)
            return True

    def reset(self, key):
        with self.lock:
            self.attempts.pop(key, None)
