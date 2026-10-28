"""Transactional recovery snapshots. Latest state in each of 7 active-hour buckets.
Same database: protects against editing mistakes, not loss of the database itself.
"""
import hashlib
import json
from sqlalchemy import select
from .db import Universe, Source, Record, Proposal, Revision, Turn, RecoveryPoint, uid, now

MODELS = (Source, Record, Proposal, Revision, Turn)


def row_data(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def capture(session, identity):
    universe = session.get(Universe, identity)
    if not universe:
        return
    payload = {'format': 1, 'universe': row_data(universe), 'tables': {}}
    for model in MODELS:
        payload['tables'][model.__tablename__] = [row_data(r) for r in session.scalars(
            select(model).where(model.universe_id == identity).order_by(model.id))]
    stamp = now()
    bucket = stamp[:13]  # UTC hour, not world time. Update latest after every narrative write.
    point = session.scalar(select(RecoveryPoint).where(RecoveryPoint.universe_id == identity, RecoveryPoint.bucket == bucket))
    if point is None:
        point = RecoveryPoint(id=uid(), universe_id=identity, bucket=bucket)
        session.add(point)
    point.created_at, point.version, point.payload, point.sha256 = stamp, universe.version, payload, digest(payload)
    session.flush()
    old = list(session.scalars(select(RecoveryPoint).where(RecoveryPoint.universe_id == identity).order_by(RecoveryPoint.bucket.desc())))[7:]
    for row in old:
        session.delete(row)


def metadata(point):
    return {k: getattr(point, k) for k in ('id', 'universe_id', 'created_at', 'version', 'sha256')}


def restore_as_copy(session, point, label):
    if digest(point.payload) != point.sha256:
        raise ValueError('La copia no supera la comprobación de integridad.')
    payload = point.payload
    rows = [payload['universe']] + [r for items in payload['tables'].values() for r in items]
    mapping = {r['id']: uid() for r in rows}
    literal_fields = {'content','name','title','description','statement','summary','decision','reason','excerpt','routine','origin','relation','explanation','label','aliases','goals','capabilities','injuries'}
    def remap(value):
        if isinstance(value, str):
            return mapping.get(value, value)
        if isinstance(value, list):
            return [remap(v) for v in value]
        if isinstance(value, dict):
            return {mapping.get(k, k): v if k in literal_fields else remap(v) for k, v in value.items()}
        return value
    data = remap(payload['universe'])
    data['name'] = label
    universe = Universe(**data)
    session.add(universe)
    session.flush()
    # Core FK columns inserted with nullable links deferred until all record identities exist.
    for model in MODELS:
        items = [remap(r) for r in payload['tables'][model.__tablename__]]
        if model is Record:
            links = [(r['id'], r['world_id'], r['campaign_id']) for r in items]
            for item in items:
                item['world_id'] = item['campaign_id'] = None
            session.add_all(model(**r) for r in items)
            session.flush()
            for identity, world, campaign in links:
                row = session.get(Record, identity)
                row.world_id, row.campaign_id = world, campaign
        else:
            session.add_all(model(**r) for r in items)
        session.flush()
    # The recovered history is unchanged; provenance of restoration is an additional source.
    content = json.dumps(dict(recovered_from=point.id, original_universe=point.universe_id, sha256=point.sha256, at=now()))
    session.add(Source(id=uid(), universe_id=universe.id, title='Recuperación en universo separado', content=content,
                       sha256=hashlib.sha256(content.encode()).hexdigest()))
    session.flush()
    return universe.id
