"""Pure, deterministic continuity checks and projected effects; no database or model calls."""
from copy import deepcopy
from .identity import check_character, check_cast


def check(record, records, turns=None, target_id=None):
    state = deepcopy(records)
    issues, effects = [], {}
    data, kind = record['data'], record['kind']
    turns = turns or {}

    def issue(code, message, severity='error', suggestion='Corrige el borrador o aporta una corrección explícita del canon.'):
        issues.append(dict(code=code, message=message, severity=severity, suggestion=suggestion))

    def ref(identity, expected=None, required=False):
        if not identity:
            if required:
                issue('missing_reference', 'Falta una referencia necesaria.')
            return None
        result = state.get(identity)
        if not result or (expected and result['kind'] not in expected):
            issue('invalid_reference', f'Referencia inexistente, incompatible o de otro universo: {identity}.')
            return None
        return result

    def changed(identity):
        effects[identity] = state[identity]

    world = ref(record.get('world_id'), ['world'])
    campaign = ref(record.get('campaign_id'), ['campaign'])
    if campaign and world and campaign['world_id'] != record['world_id']:
        issue('campaign_world', 'La campaña y el registro pertenecen a mundos distintos.')
    if data.get('end_day') is not None and data.get('day') is not None and data['end_day'] < data['day']:
        issue('time_interval', 'El intervalo termina antes de comenzar.')
    for identity in data.get('known_by', []) + data.get('participants', []):
        ref(identity, ['character'])
    for identity in data.get('evidence_ids', []) + data.get('contradicts', []):
        ref(identity, ['fact', 'detail', 'event', 'rule'])
    if kind == 'detail':
        for field, expected in [('place_id', ['place']), ('item_id', ['item'])]:
            linked = ref(data.get(field), expected)
            if linked and linked.get('world_id') != record.get('world_id'):
                issue('detail_world', 'El lugar u objeto del detalle pertenece a otro mundo.')
        for identity in data.get('known_by', []) + data.get('participants', []):
            linked = state.get(identity)
            if linked and linked.get('world_id') != record.get('world_id'):
                issue('detail_world', 'Los personajes del detalle deben pertenecer a su mundo.')
        for connection in data['connections']:
            linked = ref(connection['target_id'], ['detail'], True)
            if target_id and connection['target_id'] == target_id:
                issue('detail_self_connection', 'Un detalle no puede conectarse consigo mismo.')
            evidence = [ref(identity, ['fact', 'detail', 'event', 'rule'], True) for identity in connection['evidence_ids']]
            if connection['certainty'] == 'confirmed':
                # Relevance is explicit structured provenance, never similarity
                # of names, tags or features. Both persisted detail IDs must be
                # cited by a confirmed canonical evidence record.
                def references(value, identity):
                    if isinstance(value, dict):
                        return any(references(v, identity) for v in value.values())
                    if isinstance(value, list):
                        return any(references(v, identity) for v in value)
                    return value == identity
                relevant = any(e and e['data'].get('certainty') == 'confirmed' and target_id and
                               e['id'] not in [target_id, connection['target_id']] and
                               references([e['data'], e.get('source_ref', {})], target_id) and
                               references([e['data'], e.get('source_ref', {})], connection['target_id']) for e in evidence)
                if not linked or not relevant:
                    issue('detail_connection_evidence', 'Confirmar una conexión requiere evidencia canónica confirmada que cite explícitamente ambos detalles; un parecido no prueba identidad.')
    if kind == 'campaign':
        protagonist = ref(data['protagonist_id'], ['character'], True)
        if protagonist and world and protagonist['world_id'] != record['world_id']:
            issue('protagonist_world', 'El protagonista debe estar en el mundo de esta campaña.')
    if kind == 'character':
        check_character(record, records, target_id, issue)
        location = ref(data.get('location_id'), ['place'])
        if location and location['world_id'] != record.get('world_id'):
            issue('location_world', 'La ubicación del personaje pertenece a otro mundo.')
        if target_id and target_id in records:
            old = records[target_id]['data']
            for field in ['personality', 'psychology', 'injuries', 'life']:
                if old.get(field) and old.get(field) != data.get(field):
                    issue('persistent_character_state', f'Se modifica {field}: documenta la causa; las consecuencias no se reinician entre escenas.', 'warning')
            if old.get('age') is not None and data.get('age') is not None and data['age'] < old['age']:
                issue('age_correction', 'La edad disminuye. Debe tratarse como corrección explícita y justificada.', 'warning')
        if data.get('born_day') is not None and world and data['born_day'] > world['data']['current_day']:
            issue('birth_future', 'El nacimiento está después del presente local.')
    if kind == 'place':
        parent = ref(data.get('parent_id'), ['place'])
        if parent and parent['world_id'] != record.get('world_id'):
            issue('place_world', 'El lugar padre pertenece a otro mundo.')
        seen = {target_id} if target_id else set()
        while parent:
            if parent['id'] in seen:
                issue('place_cycle', 'La jerarquía de lugares contiene un ciclo.')
                break
            seen.add(parent['id'])
            parent = state.get(parent['data'].get('parent_id'))
    if kind == 'item':
        owner = ref(data.get('owner_id'), ['character', 'place', 'faction'])
        if owner and owner['world_id'] != record.get('world_id'):
            issue('owner_world', 'El objeto y su poseedor deben estar en el mismo mundo.')
        if world and data.get('created_day') is not None and data['created_day'] > world['data']['current_day']:
            issue('item_future', 'La fecha de fabricación está en el futuro local.')
    if kind == 'relationship':
        ref(data['from_id'], ['character'], True)
        ref(data['to_id'], ['character'], True)
        if data['from_id'] == data['to_id']:
            issue('self_relationship', 'Una relación dirigida requiere dos personajes distintos.')
        for r in records.values():
            if r['id'] != target_id and r['kind'] == 'relationship' and (r['data']['from_id'], r['data']['to_id']) == (data['from_id'], data['to_id']):
                issue('duplicate_relationship', 'Ya existe esta relación dirigida: propón una modificación.')
        if target_id and not data.get('explanation') and not data.get('evidence_ids'):
            issue('relationship_evidence', 'Un cambio de relación necesita una explicación o evidencias.')
    if kind == 'knowledge':
        ref(data['character_id'], ['character'], True)
        fact = ref(data['fact_id'], ['fact', 'detail', 'event', 'rule'], True)
        if not data['learned_from'].strip():
            issue('knowledge_source', 'Indica cómo obtuvo esta información el personaje.')
        if fact and data['belief'] == 'knows' and fact['data'].get('certainty') != 'confirmed':
            issue('belief_not_fact', 'Una creencia o rumor no puede registrarse como conocimiento confirmado.')
        if world and data.get('acquired_day') is not None and data['acquired_day'] > world['data']['current_day']:
            issue('future_knowledge', 'El conocimiento aún no puede adquirirse en una fecha futura.')
        for other in records.values():
            if other['id'] != target_id and other['kind'] == 'knowledge' and (other['data']['character_id'], other['data']['fact_id']) == (data['character_id'], data['fact_id']):
                issue('duplicate_knowledge', 'Ya existe una postura sobre esta información: propón una modificación con su nueva fuente.')
    if kind == 'faction':
        for identity in data['members']:
            ref(identity, ['character'])
    if kind == 'temporal_link':
        ref(data['from_world_id'], ['world'], True)
        ref(data['to_world_id'], ['world'], True)
    if kind in ['fact', 'detail', 'event']:
        for identity in data.get('contradicts', []):
            other = state.get(identity)
            if data.get('certainty') == 'confirmed' and other and other['data'].get('certainty') == 'confirmed':
                issue('confirmed_contradiction', 'El borrador contradice un hecho confirmado. Resuelve primero la contradicción explícita.')
    if target_id and target_id in records:
        old = records[target_id]
        if old['kind'] != kind:
            issue('record_kind', 'Una corrección no puede cambiar el tipo del registro.')
        if old['world_id'] != record.get('world_id'):
            issue('world_reassignment', 'Cambiar el mundo de un registro requiere reconciliar viajes, objetos y dependencias. El MVP no realiza este traslado mediante una corrección aislada.')
        issue('explicit_correction', 'Se reemplazará un registro validado; la decisión quedará como corrección del usuario.', 'warning')
        if old['kind'] == 'event' and (old['data'].get('actions') or old['data'].get('simulation')):
            issue('event_effects_correction', 'Este evento produjo efectos. Revierte la última revisión si corresponde y crea un nuevo borrador; no se pueden reaplicar sus efectos.')
    if kind != 'event':
        return issues, effects

    day = data.get('day')
    check_cast(record, records, issue)
    for predecessor_id in data['predecessors']:
        previous = ref(predecessor_id, ['event'], True)
        if predecessor_id == target_id:
            issue('causal_cycle', 'Un acontecimiento no puede causarse a sí mismo.')
        if previous and previous['world_id'] == record.get('world_id'):
            end = previous['data'].get('end_day') if previous['data'].get('end_day') is not None else previous['data'].get('day')
            if end is not None and day is not None and end > day:
                issue('causal_order', 'El acontecimiento ocurre antes que su causa.')
        if target_id:
            visited, pending = set(), [predecessor_id]
            while pending:
                current = pending.pop()
                if current == target_id:
                    issue('causal_cycle', 'La relación causal crea un ciclo.')
                    break
                if current in visited:
                    continue
                visited.add(current)
                pending.extend(state.get(current, {}).get('data', {}).get('predecessors', []))
    actions = data['actions']
    issue('semantic_review', 'Revisa el sentido completo de la escena: nombres, personalidad, edades y hechos anteriores. Las comprobaciones automáticas cubren las reglas implementadas sobre datos estructurados.', 'warning')
    if actions and data['certainty'] != 'confirmed':
        issue('uncertain_effects', 'Un rumor, hipótesis o resultado desconocido no puede modificar el estado material.')
    if actions and (day is None or not world):
        issue('undated_effects', 'Los efectos requieren mundo y día local explícitos.')
    if actions and world and day is not None and day < world['data']['current_day']:
        issue('retroactive_effects', 'No se aplican efectos retroactivos sobre el presente. Importa el pasado sin efectos y revisa su estado final.')
    if actions and data['development'] in ['undeveloped', 'deferred']:
        issue('deferred_effects', 'Un resultado todavía no desarrollado no puede tener efectos confirmados.')
    for identity in data['participants']:
        character = state.get(identity)
        if character and day is not None and character['world_id'] == record.get('world_id'):
            if character['data'].get('born_day') is not None and day < character['data']['born_day']:
                issue('before_birth', 'Un participante aún no había nacido.')

    def knows(character_id, fact_id):
        fact = state.get(fact_id)
        if fact and character_id in fact['data'].get('known_by', []):
            return True
        return any(r['kind'] == 'knowledge' and r['data']['character_id'] == character_id and r['data']['fact_id'] == fact_id for r in state.values())

    protagonist_ids = {r['data']['protagonist_id'] for r in state.values() if r['kind'] == 'campaign'}
    protagonist_ids.update(r['id'] for r in state.values() if r['kind'] == 'character' and r['data']['player_controlled'])

    def check_actor(actor):
        if not actor or actor['kind'] != 'character':
            return
        if world and actor['world_id'] != record['world_id']:
            issue('actor_world', 'El actor no está en el mundo del acontecimiento.')
        born = actor['data'].get('born_day')
        if day is not None and born is not None and actor['world_id'] == record.get('world_id') and day < born:
            issue('before_birth', 'Un actor aún no había nacido; omitirlo de los participantes no permite esta acción.')

    def holder_location(holder):
        if holder['kind'] == 'place':
            return holder['id']
        return holder['data'].get('location_id')

    for action in actions:
        typ = action['type']
        character = ref(action.get('character_id'), ['character'], typ not in ['transfer', 'interaction'])
        check_actor(character)
        if typ == 'move':
            destination = ref(action.get('to_id'), ['place'], True)
            if character and destination:
                if character['data'].get('location_id') != action.get('from_id'):
                    issue('location_mismatch', 'El personaje no está en el lugar de partida indicado.')
                elif destination['world_id'] != record.get('world_id'):
                    issue('interworld_travel', 'El viaje intermundos necesita reglas explícitas; el MVP no deduce equivalencias ni traslados.')
                else:
                    character['data']['location_id'] = destination['id']
                    changed(character['id'])
        elif typ == 'transfer':
            item = ref(action.get('item_id'), ['item'], True)
            recipient = ref(action.get('to_id'), ['character', 'place', 'faction'], True)
            sender = ref(action.get('from_id'), ['character', 'place', 'faction'], True)
            if item and recipient and sender:
                check_actor(sender)
                check_actor(recipient)
                sender_location, recipient_location = holder_location(sender), holder_location(recipient)
                if item['data']['owner_id'] != sender['id']:
                    issue('ownership', 'El objeto ya no pertenece a quien intenta entregarlo.')
                elif action.get('quantity') not in [None, item['data']['quantity']]:
                    issue('partial_transfer', 'Divide primero el lote mediante una corrección explícita; este MVP transfiere lotes completos.')
                elif recipient['world_id'] != sender['world_id'] or (world and sender['world_id'] != record['world_id']):
                    issue('transfer_world', 'No se puede entregar un objeto entre mundos sin un tránsito registrado.')
                elif sender_location and recipient_location and sender_location != recipient_location:
                    issue('transfer_location', 'El objeto no puede entregarse entre ubicaciones incompatibles; registra primero el traslado.')
                else:
                    item['data']['owner_id'] = recipient['id']
                    changed(item['id'])
        elif typ == 'spend' and character:
            amount = action.get('amount')
            if not amount:
                issue('missing_amount', 'Indica un importe positivo en unidades mínimas de la moneda.')
            elif character['data']['balance'] < amount:
                issue('insufficient_funds', 'Los fondos disponibles son insuficientes.')
            else:
                character['data']['balance'] -= amount
                changed(character['id'])
        elif typ in ['mention', 'reveal']:
            fact = ref(action.get('fact_id'), ['fact', 'detail', 'event', 'rule'], True)
            if character and fact:
                if typ == 'mention' and not knows(character['id'], fact['id']):
                    issue('unknown_secret', 'El personaje menciona información que nunca adquirió.')
                if typ == 'reveal':
                    if not action['learned_from'].strip():
                        issue('knowledge_source', 'La revelación necesita un medio narrativo o fuente.')
                    elif action.get('from_id') and not knows(action['from_id'], fact['id']):
                        issue('uninformed_source', 'La persona que revela la información tampoco la conoce.')
                    else:
                        # Access conveys the original certainty; it never promotes a rumor.
                        fact['data']['known_by'] = sorted(set(fact['data'].get('known_by', []) + [character['id']]))
                        changed(fact['id'])
        elif typ == 'interaction':
            a = ref(action.get('from_id'), ['character'], True)
            b = ref(action.get('to_id'), ['character'], True)
            check_actor(a)
            check_actor(b)
            if a and b and action['as_strangers'] and any(r['kind'] == 'relationship' and {r['data']['from_id'], r['data']['to_id']} == {a['id'], b['id']} for r in state.values()):
                issue('forgotten_relationship', 'Se describe como desconocidos a personajes con una relación registrada.')
            if a and b and (a['world_id'] != b['world_id'] or (a['data']['location_id'] and b['data']['location_id'] and a['data']['location_id'] != b['data']['location_id'])):
                issue('interaction_location', 'Los personajes están en ubicaciones incompatibles para esta interacción presencial.')
        elif typ == 'emotion' and character:
            if not action['reason'].strip():
                issue('emotion_cause', 'Un cambio emocional necesita una causa establecida.')
            else:
                projected = deepcopy(character)
                projected['data']['psychology'].update(action['changes'])
                # Each action needs its own justification. An earlier accepted
                # change cannot authorize a later overwrite implicitly.
                projected['data']['change_basis'] = action.get('change_basis')
                check_character(projected, records, character['id'], issue)
                if projected['data']['psychology'] != character['data']['psychology']:
                    character['data']['psychology'] = projected['data']['psychology']
                    character['data']['change_basis'] = projected['data']['change_basis']
                changed(character['id'])
        elif typ == 'injury' and character:
            if not action['injury'].strip():
                issue('injury_detail', 'Describe la lesión o consecuencia persistente.')
            else:
                character['data']['injuries'].append(action['injury'])
                changed(character['id'])
        elif typ == 'ability' and character:
            if action['ability'] not in character['data']['capabilities']:
                issue('unestablished_ability', 'El personaje no tiene esta capacidad establecida.')
            for rule in state.values():
                if rule['kind'] == 'rule' and rule['world_id'] == record.get('world_id') and rule['data']['certainty'] == 'confirmed' and action['ability'] in rule['data']['forbidden_abilities']:
                    issue('world_rule', 'La capacidad infringe una regla confirmada del mundo.')
        elif typ == 'use_item' and character:
            item = ref(action.get('item_id'), ['item'], True)
            if item:
                if item['data']['owner_id'] != character['id'] or item['data']['quantity'] < 1:
                    issue('item_unavailable', 'El personaje no dispone de este objeto.')
                created = item['data'].get('created_day')
                if day is not None and created is not None and day - (action.get('claimed_days') or 0) < created:
                    issue('item_age', 'El uso declarado del objeto comienza antes de su fabricación.')
        elif typ == 'protagonist_decision' and character:
            turn = turns.get(action.get('authorized_turn_id'))
            if not turn or turn['role'] != 'user' or turn['protagonist_id'] != character['id'] or turn['campaign_id'] != record.get('campaign_id') or turn['content'] != action['decision']:
                issue('player_agency', 'La decisión debe corresponder exactamente a una intervención del jugador de esta campaña.')
        if character and character['id'] in protagonist_ids and typ in ['move', 'spend', 'ability', 'use_item']:
            turn = turns.get(action.get('authorized_turn_id'))
            if not turn or turn['role'] != 'user' or turn['protagonist_id'] != character['id'] or turn['campaign_id'] != record.get('campaign_id') or turn['content'] != action['decision']:
                issue('player_agency', 'La acción del protagonista requiere una intervención explícita del jugador como autorización.')
        if typ == 'transfer' and action.get('from_id') in protagonist_ids:
            turn = turns.get(action.get('authorized_turn_id'))
            if not turn or turn['role'] != 'user' or turn['protagonist_id'] != action['from_id'] or turn['campaign_id'] != record.get('campaign_id') or turn['content'] != action['decision']:
                issue('player_agency', 'Entregar un objeto del protagonista requiere su decisión explícita.')
    simulation = data.get('simulation')
    if simulation:
        for interval in simulation['intervals']:
            character = ref(interval['character_id'], ['character'], True)
            if character:
                if character['world_id'] != record.get('world_id') or character['id'] in protagonist_ids:
                    issue('simulation_actor', 'El avance fuera de escena solo desarrolla NPC de este mundo.')
                if interval['development'] == 'routine' and (not character['data']['routine'] or interval['summary'] != character['data']['routine']):
                    issue('unestablished_routine', 'La simulación no puede inventar rutinas que no estén establecidas.')
        if not world or data['certainty'] != 'confirmed' or simulation.get('start_day') != world['data']['current_day'] or not isinstance(simulation.get('end_day'), int) or simulation['end_day'] <= simulation['start_day'] or simulation['end_day'] != day:
            issue('simulation_interval', 'El avance no coincide con el reloj actual del mundo. Genera una nueva previsualización.')
        elif actions:
            issue('simulation_effects', 'La simulación básica solo resume rutinas explícitas; no aplica decisiones ni resultados extraordinarios.')
        else:
            world['data']['current_day'] = simulation['end_day']
            changed(world['id'])
            for r in state.values():
                if r['kind'] == 'campaign' and r['world_id'] == world['id']:
                    r['data']['current_day'] = simulation['end_day']
                    changed(r['id'])
    if actions and world and day is not None and day >= world['data']['current_day']:
        world['data']['current_day'] = day
        changed(world['id'])
        for r in state.values():
            if r['kind'] == 'campaign' and r['world_id'] == world['id']:
                r['data']['current_day'] = day
                changed(r['id'])
    return issues, effects
