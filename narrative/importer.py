"""Offline candidate extraction. No canon writes, semantic inference or name merges."""
from __future__ import annotations
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

MAX_BYTES = 2 * 1024 * 1024
MAX_CANDIDATES = 500
MAX_STATEMENT_CHARS = 16_000
CERTAINTIES = {'unknown', 'hypothesis', 'rumor', 'interpretation', 'refuted', 'confirmed', 'contradiction'}
MARKERS = {'hecho': 'confirmed', 'confirmado': 'confirmed', 'hipótesis': 'hypothesis',
           'hipotesis': 'hypothesis', 'rumor': 'rumor', 'interpretación': 'interpretation',
           'interpretacion': 'interpretation', 'refutado': 'refuted', 'desconocido': 'unknown', 'contradicción': 'contradiction'}
MARKER_RE = re.compile(r'^\s*(?:[-*+]\s+)?\[([^\]]+)\]', re.IGNORECASE)
WORD_RE = re.compile(r"[^\W\d_]+(?:[-’'][^\W\d_]+)*", re.UNICODE)


def _explicit_certainty(statement: str, supplied: Any = None) -> str:
    if isinstance(supplied, str) and supplied in CERTAINTIES:
        return supplied
    marker = MARKER_RE.match(statement)
    return MARKERS.get(marker.group(1).casefold(), 'unknown') if marker else 'unknown'


def _json_entries(value: Any, path: str = '$', depth: int = 0):
    if depth > 30:
        raise ValueError('El JSON supera los 30 niveles de anidación admitidos.')
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _json_entries(item, f'{path}[{index}]', depth + 1)
    elif isinstance(value, dict):
        if isinstance(value.get('content'), str):
            yield value['content'], f'{path}.content', value.get('certainty'), value.get('role')
        elif isinstance(value.get('statement'), str):
            yield value['statement'], f'{path}.statement', value.get('certainty'), None
        elif isinstance(value.get('data'), dict) and isinstance(value['data'].get('statement'), str):
            yield value['data']['statement'], f'{path}.data.statement', value['data'].get('certainty'), None
        else:
            for key in ('messages', 'conversations', 'candidates', 'records'):
                if key in value:
                    yield from _json_entries(value[key], f'{path}.{key}', depth + 1)


def _plain_entries(content: str):
    lines = content.splitlines(keepends=True)
    start = None
    for index, line in enumerate(lines):
        if line.strip():
            if start is None:
                start = index
        elif start is not None:
            yield ''.join(lines[start:index]).rstrip('\r\n'), start + 1, index
            start = None
    if start is not None:
        yield ''.join(lines[start:]).rstrip('\r\n'), start + 1, len(lines)


def _name_suggestions(candidates: list[dict], known_names: list[str]) -> list[dict]:
    names = list(dict.fromkeys(n.strip() for n in known_names if isinstance(n, str) and n.strip()))[:250]
    matches, seen = [], set()
    remaining_windows = 100000
    for candidate_index, candidate in enumerate(candidates):
        tokens = WORD_RE.findall(candidate['data']['statement'])
        for known_name in names:
            width = len(WORD_RE.findall(known_name))
            if not width:
                continue
            for index in range(len(tokens) - width + 1):
                remaining_windows -= 1
                if remaining_windows <= 0:
                    return matches
                if not tokens[index][:1].isupper():
                    continue
                observed = ' '.join(tokens[index:index + width])
                if observed.casefold() == known_name.casefold():
                    continue
                key = observed, known_name
                if key in seen or abs(len(observed) - len(known_name)) > 5:
                    continue
                ratio = SequenceMatcher(None, observed.casefold(), known_name.casefold()).ratio()
                if ratio >= .84:
                    seen.add(key)
                    matches.append({'observed_name': observed, 'known_name': known_name,
                                    'similarity': round(ratio, 3), 'candidate_index': candidate_index,
                                    'suggestion': 'review_only'})
                    if len(matches) >= 100:
                        return matches
    return matches


def extract_candidates(filename: str, content: str, known_names: list[str] | None = None) -> dict:
    """Return review candidates; explicit source labels never validate canon.

    JSON references use the document line range plus the exact JSON value path;
    decoded strings need not correspond to separate physical source lines.
    """
    if not isinstance(content, str):
        raise ValueError('El contenido debe ser texto decodificado como UTF-8.')
    if len(content.encode('utf-8')) > MAX_BYTES:
        raise ValueError('El archivo supera el límite de 2 MiB; divídelo antes de importar.')
    extension = Path(filename).suffix.lower()
    if extension not in {'.txt', '.md', '.markdown', '.json'}:
        raise ValueError('Formato no admitido: usa .txt, .md, .markdown o .json.')
    warnings = ['Extracción estructural sin interpretación semántica: revisa cada candidato. '
                'Las etiquetas de certeza proceden de la fuente y no validan el canon.']
    candidates = []
    if extension == '.json':
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise ValueError('JSON inválido o demasiado anidado; no se importó ningún candidato.') from exc
        total_lines = max(1, len(content.splitlines()))
        entries = ((text, {'line_start': 1, 'line_end': total_lines, 'path': path}, certainty, role)
                   for text, path, certainty, role in _json_entries(parsed))
    else:
        entries = ((text, {'line_start': first, 'line_end': last}, None, None)
                   for text, first, last in _plain_entries(content))
    seen_statements = {}
    for statement, source_ref, supplied_certainty, role in entries:
        if not statement.strip():
            continue
        if len(candidates) == MAX_CANDIDATES:
            warnings.append('Se alcanzó el límite de 500 candidatos. Divide la fuente para revisar el resto.')
            break
        if len(statement) > MAX_STATEMENT_CHARS:
            warnings.append(f'Bloque en {source_ref} omitido: supera 16 000 caracteres. Divídelo en párrafos.')
            continue
        candidate_warnings = []
        if role == 'assistant':
            candidate_warnings.append('Texto atribuido a un asistente: no equivale a un hecho validado.')
        certainty = _explicit_certainty(statement, supplied_certainty)
        if supplied_certainty is not None and (not isinstance(supplied_certainty, str) or supplied_certainty not in CERTAINTIES):
            candidate_warnings.append('Etiqueta de certeza no reconocida; se requiere clasificación humana.')
        if certainty == 'confirmed':
            candidate_warnings.append('La fuente lo etiqueta como hecho; su incorporación aún exige validación humana.')
        key = ' '.join(statement.split()).casefold()
        if key in seen_statements:
            candidate_warnings.append(f'Posible duplicado del candidato {seen_statements[key] + 1}; no se ha fusionado.')
        else:
            seen_statements[key] = len(candidates)
        title = ' '.join(statement.split())[:100]
        candidates.append({'title': title, 'kind': 'fact', 'name': title,
                           'data': {'statement': statement, 'certainty': certainty,
                                    'visibility': 'narrator', 'known_by': []},
                           'source_ref': source_ref, 'warnings': candidate_warnings})
    if not candidates:
        warnings.append('No se encontraron bloques en los formatos admitidos. La fuente no genera hechos automáticamente.')
    if len(known_names or []) > 250:
        warnings.append('Las sugerencias de variantes se limitaron a los primeros 250 nombres conocidos.')
    name_matches = _name_suggestions(candidates, known_names or [])
    if len(name_matches) == 100:
        warnings.append('Se muestran hasta 100 sugerencias de nombres; ninguna se ha fusionado.')
    return {'candidates': candidates, 'name_matches': name_matches, 'warnings': warnings}
