"""Literal archival retrieval and character-safe projections. No model inference."""
from copy import deepcopy
import json
import unicodedata


def character_projection(records, hero_id):
    known = {r['data']['fact_id']: r['data'] for r in records.values()
             if r['kind'] == 'knowledge' and r['data']['character_id'] == hero_id}
    visible = []
    for r in records.values():
        if r['kind'] in ['fact', 'detail', 'event', 'rule'] and (r['id'] in known or hero_id in r['data'].get('known_by', [])):
            personal = {k: deepcopy(v) for k, v in r['data'].items()
                        if k in ['statement', 'summary', 'certainty', 'day', 'end_day']}
            if r['kind'] == 'detail':
                # Features describe the observed detail. Private meanings belong
                # in separately scoped facts or evidence, never in this field.
                personal['features'] = deepcopy(r['data'].get('features', {}))
            if r['id'] in known:
                stance = known[r['id']]['belief']
                personal['belief'] = stance
                if stance == 'suspects':
                    personal['certainty'] = 'hypothesis'
                elif stance == 'believes':
                    personal['certainty'] = 'interpretation'
            visible.append(dict(id=r['id'], kind=r['kind'], name=r['name'], world_id=r['world_id'], campaign_id=r.get('campaign_id'), data=personal))
        elif r['id'] == hero_id:
            personal = {k: deepcopy(v) for k, v in r['data'].items() if k != 'change_basis'}
            visible.append(dict(id=r['id'], kind='character', name=r['name'], world_id=r['world_id'], campaign_id=r.get('campaign_id'), data=personal))
        elif r['kind'] == 'knowledge' and r['data']['character_id'] == hero_id:
            visible.append(dict(id=r['id'], kind='knowledge', name=r['name'], world_id=r.get('world_id'), campaign_id=r.get('campaign_id'), data=deepcopy(r['data'])))
    return visible


def text_fields(value, prefix=''):
    """Yield original strings separately, so excerpts never invent joins."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield from text_fields(child, f'{prefix}.{key}' if prefix else key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from text_fields(child, f'{prefix}[{index}]')
    elif isinstance(value, str):
        yield prefix, value
    elif value is not None:
        yield prefix, str(value)


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFD', value.casefold()) if not unicodedata.combining(c))


def fragment(value, query):
    """Map normalized matching back to an exact slice of the original text."""
    needle = normalized(query.strip())
    if not needle:
        return value[:240]
    chars, positions = [], []
    for index, char in enumerate(value):
        folded = normalized(char)
        chars.extend(folded)
        positions.extend([index] * len(folded))
    start = ''.join(chars).find(needle)
    if start < 0:
        return None
    left = positions[start]
    right = positions[start + len(needle) - 1] + 1
    return value[max(0, left - 90):min(len(value), max(right + 90, left + 150))]


def entry(origin, identity, title, payload, *, canonical=False, certainty=None,
          status, world_id=None, campaign_id=None, source_id=None, **metadata):
    fields = list(text_fields(payload))
    return dict(origin=origin, id=identity, title=title, canonical=canonical,
                certainty=certainty, status=status, world_id=world_id,
                campaign_id=campaign_id, source_id=source_id,
                **metadata, payload=payload, text='\n'.join(value for _, value in fields), _fields=fields)


def public_entry(item, query='', full=False):
    result = {key: deepcopy(value) for key, value in item.items() if key not in ['_fields', 'payload', 'text']}
    # Prefer original narrative content over IDs or a synthetic display title.
    fields = sorted(item['_fields'], key=lambda pair: 0 if pair[0].split('.')[-1] in ['content', 'statement', 'summary'] else
                    1 if pair[0].split('.')[-1] in ['name', 'title'] else 2)
    fragments = []
    # All literal tokens must occur somewhere in this one entry. No synonym,
    # inferred relation, or match in a different archive entry is introduced.
    for token in query.split() or ['']:
        for field, value in fields:
            found = fragment(value, token)
            if found is not None:
                match = dict(field=field, text=found)
                if match not in fragments:
                    fragments.append(match)
                break
        else:
            return None
    result.update(excerpt=fragments[0]['text'], fragments=fragments)
    if full:
        result.update(text=item['text'], payload=deepcopy(item['payload']))
        result['content'] = item['payload'].get('content') if item['origin'] in ['source', 'turn'] else json.dumps(item['payload'], ensure_ascii=False, indent=2)
        if item['origin'] == 'record' and 'source_ref' in item['payload']:
            result['source_ref'] = deepcopy(item['payload']['source_ref'])
        elif item['origin'] == 'proposal':
            result['source_ref'] = deepcopy(item['payload']['record'].get('source_ref', {}))
    return result
