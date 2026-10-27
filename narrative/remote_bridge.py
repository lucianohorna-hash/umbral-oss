"""Optional machine transport for an authenticated, owner-controlled proxy.

This is NOT an OAuth server and is not a public anonymous MCP endpoint. A trusted
proxy authenticates its own users, then sends one revocable bearer grant scoped
to one universe. The owner alone creates/revokes grants in the ordinary private
API. No tool can grant access, accept canon, or confirm protagonist decisions.

No optional MCP SDK is needed by this HTTP adapter. Its fixed typed registry
mirrors the ten stdio tools; dispatch never accepts a URL, SQL or method path.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import re
import secrets
from typing import Any, Literal

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from pydantic import Field, ValidationError
from sqlalchemy import select

from .db import BridgeGrant, uid
from .mcp_bridge import INSTRUCTIONS, NarrativeBridge
from .schemas import DATA_MODELS, Kind, RecordInput, SceneCharacter, Strict
from .service import DomainError


BRIDGE_PATHS = frozenset(('/bridge/tools/list', '/bridge/tools/call'))
MAX_BRIDGE_BODY = 260_000
TOKEN_PATTERN = re.compile(r'Bearer umbral_bg_([0-9a-f-]{36})\.([A-Za-z0-9_-]{43})\Z')
AUTH_ERROR = 'Conexión no autorizada o caducada.'


def _utc():
    return datetime.now(timezone.utc)


def grant_metadata(grant):
    """Never serialize a model wholesale: the credential hash is private too."""
    return dict(id=grant.id, universe_id=grant.universe_id, label=grant.label,
                created_at=grant.created_at, expires_at=grant.expires_at,
                revoked_at=grant.revoked_at,
                active=not grant.revoked_at and datetime.fromisoformat(grant.expires_at) > _utc())


class GrantInput(Strict):
    universe_id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=120)
    expires_in_days: int = Field(default=90, ge=1, le=365)


class BridgeCall(Strict):
    name: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ListCampaigns(Strict):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)


class CampaignContext(Strict):
    campaign_id: str = Field(min_length=1, max_length=100)
    audience: Literal['character', 'narrator'] = 'character'


class SearchMemory(Strict):
    query: str = Field(min_length=1, max_length=500)
    audience: Literal['character', 'narrator'] = 'character'
    campaign_id: str | None = Field(default=None, max_length=100)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=25, ge=1, le=100)


class ReadMemory(Strict):
    origin: Literal['record', 'turn', 'source', 'proposal']
    item_id: str = Field(min_length=1, max_length=100)
    audience: Literal['character', 'narrator'] = 'character'
    campaign_id: str | None = Field(default=None, max_length=100)


class RecentTurns(Strict):
    campaign_id: str = Field(min_length=1, max_length=100)
    limit: int = Field(default=20, ge=1, le=50)


class SaveDraft(Strict):
    campaign_id: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=50000)
    expected_version: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.:-]+$')
    cast: list[SceneCharacter] = Field(default_factory=list, max_length=200)


class ProposeRecord(Strict):
    campaign_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=240)
    record: RecordInput
    expected_version: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.:-]+$')
    operation: Literal['create', 'replace'] = 'create'
    target_id: str | None = Field(default=None, max_length=100)


class SavePlayerMessage(Strict):
    campaign_id: str = Field(min_length=1, max_length=100)
    protagonist_id: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=50000)
    expected_version: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.:-]+$')


class ReviewIssues(Strict):
    proposal_id: str = Field(min_length=1, max_length=100)


class RecordSchema(Strict):
    kind: Kind


# Function references are fixed at import time. Request input cannot select any
# Engine or Python attribute beyond this explicit set of draft/read operations.
TOOLS = {
    'list_campaigns': (ListCampaigns, NarrativeBridge.list_campaigns,
        'Ver mis campañas', 'Busca la campaña que quieres retomar en el universo autorizado.', True),
    'read_campaign_context': (CampaignContext, NarrativeBridge.read_campaign_context,
        'Consultar la continuidad', 'Relee antes de CADA escena. Los anclajes conservan identidades y personalidad; character solo devuelve el conocimiento del protagonista.', True),
    'search_memory': (SearchMemory, NarrativeBridge.search_memory,
        'Buscar en la memoria', 'Busca antes de negar un recuerdo. Usa las fuentes como evidencia; semejanza no significa identidad ni causa confirmada.', True),
    'read_memory': (ReadMemory, NarrativeBridge.read_memory,
        'Leer un recuerdo', 'Lee un resultado por origin e id. character requiere campaign_id. narrator incluye archivo privado y borradores; no los reveles automáticamente.', True),
    'recent_turns': (RecentTurns, NarrativeBridge.recent_turns,
        'Retomar la conversación', 'Solo para el narrador del propietario: la conversación privada puede contener secretos y borradores, y no es canon ni conocimiento del personaje.', True),
    'save_narrator_draft': (SaveDraft, NarrativeBridge.save_narrator_draft,
        'Guardar narración para revisar', 'Guarda tu texto exacto y cast tras leer contexto. Reutiliza request_id solo para el mismo texto, elenco y versión. Crea un borrador, nunca canon ni decisiones del usuario.', False),
    'save_player_message': (SavePlayerMessage, NarrativeBridge.save_player_message,
        'Guardar el mensaje del jugador', 'Conserva el mensaje exacto del usuario sin resumir, inventar ni completar sus palabras. Relee campaña, protagonista y versión. Queda pendiente de atribución: solo el jugador puede confirmarlo en Umbral. Nunca autoriza acciones ni aprueba canon. Reutiliza request_id solo para el mismo mensaje, protagonista y versión.', False),
    'propose_record': (ProposeRecord, NarrativeBridge.propose_record,
        'Proponer un cambio de continuidad', 'Propón un registro para revisión humana. Consulta record_schema y preserva fuentes, IDs e incertidumbre. Nunca aprueba canon.', False),
    'review_issues': (ReviewIssues, NarrativeBridge.review_issues,
        'Comprobar un borrador', 'Consulta contradicciones y versiones obsoletas sin aprobar ni modificar el borrador.', True),
    'record_schema': (RecordSchema, None,
        'Consultar el formato de un registro', 'Consulta los campos de un registro; deja vacía la información desconocida, nunca la inventes.', True),
}


def catalog():
    return dict(instructions=INSTRUCTIONS, tools=[dict(
        name=name, title=title, description=description, inputSchema=model.model_json_schema(),
        annotations=dict(readOnlyHint=read_only, destructiveHint=False,
                         idempotentHint=True, openWorldHint=False))
        for name, (model, _, title, description, read_only) in TOOLS.items()])


class RemoteBridge:
    def __init__(self, engine, enabled):
        self.engine, self.enabled = engine, enabled

    def require_enabled(self):
        if not self.enabled:
            raise DomainError(404, 'El puente remoto requiere una instalación privada HTTPS.')

    def create_grant(self, body: GrantInput):
        self.require_enabled()
        secret = secrets.token_urlsafe(32)  # 256 random bits, never stored in plaintext.
        identity, created = uid(), _utc()
        with self.engine.db.session(True) as session:
            self.engine._universe(session, body.universe_id)
            grant = BridgeGrant(id=identity, universe_id=body.universe_id, label=body.label,
                token_hash=hashlib.sha256(secret.encode('ascii')).hexdigest(),
                created_at=created.isoformat(), expires_at=(created + timedelta(days=body.expires_in_days)).isoformat())
            session.add(grant)
            session.flush()
            return dict(grant=grant_metadata(grant), token='umbral_bg_' + identity + '.' + secret)

    def list_grants(self):
        self.require_enabled()
        with self.engine.db.session() as session:
            return dict(grants=[grant_metadata(row) for row in session.scalars(
                select(BridgeGrant).order_by(BridgeGrant.created_at, BridgeGrant.id))])

    def revoke(self, identity):
        self.require_enabled()
        with self.engine.db.session(True) as session:
            grant = session.get(BridgeGrant, identity)
            if not grant:
                raise DomainError(404, 'Conexión inexistente.')
            if not grant.revoked_at:
                grant.revoked_at = _utc().isoformat()
            return dict(grant=grant_metadata(grant))

    def authenticate(self, authorization):
        self.require_enabled()
        match = TOKEN_PATTERN.fullmatch(authorization or '')
        if not match:
            raise DomainError(401, AUTH_ERROR)
        identity, secret = match.groups()
        digest = hashlib.sha256(secret.encode('ascii')).hexdigest()
        with self.engine.db.session() as session:
            grant = session.get(BridgeGrant, identity)
            matches = secrets.compare_digest(digest, grant.token_hash if grant else '0' * 64)
            if not matches or not grant or grant.revoked_at or datetime.fromisoformat(grant.expires_at) <= _utc():
                raise DomainError(401, AUTH_ERROR)
            return grant.universe_id

    def invoke(self, universe_id, call):
        if call.name not in TOOLS:
            raise DomainError(404, 'Herramienta no disponible en este puente.')
        model, operation, *_ = TOOLS[call.name]
        args = model.model_validate(call.arguments)
        if call.name == 'record_schema':
            return dict(kind=args.kind, data_schema=DATA_MODELS[args.kind].model_json_schema())
        # Construct only the fixed universe bridge, never from tool arguments.
        bridge = NarrativeBridge(self.engine, universe_id)
        return operation(bridge, **args.model_dump())


def mount_routes(app, remote):
    @app.get('/api/bridge/grants')
    def grants():
        return remote.list_grants()

    @app.post('/api/bridge/grants')
    def create_grant(body: GrantInput):
        return remote.create_grant(body)

    @app.post('/api/bridge/grants/{identity}/revoke')
    def revoke_grant(identity: str):
        return remote.revoke(identity)

    @app.get('/bridge/tools/list')
    def tools_list():
        return catalog()

    @app.post('/bridge/tools/call')
    async def tools_call(request: Request):
        try:
            body = BridgeCall.model_validate(await request.json())
            return await run_in_threadpool(remote.invoke, request.state.bridge_universe, body)
        except DomainError as error:
            return JSONResponse({'detail': error.detail}, status_code=error.status)
        except (ValidationError, ValueError, TypeError, RecursionError):
            # No echoed arguments: they can contain private sources or secrets.
            return JSONResponse({'detail': 'Argumentos inválidos para esta herramienta.'}, status_code=422)
        except Exception:
            # Driver errors, URLs, filesystem paths and tracebacks stay private.
            return JSONResponse({'detail': 'No se pudo completar la operación. Tu canon no se ha aprobado ni reescrito.'}, status_code=500)
