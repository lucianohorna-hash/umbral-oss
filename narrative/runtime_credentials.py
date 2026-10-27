"""Encrypted server-side sessions with a transaction lease for refresh ownership.

No OAuth transfer or inference is performed here. The caller must authorize any
session import separately. A host ID is public metadata, never an account token.
"""
import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import re
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session
from .db import RuntimeSecret


class CredentialLease:
    def __init__(self, row, cipher):
        self.row, self.cipher, self.active = row, cipher, True

    def _require(self):
        if not self.active:
            raise ValueError('La operación protegida ya terminó.')

    def read(self):
        self._require()
        try:
            envelope = json.loads(self.cipher.decrypt(self.row.sealed.encode('ascii')))
            if envelope['name'] != self.row.name or envelope['profile']['host_id'] != self.row.host_id:
                raise ValueError()
            return envelope['profile']
        except Exception:
            raise ValueError('No se pudo abrir la sesión protegida. No se reemplazará automáticamente.') from None

    def save(self, profile):
        self._require()
        if not isinstance(profile, dict) or profile.get('host_id') != self.row.host_id:
            raise ValueError('La sesión debe conservar el identificador propio del servidor.')
        try:
            raw = json.dumps(dict(name=self.row.name, profile=profile), allow_nan=False).encode('utf-8')
        except (TypeError, ValueError):
            raise ValueError('La sesión no tiene un formato válido.') from None
        if len(raw) > 200000:
            raise ValueError('La sesión excede el tamaño permitido.')
        self.row.sealed = self.cipher.encrypt(raw).decode('ascii')
        self.row.revision += 1


class RuntimeCredentialStore:
    def __init__(self, db, secret, name='chatgpt-personal'):
        from cryptography.fernet import Fernet
        secret = secret.encode('utf-8') if isinstance(secret, str) else secret
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError('La sesión del servidor requiere un secreto privado de al menos 32 bytes.')
        if not re.fullmatch('[a-z0-9_-]{1,80}', name):
            raise ValueError('Identificador de sesión inválido.')
        key = hmac.new(secret, ('umbral/runtime-credentials/v1/' + name).encode('ascii'), hashlib.sha256).digest()
        self.db, self.name, self.cipher = db, name, Fernet(base64.urlsafe_b64encode(key))

    @contextmanager
    def lease(self):
        # Separate from the canon writer lock: model work must not hold that lock.
        # PostgreSQL serializes refresh owners across processes, not just threads.
        with Session(self.db.engine, expire_on_commit=False) as session:
            lease = None
            try:
                if self.db.is_postgres:
                    session.execute(text("SET LOCAL lock_timeout = '15s'"))
                    slot = int.from_bytes(hashlib.sha256(self.name.encode('ascii')).digest()[:4], signed=True)
                    session.execute(text('SELECT pg_advisory_xact_lock(1160397, :slot)'), {'slot': slot})
                else:
                    session.execute(text('BEGIN IMMEDIATE'))
                row = session.get(RuntimeSecret, self.name)
                if row is None:
                    host = 'urn:uuid:' + str(uuid.uuid4())
                    envelope = json.dumps(dict(name=self.name, profile=dict(host_id=host))).encode('utf-8')
                    row = RuntimeSecret(name=self.name, host_id=host,
                        sealed=self.cipher.encrypt(envelope).decode('ascii'), revision=1)
                    session.add(row)
                lease = CredentialLease(row, self.cipher)
                lease.read()  # Corrupt or wrong-key rows must fail before modification.
                yield lease
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                if lease is not None:
                    lease.active = False
