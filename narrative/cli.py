import argparse
from pathlib import Path
import sqlite3
import shutil
import uvicorn
from .db import Database, Universe
from .service import Engine
from .schemas import Wizard, RecordInput, ProposalInput, Acceptance, TurnInput


def seed_demo(db):
    engine = Engine(db)
    if engine.universes():
        raise ValueError('La demostración requiere una base vacía. No se mezclará con datos existentes.')
    setup = engine.wizard(Wizard(universe_name='DEMOSTRACIÓN FICTICIA · Las Islas de Papel', campaign_name='DEMO · El puerto quieto', world_name='DEMO · Liria', mode='defined', protagonist_name='DEMO · Irea', premise='Ejemplo ficticio para practicar continuidad. No pertenece al canon real.', personality='Curiosa y prudente.'))
    universe_id = setup['universe_id']
    with db.session(True) as session:
        session.get(Universe, universe_id).is_demo = True
    def add(kind, name, data):
        proposal = engine.propose(universe_id, ProposalInput(title='Ejemplo ficticio: ' + name, record=RecordInput(kind=kind, name=name, world_id=setup['world_id'], data=data)))
        return engine.accept(proposal['id'], Acceptance(expected_version=engine.snapshot(universe_id)['universe']['version'], decision='Datos de demostración ficticios solicitados; separados del canon real.', acknowledge_warnings=True))['record']
    harbor = add('place', 'DEMO · Puerto de Tinta', {'description': 'Lugar ficticio del ejemplo.'})
    npc = add('character', 'DEMO · Nelo', {'location_id': harbor['id'], 'routine': 'Mantiene las redes de pesca según su rutina establecida.', 'life': {'profession': 'Pescador'}, 'goals': ['Reparar su bote'], 'psychology': {'mood': 'Concentrado'}})
    add('relationship', 'DEMO · Nelo hacia Irea', {'from_id': npc['id'], 'to_id': setup['protagonist_id'], 'dimensions': {'confianza': 'Cautelosa', 'familiaridad': 'Conocidos del puerto'}, 'explanation': 'Declaración ficticia inicial de la demostración.'})
    add('fact', 'DEMO · Rumor del faro', {'statement': 'Se rumorea que el faro cambia de color.', 'certainty': 'rumor', 'known_by': [setup['protagonist_id']]})
    add('fact', 'DEMO · Proyecto privado', {'statement': 'Nelo quiere reparar su bote antes del próximo viaje.', 'certainty': 'confirmed', 'known_by': [npc['id']], 'visibility': 'narrator'})
    engine.add_turn(setup['campaign_id'], TurnInput(role='narrator', content='ESCENA FICTICIA PROPUESTA: Irea llega al puerto. Puede observar, conversar o decidir otro rumbo. ¿Qué decide hacer?'))
    return setup


def main():
    parser = argparse.ArgumentParser(description='Motor local de universos narrativos persistentes')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ['serve', 'demo', 'backup', 'restore', 'export']:
        command = sub.add_parser(name)
        command.add_argument('--db', default='data/demo.db' if name == 'demo' else 'data/canon.db')
        if name == 'serve':
            command.add_argument('--port', type=int, default=8000)
            command.add_argument('--chatgpt', action='store_true', help='Narrador con autorización personal local del plan ChatGPT')
        if name in ['backup', 'export']:
            command.add_argument('--output', required=True)
        if name == 'export':
            command.add_argument('--universe', required=True)
        if name == 'restore':
            command.add_argument('--from-backup', required=True)
    args = parser.parse_args()
    if args.command == 'restore':
        target, source = Path(args.db).resolve(), Path(args.from_backup).resolve()
        if target.exists() or not source.is_file():
            parser.error('La restauración requiere una copia existente y un destino nuevo. Detén el servidor antes de restaurar.')
        uri = source.as_uri() + '?mode=ro'
        with sqlite3.connect(uri, uri=True) as connection:
            if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                parser.error('Copia dañada.')
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'universes', 'records', 'revisions', 'alembic_version'}.issubset(tables):
                parser.error('El archivo no es una copia de Multimundo.')
            target.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(target) as output:
                connection.backup(output)
        print('Copia restaurada en', target)
        return
    if args.command == 'serve':
        from .api import create_app
        provider = None
        if args.chatgpt:
            from .chatgpt_plan import ChatGPTPlanNarrator
            provider = ChatGPTPlanNarrator(Path(args.db).resolve().parent / 'chatgpt-plan')
        uvicorn.run(create_app(args.db, provider=provider), host='127.0.0.1', port=args.port, log_level='info', access_log=not args.chatgpt)
        return
    db = Database(args.db)
    if args.command == 'demo':
        print(seed_demo(db))
        print('Demostración creada. Ejecuta: python -m narrative.cli serve --db', args.db)
    elif args.command == 'backup':
        print(db.backup(args.output))
    elif args.command == 'export':
        output = Path(args.output)
        if output.exists():
            parser.error('El archivo de salida ya existe; elige otro nombre.')
        output.write_text(Engine(db).export_markdown(args.universe), encoding='utf-8')


if __name__ == '__main__':
    main()
