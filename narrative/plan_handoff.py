"""Short-lived encrypted transfer of one authorized personal registration.

RSA-OAEP wraps a random AES-GCM key. OAuth credentials never appear in the
descriptor, packet header, browser-readable plaintext, or diagnostic messages.
"""
import base64
import hashlib
import json
import math
import secrets
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def origin(value):
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('El traslado requiere el origen HTTPS de tu servidor, sin rutas ni credenciales.')
    return value.rstrip('/')


def challenge(profile, target, now=None):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    if profile.get('access_token'):
        raise ValueError('El servidor ya conserva una sesión; desconéctala antes de preparar otra.')
    clock = time.time() if now is None else now
    if profile.get('handoff_private_key') and profile.get('handoff_expires', 0) > clock:
        result = dict(profile)
        key = serialization.load_pem_private_key(result['handoff_private_key'].encode('ascii'), password=None)
    else:
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        result = dict(profile, handoff_id=str(uuid4()), handoff_expires=int(clock) + 900,
            handoff_private_key=key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode('ascii'))
    descriptor = dict(version=1, target_origin=origin(target), host_id=profile['host_id'],
        transfer_id=result['handoff_id'], expires=result['handoff_expires'],
        public_key=key.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode('ascii'))
    return result, descriptor


def _header(value):
    return {key: value[key] for key in ('version', 'target_origin', 'host_id', 'transfer_id', 'expires')}


def _validate_header(value, target, now):
    if value.get('version') != 1 or value.get('target_origin') != origin(target):
        raise ValueError('El paquete no corresponde al servidor elegido.')
    UUID(value['transfer_id'])
    if not value['host_id'].startswith('urn:uuid:'):
        raise ValueError('Identificador del servidor inválido.')
    UUID(value['host_id'][9:])
    if not isinstance(value['expires'], int) or not now < value['expires'] <= now + 960:
        raise ValueError('El traslado caducó; prepara uno nuevo desde el servidor.')


def seal(profile, descriptor, expected_origin, now=None):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        clock = time.time() if now is None else now
        _validate_header(descriptor, expected_origin, clock)
        if profile.get('host_id') == descriptor['host_id']:
            raise ValueError('El servidor debe conservar un host distinto del computador.')
        _registration(profile)
        key = serialization.load_pem_public_key(descriptor['public_key'].encode('ascii'))
        if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 3072:
            raise ValueError('Clave de traslado no admitida.')
        header, aes, nonce = _header(descriptor), secrets.token_bytes(32), secrets.token_bytes(12)
        raw = canonical(profile)
        if len(raw) > 150000:
            raise ValueError('Sesión demasiado grande.')
        sealed = AESGCM(aes).encrypt(nonce, raw, canonical(header))
        wrapped = key.encrypt(aes, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        return dict(header, nonce=base64.urlsafe_b64encode(nonce).decode('ascii'),
            wrapped_key=base64.urlsafe_b64encode(wrapped).decode('ascii'),
            ciphertext=base64.urlsafe_b64encode(sealed).decode('ascii'))
    except Exception:
        raise ValueError('No se pudo preparar el traslado cifrado. Comprueba el servidor y la vigencia de su descriptor.') from None


def _registration(profile):
    for key in ('host_id', 'client_id', 'subject', 'access_token', 'refresh_token', 'id_token'):
        if not isinstance(profile.get(key), str) or not 0 < len(profile[key]) <= 30000:
            raise ValueError('Registro personal incompleto.')
    if profile['client_id'] == 'dynamic_agent_client':
        raise ValueError('Falta el identificador emitido del registro.')
    if not isinstance(profile.get('scopes'), list) or not {'resource.invoke', 'chatgpt.tokens.use.direct'}.issubset(profile['scopes']):
        raise ValueError('Faltan permisos de uso del plan.')
    if not isinstance(profile.get('expires'), (int, float)) or not math.isfinite(profile['expires']):
        raise ValueError('Expiración del registro inválida.')


def accept(profile, packet, target, now=None):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        if len(canonical(packet)) > 220000 or set(packet) != {'version','target_origin','host_id','transfer_id','expires','nonce','wrapped_key','ciphertext'}:
            raise ValueError()
        digest = hashlib.sha256(canonical(packet)).hexdigest()
        if profile.get('last_handoff_id') == packet['transfer_id']:
            if profile.get('last_handoff_digest') != digest:
                raise ValueError()
            return dict(profile), True  # A committed retry cannot replace rotated credentials.
        clock = time.time() if now is None else now
        _validate_header(packet, target, clock)
        if profile.get('access_token') or packet['host_id'] != profile['host_id'] or packet['transfer_id'] != profile['handoff_id'] or packet['expires'] != profile['handoff_expires']:
            raise ValueError()
        private = serialization.load_pem_private_key(profile['handoff_private_key'].encode('ascii'), password=None)
        decode = lambda s: base64.b64decode(s, altchars=b'-_', validate=True)
        aes = private.decrypt(decode(packet['wrapped_key']),
            padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        imported = json.loads(AESGCM(aes).decrypt(decode(packet['nonce']),
            decode(packet['ciphertext']), canonical(_header(packet))))
        _registration(imported)
        if imported['host_id'] == profile['host_id']:
            raise ValueError()
        for key in ('subject', 'client_id'):
            if profile.get(key) and profile[key] != imported[key]:
                raise ValueError()
        fields = ('client_id','subject','email','access_token','refresh_token','id_token','scopes','expires','model')
        result = {key: imported[key] for key in fields if key in imported}
        result.update(host_id=profile['host_id'], server_verified=False,
            last_handoff_id=packet['transfer_id'], last_handoff_digest=digest)
        return result, False
    except Exception:
        raise ValueError('El traslado no es válido para esta sesión y servidor, o ha caducado. No se reemplazó la conexión.') from None
