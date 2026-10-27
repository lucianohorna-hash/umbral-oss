"""Local HTTP transport. No narrative rules live in route handlers."""
from pathlib import Path
import secrets
import tempfile
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field, ValidationError, field_validator
from uuid import UUID
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select

from .db import Database, Source, Revision
from .schemas import Strict, Wizard, ProposalInput, Acceptance, TurnInput, Advance, PlayerMessageReview
from .service import Engine, DomainError, serialize
from .providers import configured_provider
from .narration import NarrationCoordinator
from .chatgpt_plan import ChatGPTPlanNarrator, mount_plan_routes
from .self_hosted_plan import SelfHostedPlanNarrator
from .security import CloudAccess, LoginLimiter, COOKIE
from .remote_bridge import RemoteBridge, BRIDGE_PATHS, MAX_BRIDGE_BODY, mount_routes


class UniverseInput(Strict):
    name: str = Field(min_length=1, max_length=200)
    description: str = ''


class Decision(Strict):
    decision: str = Field(min_length=1)


class Revert(Decision):
    expected_version: int


class Switch(Strict):
    character_id: str
    expected_version: int
    reason: str = Field(min_length=1)


class ImportInput(Strict):
    filename: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=2000000)
    campaign_id: str | None = None
    world_id: str | None = None


class Generate(Strict):
    expected_version: int = Field(ge=0)
    request_id: str | None = None
    expected_last_turn_id: str | None = None

    @field_validator('request_id', 'expected_last_turn_id')
    @classmethod
    def valid_uuid(cls, value):
        return str(UUID(value)) if value is not None else None


class Login(Strict):
    password: str = Field(min_length=1, max_length=1024)


def create_app(db_path='data/canon.db', provider=None, access: CloudAccess | None = None, self_hosted_plan=False):
    app = FastAPI(title='Multimundo · API local', version='0.1.0', description='API de administración local de confianza. No exponer a Internet sin autenticación y autorización.')
    if access and isinstance(provider, ChatGPTPlanNarrator):
        raise ValueError("La conexión personal local no se puede publicar como servidor remoto sin una integración autorizada.")
    if (self_hosted_plan or isinstance(provider, SelfHostedPlanNarrator)) and not access:
        raise ValueError('El narrador del servidor requiere autenticación privada y HTTPS.')
    if self_hosted_plan and provider is not None:
        raise ValueError('El modo personal del servidor no admite otro proveedor alternativo.')
    if isinstance(provider, SelfHostedPlanNarrator) and provider.origin != access.public_url:
        raise ValueError('El narrador no corresponde al origen de esta instalación.')
    db = Database(db_path)
    engine = Engine(db)
    csrf = secrets.token_urlsafe(32)
    if self_hosted_plan:
        from .runtime_credentials import RuntimeCredentialStore
        narrator = SelfHostedPlanNarrator(RuntimeCredentialStore(db, access.secret), access.public_url)
    else:
        narrator = provider or configured_provider()
    narration = NarrationCoordinator(engine, narrator)
    app.state.engine, app.state.db = engine, db
    limiter = LoginLimiter()
    remote = RemoteBridge(engine, enabled=access is not None)
    mount_routes(app, remote)
    mount_plan_routes(app, narrator, cloud=bool(access))

    @app.middleware('http')
    async def local_boundary(request: Request, call_next):
        host = request.url.hostname
        allowed_hosts = [urlsplit(access.public_url).hostname] if access else ['localhost', '127.0.0.1', '::1', 'testserver']
        if host not in allowed_hosts:
            return JSONResponse({'detail': 'La aplicación local solo admite acceso desde loopback.'}, status_code=403)
        origin = request.headers.get('origin')
        expected_origin = access.public_url if access else f'{request.url.scheme}://{request.url.netloc}'
        if origin and origin != expected_origin:
            return JSONResponse({'detail': 'Origen no autorizado.'}, status_code=403)
        machine = request.url.path in BRIDGE_PATHS
        if request.url.path.startswith('/bridge/'):
            if not machine:
                return JSONResponse({'detail': 'Ruta de puente inexistente.'}, status_code=404)
            try:
                request.state.bridge_universe = await run_in_threadpool(remote.authenticate, request.headers.get('authorization'))
            except DomainError as error:
                return JSONResponse({'detail': error.detail}, status_code=error.status, headers={'Cache-Control': 'no-store'})
            except Exception:
                return JSONResponse({'detail': 'No se pudo validar la conexión.'}, status_code=503, headers={'Cache-Control': 'no-store'})
        token = request.cookies.get(COOKIE)
        session = access.session(token) if access else {'auth': True}
        if access and request.url.path.startswith('/api/') and request.url.path not in ['/api/auth', '/api/login'] and (not session or not session['auth']):
            return JSONResponse({'detail': 'Inicia sesión para abrir tu archivo privado.'}, status_code=401)
        if request.method not in ['GET', 'HEAD', 'OPTIONS']:
            expected_csrf = access.csrf(token) if access else csrf
            # Only exact machine paths authenticated above bypass cookie CSRF.
            # Bearer grants never authenticate an /api owner endpoint.
            if not machine and ((access and not session) or not secrets.compare_digest(request.headers.get('x-csrf-token', '').encode('utf-8'), expected_csrf.encode('utf-8'))):
                return JSONResponse({'detail': 'Sesión local caducada. Recarga la página.'}, status_code=403)
            # Bound streaming bodies too, not only caller-declared Content-Length.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > (MAX_BRIDGE_BODY if machine else 2200000):
                    return JSONResponse({'detail': 'Solicitud demasiado grande.'}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-API-Version'] = '1'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith(('/api', '/bridge/')):
            response.headers['Cache-Control'] = 'no-store'
        if access:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    @app.exception_handler(DomainError)
    async def domain_error(_, error):
        return JSONResponse({'detail': error.detail}, status_code=error.status)

    @app.exception_handler(ValueError)
    async def value_error(_, error):
        return JSONResponse({'detail': str(error)}, status_code=422)

    @app.get('/api/bootstrap')
    def bootstrap(request: Request):
        return dict(universes=engine.universes(), csrf_token=access.csrf(request.cookies.get(COOKIE)) if access else csrf, provider=narrator.name, api_version=1, cloud=bool(access))

    @app.get('/api/auth')
    def auth(request: Request):
        if not access:
            return dict(authenticated=True, cloud=False, csrf_token=csrf)
        token = request.cookies.get(COOKIE)
        session = access.session(token)
        if not session:
            token = access.token()
            session = access.session(token)
        response = JSONResponse(dict(authenticated=session['auth'], cloud=True, csrf_token=access.csrf(token)))
        response.set_cookie(COOKIE, token, httponly=True, secure=True, samesite='strict', path='/', max_age=604800 if session['auth'] else 900)
        return response

    @app.post('/api/login')
    def login(request: Request, body: Login):
        if not access:
            return {'authenticated': True}
        key = request.client.host if request.client else 'unknown'
        if not limiter.allow(key):
            raise DomainError(429, 'Demasiados intentos. Espera diez minutos.')
        if not access.password_matches(body.password):
            raise DomainError(401, 'Contraseña incorrecta.')
        limiter.reset(key)
        response = JSONResponse({'authenticated': True})
        response.set_cookie(COOKIE, access.token(True), httponly=True, secure=True, samesite='strict', path='/', max_age=604800)
        return response

    @app.post('/api/logout')
    def logout():
        response = JSONResponse({'authenticated': False})
        response.delete_cookie(COOKIE, path='/', secure=True, httponly=True, samesite='strict')
        return response

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    @app.post('/api/universes')
    def universe_create(body: UniverseInput):
        return engine.create_universe(body.name, body.description)

    @app.post('/api/wizard')
    def wizard(body: Wizard):
        return engine.wizard(body)

    @app.get('/api/universes/{identity}')
    def snapshot(identity: str):
        return engine.snapshot(identity)

    @app.post('/api/universes/{identity}/proposals')
    def propose(identity: str, body: ProposalInput):
        return engine.propose(identity, body)

    @app.post('/api/proposals/{identity}/review')
    def review(identity: str):
        return engine.review(identity)

    @app.post('/api/proposals/{identity}/accept')
    def accept(identity: str, body: Acceptance):
        return engine.accept(identity, body)

    @app.post('/api/proposals/{identity}/reject')
    def reject(identity: str, body: Decision):
        return engine.reject(identity, body.decision)

    @app.post('/api/universes/{identity}/revert')
    def revert(identity: str, body: Revert):
        return engine.revert(identity, body.expected_version, body.decision)

    @app.get('/api/campaigns/{identity}/turns')
    def turns(identity: str):
        return engine.turns(identity)

    @app.post('/api/campaigns/{identity}/turns')
    def add_turn(identity: str, body: TurnInput):
        return engine.add_turn(identity, body)

    @app.post('/api/campaigns/{identity}/turns/{turn_id}/review-player')
    def review_player_message(identity: str, turn_id: str, body: PlayerMessageReview):
        return engine.review_player_message(identity, turn_id, body)

    @app.get('/api/campaigns/{identity}/context')
    def context(identity: str, audience: str = 'character'):
        return engine.context(identity, audience)

    @app.post('/api/campaigns/{identity}/protagonist')
    def switch(identity: str, body: Switch):
        return engine.switch_protagonist(identity, body.character_id, body.expected_version, body.reason)

    @app.post('/api/campaigns/{identity}/generate')
    def generate(identity: str, body: Generate):
        return narration.generate(identity, body)

    @app.post('/api/universes/{identity}/advance')
    def advance(identity: str, body: Advance):
        return engine.advance(identity, body)

    @app.post('/api/universes/{identity}/import')
    def import_text(identity: str, body: ImportInput):
        return engine.import_text(identity, body.filename, body.content, body.campaign_id, body.world_id)

    @app.get('/api/universes/{identity}/search')
    def search(identity: str, q: str = ''):
        return {'records': engine.search(identity, q)}

    @app.get('/api/universes/{identity}/memory')
    def search_memory(identity: str, q: str = '', audience: str = 'narrator', campaign_id: str | None = None, offset: int = 0, limit: int = 25):
        return engine.search_memory(identity, q, audience, campaign_id, offset, limit)

    @app.get('/api/universes/{identity}/memory/{origin}/{item_id}')
    def read_memory(identity: str, origin: str, item_id: str, audience: str = 'narrator', campaign_id: str | None = None):
        return engine.read_memory(identity, origin, item_id, audience, campaign_id)

    @app.get('/api/universes/{identity}/export.md')
    def export(identity: str):
        return Response(engine.export_markdown(identity), media_type='text/markdown; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="biblia-del-mundo.md"'})

    @app.get('/api/universes/{identity}/sources/{source_id}')
    def source(identity: str, source_id: str):
        with db.session() as session:
            row = session.get(Source, source_id)
            if not row or row.universe_id != identity:
                raise DomainError(404, 'Fuente inexistente.')
            return serialize(row)

    @app.get('/api/universes/{identity}/revisions/{version}')
    def revision(identity: str, version: int):
        with db.session() as session:
            row = session.scalar(select(Revision).where(Revision.universe_id == identity, Revision.version == version))
            if not row:
                raise DomainError(404, 'Revisión inexistente.')
            return serialize(row)

    @app.get('/api/backup')
    def backup():
        folder = Path(tempfile.mkdtemp(prefix='multimundo-backup-'))
        target = db.backup(folder / 'multimundo.sqlite3')
        def cleanup():
            target.unlink(missing_ok=True)
            folder.rmdir()
        return FileResponse(target, media_type='application/vnd.sqlite3', filename='multimundo-backup.sqlite3', background=BackgroundTask(cleanup))

    static = Path(__file__).parent / 'static'
    @app.get('/')
    def index():
        return FileResponse(static / 'index.html')
    @app.get('/sw.js')
    def service_worker():
        return FileResponse(static / 'sw.js', media_type='application/javascript', headers={'Cache-Control': 'no-cache', 'Service-Worker-Allowed': '/'})
    app.mount('/static', StaticFiles(directory=static), name='static')
    return app
