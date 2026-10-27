"""Fail-closed entrypoint for private HTTPS hosting; local CLI stays offline by default."""
import os
from sqlalchemy.engine import make_url
from .api import create_app
from .security import CloudAccess


def from_environment():
    database = os.getenv('DATABASE_URL', '')
    public_url = os.getenv('NARRATIVE_PUBLIC_URL', '')
    if not public_url and os.getenv('RENDER') == 'true':
        public_url = os.getenv('RENDER_EXTERNAL_URL', '')
    password = os.getenv('NARRATIVE_ACCESS_PASSWORD', '')
    secret = os.getenv('NARRATIVE_SESSION_SECRET', '')
    if not database.startswith(('postgres://', 'postgresql://', 'postgresql+psycopg://')):
        raise ValueError('La nube exige DATABASE_URL PostgreSQL persistente. No se usará SQLite temporal.')
    normalized = database.replace('postgres://', 'postgresql://', 1)
    url = make_url(normalized)
    if url.query.get('sslmode') not in ['require', 'verify-ca', 'verify-full']:
        raise ValueError('La conexión de nube requiere TLS: añade sslmode=require o verificación más estricta.')
    access = CloudAccess.build(public_url, password, secret)
    personal = os.getenv('NARRATIVE_PERSONAL_OSS_PLAN', '')
    if personal not in ('', 'true'):
        raise ValueError('NARRATIVE_PERSONAL_OSS_PLAN solo admite true o ausencia de configuración.')
    if personal:
        if any(os.getenv(key) for key in ('NARRATIVE_AI_BASE_URL', 'NARRATIVE_AI_MODEL', 'NARRATIVE_AI_API_KEY')):
            raise ValueError('El modo personal OSS solo usa el plan autorizado, sin proveedor alternativo.')
        return create_app(database, access=access, self_hosted_plan=True)
    return create_app(database, access=access)
