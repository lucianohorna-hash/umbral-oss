"""Stable NPC identity and characterization safeguards, independent of persistence.

These checks validate declared data. They are deliberately not a claim of
understanding or proving the meaning of arbitrary narrative prose.
"""
from copy import deepcopy
import unicodedata


def label_key(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def protected_changes(old, proposed):
    fields = []
    if old['name'] != proposed['name']:
        fields.append('nombre')
    for key, title in [('aliases', 'apodos'), ('personality', 'personalidad'),
                       ('psychology', 'estado psicológico'), ('roles', 'papeles establecidos')]:
        # Establishing a previously unknown attribute is different from erasing
        # or replacing an existing one. Both still require normal canon review.
        if old['data'].get(key) and old['data'].get(key) != proposed['data'].get(key):
            fields.append(title)
    return fields


def check_character(record, records, target_id, issue):
    data = record['data']
    seen = set()
    for role in data.get('roles', []):
        location = records.get(role['place_id'])
        if not location or location['kind'] != 'place' or location['world_id'] != record.get('world_id'):
            issue('role_location', 'El papel del personaje necesita un lugar válido de su mismo mundo.')
        key = (role['place_id'], label_key(role['label']))
        if not key[1]:
            issue('role_label', 'El papel del personaje necesita un nombre, por ejemplo recepcionista.')
        if key in seen:
            issue('duplicate_role', 'Este papel está repetido en la ficha del mismo personaje.')
        seen.add(key)
        for other in records.values():
            if other['kind'] != 'character' or other['id'] == target_id:
                continue
            for existing in other['data'].get('roles', []):
                if key == (existing['place_id'], label_key(existing['label'])) and (role['exclusive'] or existing['exclusive']):
                    issue('occupied_role', f'El papel «{role["label"]}» ya corresponde a {other["name"]}. Un nombre nuevo no crea un reemplazo automático.',
                          suggestion='Reutiliza su identificador. Si otra persona ocupa ahora el puesto, documenta y valida primero el relevo.')
    old = records.get(target_id)
    if not old or old['kind'] != 'character':
        return
    fields = protected_changes(old, record)
    if not fields:
        return
    basis = data.get('change_basis')
    if not basis or not basis['reason'].strip():
        issue('character_change_basis', 'Se intenta cambiar ' + ', '.join(fields) + ' sin documentar una corrección o evolución.',
              suggestion='Conserva la ficha o registra un motivo explícito y, si es evolución, acontecimientos causales confirmados.')
        return
    if basis['based_on_revision'] != old['revision']:
        issue('character_change_stale', 'La justificación corresponde a otra revisión del personaje. Revisa su ficha actual.')
    if basis['mode'] == 'evolution':
        if not basis['evidence_ids']:
            issue('character_evolution_evidence', 'La evolución requiere acontecimientos confirmados que involucren a este personaje.')
        for identity in basis['evidence_ids']:
            event = records.get(identity)
            if not event or event['kind'] != 'event' or event['data'].get('certainty') != 'confirmed' or event['data'].get('development') in ['deferred', 'undeveloped']:
                issue('character_evolution_evidence', 'Una hipótesis, un borrador o un resultado pendiente no justifican una evolución establecida.')
                continue
            participants = set(event['data'].get('participants', []))
            participants.update(member['character_id'] for member in event['data'].get('cast', []))
            for action in event['data'].get('actions', []):
                participants.update(action.get(k) for k in ['character_id', 'from_id', 'to_id'] if action.get(k))
            if target_id not in participants:
                issue('character_evolution_actor', 'El acontecimiento citado no involucra al personaje que cambiaría.')
            world = records.get(record.get('world_id'))
            day = event['data'].get('end_day') if event['data'].get('end_day') is not None else event['data'].get('day')
            if not world or event['world_id'] != world['id'] or day is None or day > world['data']['current_day']:
                issue('character_evolution_time', 'La causa debe estar fechada y haber ocurrido en el calendario local del personaje.')
    issue('protected_character_change', 'Cambio protegido de ' + ', '.join(fields) + ': necesita autorización específica del usuario.', 'warning',
          'Contrasta el estado anterior, el nuevo estado y la causa. Reconocer avisos generales no autoriza este cambio.')


def check_cast(record, records, issue):
    seen = set()
    for portrayal in record['data'].get('cast', []):
        character = records.get(portrayal['character_id'])
        if not character or character['kind'] != 'character':
            issue('cast_character', 'La escena menciona un personaje sin identidad registrada en este universo.')
            continue
        if character['id'] in seen:
            issue('cast_duplicate', 'El reparto de una escena contiene el mismo personaje más de una vez.')
        seen.add(character['id'])
        if character['world_id'] != record.get('world_id'):
            issue('cast_world', 'Un personaje del reparto no está en el mundo de la escena.')
        born = character['data'].get('born_day')
        day = record['data'].get('day')
        if day is not None and born is not None and character['world_id'] == record.get('world_id') and day < born:
            issue('before_birth', 'Un personaje del reparto aún no había nacido en la fecha de la escena.')
        established_names = [character['name'], *character['data'].get('aliases', [])]
        if label_key(portrayal['name']) not in {label_key(n) for n in established_names}:
            issue('cast_name', f'El personaje {character["name"]} aparece con el nombre no establecido «{portrayal["name"]}».',
                  suggestion='Usa su nombre o un apodo validado. No inventes otro al retomar la escena.')
        traits = character['data'].get('personality', {})
        for key, value in portrayal['personality'].items():
            if key not in traits or value != traits[key]:
                issue('cast_personality', f'La caracterización de {character["name"]} modifica un rasgo establecido o introduce uno sin validar: {key}.',
                      suggestion='Mantén los rasgos registrados. Una conducta puntual no redefine la personalidad; una evolución debe validarse por separado.')


def narrator_anchors(records):
    """Compact reference sheet refreshed from the canonical database, never chat memory."""
    return [dict(character_id=r['id'], name=r['name'], aliases=deepcopy(r['data'].get('aliases', [])),
                 revision=r['revision'], world_id=r['world_id'], location_id=r['data'].get('location_id'),
                 roles=deepcopy(r['data'].get('roles', [])), personality=deepcopy(r['data'].get('personality', {})),
                 psychology=deepcopy(r['data'].get('psychology', {})))
            for r in records.values() if r['kind'] == 'character']
