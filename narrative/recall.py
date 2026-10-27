"""Bounded literal recall. Retrieved prose is evidence, never inferred canon."""
import hashlib
import re
from .memory import normalized

STOP = set('para como cuando donde desde hasta sobre entre tengo tiene tienen quiero puedo puede puedes vamos estamos estaba estaban ahora antes luego despues este esta estos estas esto ese esa esos esas aqui alli algo alguna alguno algunos algunas porque pero tambien quien quienes hacer hace decir dice vuelve volver regreso regresar durante nuestro nuestra nosotros ustedes mismo misma mundo personaje personajes'.split())


def prepare_narration_memory(turns, scene_status, *, recent_limit=30, max_items=8, excerpt_limit=1000):
    trusted=[]
    for turn in turns:
        if turn['role'] not in ('user','narrator'):
            continue
        review=scene_status.get(turn['id'],{})
        if turn['role']=='narrator' and review.get('status')=='rejected':
            continue
        trusted.append(turn)
    recent=trusted[-recent_limit:]
    current=next((t['content'] for t in reversed(trusted) if t['role']=='user'),'')
    terms=[]
    for word in re.findall(r'[^\W_]+',normalized(current),re.UNICODE):
        if len(word)>=4 and word not in STOP and word not in terms:
            terms.append(word)
    terms=terms[:24]
    candidates=[]
    for position,turn in enumerate(trusted[:-recent_limit]):
        content=turn['content']
        words=set(re.findall(r'[^\W_]+',normalized(content),re.UNICODE))
        matched=[term for term in terms if term in words]
        if matched:
            candidates.append((len(matched),position,turn,matched))
    candidates.sort(key=lambda row:(-row[0],-row[1]))
    items=[]
    for _,_,turn,matched in candidates[:max_items]:
        content=turn['content']
        # Locate in original text; accents and case never alter the saved quote.
        spans=[match.span() for match in re.finditer(r'[^\W_]+',content,re.UNICODE)
            if normalized(match.group()) in matched]
        start=max(0,spans[0][0]-120)
        end=min(len(content),start+excerpt_limit)
        review=scene_status.get(turn['id'],{})
        items.append(dict(origin='turn',turn_id=turn['id'],campaign_id=turn['campaign_id'],
            source_id=review.get('source_id') or turn.get('provenance',{}).get('source_id'),
            role=turn['role'],review_status=review.get('status','unvalidated'),
            certainty=review.get('certainty','unknown'),canonical=False,
            matched_terms=matched,excerpt=content[start:end],start=start,end=end,
            text_sha256=hashlib.sha256(content.encode()).hexdigest(),
            excerpt_is_partial=start>0 or end<len(content)))
    return recent,dict(items=items,query_terms=terms,candidates=len(candidates),
        omitted=max(0,len(candidates)-len(items)),
        instructions='Referencias literales de conversación, no hechos nuevos. Conserva su certeza y estado. No revelan conocimiento del protagonista. La ausencia de coincidencias no demuestra que algo nunca ocurrió. No inventes conexiones entre semejanzas.')
