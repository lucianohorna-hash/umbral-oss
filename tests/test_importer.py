import json
import pytest
from narrative.importer import MAX_BYTES, extract_candidates


def test_plain_text_keeps_exact_source_and_is_private_unknown():
    source = '  Lucien observa el umbral.\nNo sabe adónde lleva.\n\nAlguien habla.\n'
    first, second = extract_candidates('registro.md', source)['candidates']
    assert first['data']['statement'] == '  Lucien observa el umbral.\nNo sabe adónde lleva.'
    assert first['source_ref'] == {'line_start': 1, 'line_end': 2}
    assert second['source_ref'] == {'line_start': 4, 'line_end': 4}
    assert first['data']['certainty'] == 'unknown'
    assert first['data']['visibility'] == 'narrator'
    assert first['data']['known_by'] == []


@pytest.mark.parametrize('marker,certainty', [('hecho','confirmed'),('hipótesis','hypothesis'),('rumor','rumor'),('interpretación','interpretation'),('refutado','refuted'),('otro','unknown')])
def test_only_explicit_markers_classify_candidate(marker, certainty):
    candidate = extract_candidates('registro.txt', f'[{marker}] Un testimonio.')['candidates'][0]
    assert candidate['data']['certainty'] == certainty
    assert 'id' not in candidate
    if certainty == 'confirmed': assert 'validación humana' in candidate['warnings'][0]


def test_natural_language_does_not_upgrade_certainty():
    assert extract_candidates('r.txt','Sin ninguna duda, es un hecho confirmado.')['candidates'][0]['data']['certainty'] == 'unknown'


def test_json_messages_preserve_paths_and_do_not_trust_assistant():
    source = json.dumps({'conversations':[{'messages':[{'role':'assistant','content':'[hecho] La torre existe.'},{'role':'user','content':'Quizá sea una ilusión.'}]}]}, ensure_ascii=False, indent=2)
    result = extract_candidates('r.json',source)
    assert len(result['candidates']) == 2
    assert result['candidates'][0]['source_ref']['path'] == '$.conversations[0].messages[0].content'
    assert 'asistente' in result['candidates'][0]['warnings'][0]
    assert result['candidates'][1]['data']['certainty'] == 'unknown'


def test_json_does_not_import_knowledge_or_public_visibility():
    source = json.dumps({'candidates':[{'data':{'statement':'Un rumor.','certainty':'rumor','known_by':['aren'],'visibility':'public'}}]})
    assert extract_candidates('r.json',source)['candidates'][0]['data'] == {'statement':'Un rumor.','certainty':'rumor','known_by':[],'visibility':'narrator'}


def test_duplicates_and_similar_names_are_suggestions_not_merges():
    result = extract_candidates('r.md','Saren Forrest Hill observa.\n\nSaren Forrest Hill observa.',['Sáren Forrest Hill'])
    assert len(result['candidates']) == 2
    assert 'duplicado' in result['candidates'][1]['warnings'][0]
    assert result['name_matches'][0]['observed_name'] == 'Saren Forrest Hill'
    assert result['name_matches'][0]['suggestion'] == 'review_only'


@pytest.mark.parametrize('filename,source',[('a.json','{'),('a.py','print(1)'),('a.txt','a'*(MAX_BYTES+1))], ids=['invalid_json', 'unsupported_extension', 'oversize'])
def test_invalid_inputs_fail(filename,source):
    with pytest.raises(ValueError): extract_candidates(filename,source)


def test_candidate_limit_is_explicit():
    result = extract_candidates('r.md','\n\n'.join(f'Párrafo {i}.' for i in range(501)))
    assert len(result['candidates']) == 500
    assert any('límite de 500' in warning for warning in result['warnings'])


def test_unknown_format_does_not_invent_entities():
    assert extract_candidates('r.json','{"characters":["Thael"]}')['candidates'] == []


def test_json_depth_is_bounded():
    source = '{"messages":"contenido"}'
    for _ in range(32): source = '{"conversations":['+source+']}'
    with pytest.raises(ValueError,match='anidación'): extract_candidates('r.json',source)


def test_invalid_certainty_type_does_not_crash():
    candidate = extract_candidates('r.json','{"statement":"Testimonio","certainty":{"value":"confirmed"}}')['candidates'][0]
    assert candidate['data']['certainty'] == 'unknown'
    assert candidate['warnings']
