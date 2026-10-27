"""Personal loopback SIWC runtime. Never borrows Codex/browser credentials."""
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
import uuid
from urllib.parse import urlencode, urlsplit

import httpx
from .narrator_policy import NARRATOR_INSTRUCTIONS

AUTH = 'https://auth.openai.com'
RESOURCE = 'https://api.openai.com/v1'
SCOPE = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
INSTRUCTIONS = NARRATOR_INSTRUCTIONS


def _protect(value, decrypt=False):
    if os.name != 'nt':
        return value
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    api = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not api(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError('No se pudo proteger la conexión en este computador.')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)


class ChatGPTPlanNarrator:
    name = 'chatgpt-plan'

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'connection.bin'
        self.lock = threading.RLock()
        self.pending = None
        self.profile = self._read()
        self.host = self.profile.get('host_id') or 'urn:uuid:' + str(uuid.uuid4())
        if not self.profile:
            self.profile = {'host_id': self.host}
            self._save()

    def _read(self):
        if not self.path.exists():
            return {}
        try:
            return json.loads(_protect(self.path.read_bytes(), decrypt=True))
        except Exception:
            raise ValueError('No se pudo abrir la conexión protegida. No se reemplazará automáticamente.') from None

    def _save(self):
        payload = _protect(json.dumps(self.profile).encode())
        temp = self.path.with_suffix('.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, self.path)
        if os.name != 'nt':
            self.path.chmod(0o600)

    def status(self):
        with self.lock:
            return dict(available=True, connected=bool(self.profile.get('access_token')),
                account=self.profile.get('email', ''), model=self.profile.get('model', ''),
                message='La sesión se preparó para el servidor; vuelve a autorizar si necesitas otra sesión local.' if self.profile.get('moved_to_host') else
                'Uso personal local. Consume los límites autorizados de tu plan; sin respaldo automático de pago.')

    def prepare_handoff(self, descriptor, target_origin):
        from .plan_handoff import seal, origin, _header
        with self.lock:
            prior = self.profile.get('handoff_packet')
            if prior:
                try:
                    if _header(prior) == _header(descriptor) and prior['target_origin'] == origin(target_origin):
                        return json.loads(json.dumps(prior))
                except Exception:
                    pass
                raise ValueError('Ya hay un traslado preparado. Recupera su mismo paquete o vuelve a autorizar una conexión local.')
            packet = seal(self.profile, descriptor, target_origin)
            # Relinquish refresh ownership before delivering the encrypted packet.
            # No plaintext credential export is written to disk or sent to the browser.
            self.profile = {key: self.profile[key] for key in ('host_id','client_id','subject','email','model') if key in self.profile}
            self.profile['moved_to_host'] = descriptor['host_id']
            self.profile['handoff_packet'] = packet
            self._save()
            return packet

    def begin(self, origin):
        parsed = urlsplit(origin)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('Abre Umbral en http://127.0.0.1 antes de conectar ChatGPT.')
        with self.lock:
            verifier = secrets.token_urlsafe(48)
            self.pending = dict(state=secrets.token_urlsafe(32), nonce=secrets.token_urlsafe(32),
                verifier=verifier, redirect_uri=origin + '/auth/callback', expires=time.time()+600,
                client_id=self.profile.get('client_id', 'dynamic_agent_client'))
            q = dict(client_id=self.pending['client_id'], ext_agent_host_id=self.host,
                response_type='code', redirect_uri=self.pending['redirect_uri'], scope=SCOPE,
                resource=RESOURCE, state=self.pending['state'], nonce=self.pending['nonce'],
                code_challenge_method='S256', code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='))
            if q['client_id'] == 'dynamic_agent_client':
                q['agent_name_hint'] = 'Umbral'
            elif self.profile.get('id_token'):
                q['id_token_hint'] = self.profile['id_token']
            return AUTH + '/api/accounts/authorize?' + urlencode(q)

    def _validate_identity(self, client, token, client_id, nonce):
        import jwt
        metadata = client.get(AUTH + '/.well-known/openid-configuration')
        metadata.raise_for_status()
        metadata = metadata.json()
        jwks_url = metadata['jwks_uri']
        if urlsplit(jwks_url).scheme != 'https' or urlsplit(jwks_url).hostname != 'auth.openai.com':
            raise ValueError('Origen de claves no autorizado.')
        keys = client.get(jwks_url)
        keys.raise_for_status()
        header = jwt.get_unverified_header(token)
        if header.get('alg') != 'RS256':
            raise ValueError('Firma de identidad no admitida.')
        key = next(k for k in keys.json()['keys'] if k.get('kid') == header.get('kid'))
        claims = jwt.decode(token, jwt.PyJWK.from_dict(key).key, algorithms=['RS256'],
            issuer=AUTH, audience=client_id, options={'require':['exp','iss','aud','sub','nonce']})
        if not secrets.compare_digest(claims['nonce'], nonce):
            raise ValueError('Identidad no vinculada a esta conexión.')
        return claims

    def finish(self, query, callback_uri):
        with self.lock:
            pending = self.pending
            if not pending or pending['expires'] < time.time() or not secrets.compare_digest(query.get('state',''), pending['state']):
                raise ValueError('La conexión caducó o no corresponde a este intento. Vuelve a iniciarla desde Umbral.')
            self.pending = None
            if callback_uri != pending['redirect_uri'] or query.get('error') or not query.get('code'):
                raise ValueError('La conexión no fue autorizada. Tu campaña sigue guardada.')
            client_id = query.get('client_id', pending['client_id'])
            if not client_id or client_id == 'dynamic_agent_client' or (pending['client_id'] != 'dynamic_agent_client' and client_id != pending['client_id']):
                raise ValueError('Registro de aplicación incompleto.')
            try:
                with httpx.Client(timeout=30, follow_redirects=False) as client:
                    response = client.post(AUTH + '/api/accounts/oauth/token', data=dict(
                        grant_type='authorization_code', client_id=client_id, code=query['code'],
                        code_verifier=pending['verifier'], redirect_uri=pending['redirect_uri'], resource=RESOURCE))
                    response.raise_for_status()
                    tokens = response.json()
                    identity = self._validate_identity(client, tokens['id_token'], client_id, pending['nonce'])
                if self.profile.get('subject') and identity['sub'] != self.profile['subject']:
                    raise ValueError('La cuenta no coincide con la conexión anterior.')
                scopes = tokens.get('scope','').split()
                if not {'chatgpt.tokens.use.direct','resource.invoke'}.issubset(scopes) or not tokens.get('access_token') or tokens.get('token_type','').lower() != 'bearer':
                    raise ValueError('No se autorizó el uso del plan. Iniciar sesión por sí solo no habilita al narrador.')
                self.profile = dict(host_id=self.host, client_id=client_id, subject=identity['sub'],
                    email=identity.get('email',''), scopes=scopes, access_token=tokens['access_token'],
                    refresh_token=tokens.get('refresh_token',''), id_token=tokens['id_token'],
                    expires=time.time()+int(tokens.get('expires_in',3600)))
                self._save()
            except ValueError:
                raise
            except Exception:
                raise ValueError('No se pudo completar la conexión oficial. Vuelve a intentarlo desde Umbral.') from None

    def _token(self, client):
        if not self.profile.get('access_token'):
            raise ValueError('Conecta tu plan de ChatGPT desde Umbral antes de narrar.')
        if self.profile['expires'] > time.time()+90:
            return self.profile['access_token']
        if not self.profile.get('refresh_token'):
            raise ValueError('Tu conexión caducó. Vuelve a conectar ChatGPT.')
        response = client.post(AUTH + '/api/accounts/oauth/token', data=dict(grant_type='refresh_token',
            client_id=self.profile['client_id'], refresh_token=self.profile['refresh_token'], resource=RESOURCE))
        response.raise_for_status()
        tokens = response.json()
        scopes = tokens.get('scope',' '.join(self.profile['scopes'])).split()
        if not {'chatgpt.tokens.use.direct','resource.invoke'}.issubset(scopes):
            raise ValueError('El permiso para usar tu plan ya no está disponible.')
        updated = dict(self.profile, access_token=tokens['access_token'],
            refresh_token=tokens.get('refresh_token',self.profile['refresh_token']),
            scopes=scopes, expires=time.time()+int(tokens.get('expires_in',3600)))
        self.profile = updated
        self._save()
        return updated['access_token']

    def models(self):
        with self.lock, httpx.Client(timeout=30, follow_redirects=False) as client:
            response = client.get(RESOURCE+'/models', headers={'Authorization':'Bearer '+self._token(client)})
            response.raise_for_status()
            return [dict(id=m['slug'], name=m.get('display_name',m['slug']))
                for m in response.json()['models'] if m.get('visibility') == 'list']

    def select_model(self, model):
        with self.lock:
            if model not in [m['id'] for m in self.models()]:
                raise ValueError('El modelo no está disponible para tu cuenta.')
            self.profile['model'] = model
            self._save()

    def disconnect(self):
        with self.lock:
            confirmed = not bool(self.profile.get('refresh_token'))
            if not confirmed:
                try:
                    with httpx.Client(timeout=20, follow_redirects=False) as client:
                        metadata = client.get(AUTH+'/.well-known/openid-configuration')
                        metadata.raise_for_status()
                        url = metadata.json()['revocation_endpoint']
                        if urlsplit(url).scheme != 'https' or urlsplit(url).hostname != 'auth.openai.com':
                            raise ValueError('Origen no autorizado.')
                        r = client.post(url,data=dict(token=self.profile['refresh_token'],token_type_hint='refresh_token',client_id=self.profile['client_id']))
                        confirmed = r.status_code == 200
                except Exception:
                    pass
            self.profile = {k:v for k,v in self.profile.items() if k in ['host_id','client_id','subject','email']}
            self.pending = None
            self._save()
            return dict(remote_revoked=confirmed, message='Conexión local cerrada.' if confirmed else 'Conexión local cerrada; revocación remota no confirmada. Puedes desconectar Umbral en los ajustes de ChatGPT.')

    def narrate(self, context, turns):
        with self.lock, httpx.Client(timeout=90, follow_redirects=False) as client:
            model = self.profile.get('model')
            if not model:
                raise ValueError('Elige un modelo disponible en tu conexión de ChatGPT.')
            messages = [dict(role='developer',content=INSTRUCTIONS+' Estado canónico: '+json.dumps(context,ensure_ascii=False))]
            messages.extend(dict(role='user' if t['role']=='user' else 'assistant',content=t['content']) for t in turns[-30:] if t['role'] in ['user','narrator'])
            headers = {'Authorization':'Bearer '+self._token(client)}
            text, completed, size = [], False, 0
            with client.stream('POST',RESOURCE+'/responses',headers=headers,json=dict(model=model,input=messages,store=False,stream=True)) as response:
                if response.status_code in [401,403,429]:
                    raise ValueError('Tu plan no permite continuar ahora: revisa conexión, permisos o límites. No se usará facturación de API alternativa.')
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith('data:') or line[5:].strip() == '[DONE]':
                        continue
                    event = json.loads(line[5:])
                    kind = event.get('type')
                    if kind == 'response.output_text.delta':
                        delta = event.get('delta','')
                        size += len(delta)
                        if size > 50000:
                            raise ValueError('La respuesta excede el límite seguro; no se guardó texto parcial.')
                        text.append(delta)
                    elif kind == 'response.completed':
                        completed = True
                        break
                    elif kind in ['response.failed','response.incomplete','error']:
                        raise ValueError('La respuesta no terminó o se agotó el uso permitido. No se guardó texto parcial ni se activó una API de pago.')
            content = ''.join(text).strip()
            if not completed or not content:
                raise ValueError('La respuesta quedó incompleta. Tu campaña sigue guardada; puedes reintentar.')
            return content


def mount_plan_routes(app, narrator, cloud=False):
    from fastapi import Request
    from fastapi.responses import HTMLResponse
    from pydantic import BaseModel, ConfigDict
    from .service import DomainError
    from .self_hosted_plan import SelfHostedPlanNarrator
    remote = isinstance(narrator, SelfHostedPlanNarrator)
    enabled = (isinstance(narrator, ChatGPTPlanNarrator) and not cloud) or (remote and cloud)

    def require():
        if not enabled:
            raise DomainError(409, 'La conexión directa con el plan no está habilitada en este servidor. La nube requiere acceso oficial propio.')

    class ModelChoice(BaseModel):
        model_config = ConfigDict(extra='forbid')
        model: str

    class HandoffInput(BaseModel):
        model_config = ConfigDict(extra='forbid')
        descriptor: dict
        target_origin: str

    class ImportHandoff(BaseModel):
        model_config = ConfigDict(extra='forbid')
        packet: dict

    @app.get('/api/chatgpt-plan')
    def status():
        return narrator.status() if enabled else dict(available=False, connected=False,
            message='La conexión directa del plan está disponible en el modo personal local; el servicio alojado necesita acceso oficial.')

    @app.post('/api/chatgpt-plan/connect')
    def connect(request: Request):
        require()
        if remote:
            raise DomainError(409, 'La autorización loopback se completa en el computador; no se inicia desde el servidor.')
        return dict(url=narrator.begin(str(request.base_url).rstrip('/')))

    @app.post('/api/chatgpt-plan/handoff')
    def handoff_descriptor():
        require()
        if not remote:
            raise DomainError(409, 'El descriptor se prepara en tu servidor personal.')
        return narrator.handoff_descriptor()

    @app.post('/api/chatgpt-plan/prepare-transfer')
    def prepare_transfer(body: HandoffInput):
        require()
        if remote:
            raise DomainError(409, 'El paquete se prepara en la instalación personal local.')
        return narrator.prepare_handoff(body.descriptor, body.target_origin)

    @app.post('/api/chatgpt-plan/import')
    def import_handoff(body: ImportHandoff):
        require()
        if not remote:
            raise DomainError(409, 'La sesión cifrada se recibe en el servidor personal.')
        return narrator.import_handoff(body.packet)

    @app.get('/auth/callback')
    def callback(request: Request):
        require()
        if remote:
            raise DomainError(409, 'Este servidor no recibe retornos de autorización loopback.')
        try:
            narrator.finish(dict(request.query_params),str(request.url).split('?')[0])
            body = '<h1>ChatGPT conectado a Umbral</h1><p>Vuelve a Umbral para elegir un modelo y jugar.</p><a href="/">Volver a Umbral</a>'
            status_code = 200
        except Exception:
            body = '<h1>No se completó la conexión</h1><p>Vuelve a Umbral e inicia un intento nuevo. Tu campaña sigue guardada.</p><a href="/">Volver a Umbral</a>'
            status_code = 400
        return HTMLResponse(body,status_code=status_code,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

    @app.get('/api/chatgpt-plan/models')
    def models():
        require()
        try:
            return dict(models=narrator.models())
        except Exception:
            raise DomainError(502, 'No se pudo consultar los modelos de tu plan. Revisa tu conexión.') from None

    @app.post('/api/chatgpt-plan/model')
    def model(body: ModelChoice):
        require()
        try:
            narrator.select_model(body.model)
        except Exception:
            raise DomainError(422,'No se pudo seleccionar ese modelo de tu plan.') from None
        return narrator.status()

    @app.post('/api/chatgpt-plan/disconnect')
    def disconnect():
        require()
        return narrator.disconnect()
