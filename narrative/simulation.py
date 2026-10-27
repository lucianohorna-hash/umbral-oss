"""Bounded off-screen simulation policy, independent of storage and HTTP.

Only established routines are summarized. Goals have deferred outcomes and
unselected intervals stay undeveloped. This policy never chooses for a player.
"""


def preview(world, records, days, relevant_ids):
    active = {r['data']['protagonist_id'] for r in records.values() if r['kind'] == 'campaign'}
    relevant = relevant_ids or [r['id'] for r in records.values() if r['kind'] == 'character' and r['world_id'] == world['id'] and r['id'] not in active and not r['data']['player_controlled'] and (r['data']['routine'] or r['data']['goals'])][:200]
    intervals = []
    for identity in dict.fromkeys(relevant):
        character = records.get(identity)
        if not character or character['kind'] != 'character' or character['world_id'] != world['id']:
            raise ValueError('La selección contiene un personaje fuera de este mundo.')
        if identity in active or character['data']['player_controlled']:
            continue
        if character['data']['routine']:
            intervals.append(dict(character_id=identity, development='routine', summary=character['data']['routine']))
        elif character['data']['goals']:
            intervals.append(dict(character_id=identity, development='deferred', summary='Objetivos pendientes; no se presume éxito, fracaso ni decisión.'))
        else:
            intervals.append(dict(character_id=identity, development='undeveloped', summary='Intervalo todavía no desarrollado.'))
    start, end = world['data']['current_day'], world['data']['current_day'] + days
    summary = f'Avance local del día {start} al {end}. '
    summary += ' '.join(f"{records[i['character_id']]['name']}: {i['summary']} ({i['development']})." for i in intervals)
    summary += ' Los personajes no seleccionados conservan intervalos no desarrollados, sin presumir inactividad.'
    return dict(start=start, end=end, summary=summary, simulation=dict(start_day=start, end_day=end, intervals=intervals, policy='explicit-routines-only-v1'))
