from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, create_engine, event, text, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from alembic import command
from alembic.config import Config


def uid():
    return str(uuid.uuid4())


def now():
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class Universe(Base):
    __tablename__ = 'universes'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text, default='')
    version: Mapped[int] = mapped_column(Integer, default=0)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Source(Base):
    __tablename__ = 'sources'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    universe_id: Mapped[str] = mapped_column(ForeignKey('universes.id'), index=True)
    title: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Record(Base):
    __tablename__ = 'records'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    universe_id: Mapped[str] = mapped_column(ForeignKey('universes.id'), index=True)
    kind: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    world_id: Mapped[str | None] = mapped_column(ForeignKey('records.id'), nullable=True, index=True)
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey('records.id'), nullable=True, index=True)
    source_id: Mapped[str | None] = mapped_column(ForeignKey('sources.id'), nullable=True, index=True)
    source_ref: Mapped[dict] = mapped_column(JSON, default=dict)
    data: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String, default=now)
    updated_at: Mapped[str] = mapped_column(String, default=now)


class Proposal(Base):
    __tablename__ = 'proposals'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    universe_id: Mapped[str] = mapped_column(ForeignKey('universes.id'), index=True)
    title: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default='pending')
    operation: Mapped[str] = mapped_column(String)
    target_id: Mapped[str | None] = mapped_column(String, nullable=True)
    record: Mapped[dict] = mapped_column(JSON)
    base_version: Mapped[int] = mapped_column(Integer)
    issues: Mapped[list] = mapped_column(JSON, default=list)
    decision: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[str] = mapped_column(String, default=now)
    updated_at: Mapped[str] = mapped_column(String, default=now)


class Revision(Base):
    __tablename__ = 'revisions'
    __table_args__ = (UniqueConstraint('universe_id', 'version'),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    universe_id: Mapped[str] = mapped_column(ForeignKey('universes.id'), index=True)
    version: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String, default='user')
    proposal_id: Mapped[str | None] = mapped_column(ForeignKey('proposals.id'), nullable=True, index=True)
    before: Mapped[dict] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Turn(Base):
    __tablename__ = 'turns'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    universe_id: Mapped[str] = mapped_column(ForeignKey('universes.id'), index=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey('records.id'), index=True)
    protagonist_id: Mapped[str] = mapped_column(ForeignKey('records.id'), index=True)
    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    proposal_id: Mapped[str | None] = mapped_column(ForeignKey('proposals.id'), nullable=True, index=True)
    canon_version: Mapped[int] = mapped_column(Integer)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String, default=now)


class BridgeGrant(Base):
    """Transport authorization, deliberately excluded from portable canon backups."""
    __tablename__ = 'bridge_grants'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    universe_id: Mapped[str] = mapped_column(ForeignKey('universes.id'), index=True)
    label: Mapped[str] = mapped_column(String)
    token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String, default=now)
    expires_at: Mapped[str] = mapped_column(String)
    revoked_at: Mapped[str | None] = mapped_column(String, nullable=True)


class Database:
    def __init__(self, path: str | Path):
        value = str(path)
        self.is_postgres = value.startswith(('postgres://', 'postgresql://', 'postgresql+psycopg://'))
        self.path = None if self.is_postgres else Path(path).resolve()
        if self.is_postgres:
            url = value.replace('postgres://', 'postgresql+psycopg://', 1).replace('postgresql://', 'postgresql+psycopg://', 1)
            self.engine = create_engine(url, pool_size=3, max_overflow=2, pool_pre_ping=True, connect_args={'prepare_threshold': None})
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.engine = create_engine('sqlite:///' + self.path.as_posix(), connect_args={'check_same_thread': False, 'timeout': 15})
        @event.listens_for(self.engine, 'connect')
        def configure(connection, _):
            if self.is_postgres:
                with connection.cursor() as cursor:
                    cursor.execute('SET search_path TO narrative_core')
                connection.commit()
            else:
                connection.execute('PRAGMA foreign_keys=ON')
                connection.execute('PRAGMA busy_timeout=15000')
                connection.execute('PRAGMA journal_mode=WAL')
        config = Config()
        config.set_main_option('script_location', str(Path(__file__).parent.parent / 'migrations'))
        with self.engine.begin() as connection:
            if self.is_postgres:
                connection.execute(text("SET LOCAL lock_timeout = '15s'"))
                connection.execute(text('SELECT pg_advisory_xact_lock(1160396)'))
                # PostgreSQL checks database-wide CREATE even with IF NOT EXISTS.
                # An app role only needs ownership of its pre-provisioned schema.
                if connection.scalar(text("SELECT to_regnamespace('narrative_core')")) is None:
                    connection.execute(text('CREATE SCHEMA narrative_core'))
                connection.execute(text('REVOKE ALL ON SCHEMA narrative_core FROM PUBLIC'))
            config.attributes['connection'] = connection
            command.upgrade(config, 'head')

    @contextmanager
    def session(self, write=False):
        with Session(self.engine, expire_on_commit=False) as session:
            try:
                if self.is_postgres:
                    if write:
                        session.execute(text("SET LOCAL lock_timeout = '15s'"))
                        session.execute(text('SELECT pg_advisory_xact_lock(1160396)'))
                    else:
                        session.connection(execution_options={'isolation_level': 'REPEATABLE READ'})
                        session.execute(text('SET TRANSACTION READ ONLY'))
                else:
                    session.execute(text('BEGIN IMMEDIATE' if write else 'BEGIN'))
                yield session
                if write:
                    session.commit()
            except Exception:
                session.rollback()
                raise

    def backup(self, destination):
        target = Path(destination).resolve()
        if target == self.path or target.exists():
            raise ValueError('La copia debe usar un archivo nuevo y diferente de la base activa.')
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.is_postgres:
            destination_db = Database(target)
            try:
                with self.session() as source, destination_db.session(True) as output:
                    output.execute(text('PRAGMA defer_foreign_keys=ON'))
                    # Portable canon includes history, never machine grants.
                    for model in [Universe, Source, Record, Proposal, Revision, Turn]:
                        rows = [dict(row) for row in source.execute(select(model.__table__)).mappings()]
                        if rows:
                            output.execute(model.__table__.insert(), rows)
                with destination_db.engine.connect() as connection:
                    connection.execute(text('PRAGMA wal_checkpoint(TRUNCATE)'))
            finally:
                destination_db.engine.dispose()
            with sqlite3.connect(target) as output:
                if output.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or output.execute('PRAGMA foreign_key_check').fetchall():
                    raise ValueError('La copia no superó la verificación de integridad.')
            return target
        with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(target)) as output:
            source.backup(output)
            # A restored canon must never reactivate a connector credential.
            # Sanitize only the destination and erase freed credential pages.
            output.execute('PRAGMA secure_delete=ON')
            output.execute('DELETE FROM bridge_grants')
            output.commit()
            output.execute('VACUUM')
            # The copied header retains WAL mode. Flush the sanitized state into
            # the exported single file before returning it, not just its sidecar.
            output.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            output.execute('PRAGMA journal_mode=DELETE')
            if output.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('La copia no superó la verificación de integridad.')
        return target
