/* Umbral: browser views over the versioned narrative API. No model keys in the browser. */
'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const kinds = {world:'Mundo',campaign:'Campaña',character:'Personaje',place:'Lugar',item:'Objeto',detail:'Detalle recordado',relationship:'Relación',knowledge:'Conocimiento',fact:'Hecho o información',event:'Acontecimiento',rule:'Regla del mundo',faction:'Facción',temporal_link:'Vínculo temporal'};
  const certainty = {confirmed:'Confirmado',hypothesis:'Hipótesis',rumor:'Rumor',interpretation:'Interpretación',refuted:'Refutado',unknown:'Sin determinar',contradiction:'Contradicción pendiente'};
  const labels = {story:'Tu historia',bible:'Memoria del mundo',memory:'Huellas de la historia',characters:'Personajes',timeline:'Cronología',relationships:'Relaciones',review:'Por revisar',import:'Importar historias',history:'Historial y copias'};
  const s = {universes:[],snapshot:null,uid:'',cid:'',turns:[],csrf:'',tab:'story',provider:'manual',cloud:false,loading:false,savingTurn:false};
  let editor = null, actionSubmit = null, wizardRecords = [], sequence = 0, grantsSequence = 0;
  const draftMemory = new Map();
  const memory = {sequence:0, scope:'', offset:0, next:null};
  const read = key => {if(draftMemory.has(key))return draftMemory.get(key);try{return localStorage.getItem('umbral.'+key)||'';}catch(_){return '';}};
  const write = (key,value) => {try{localStorage.setItem('umbral.'+key,value);draftMemory.delete(key);return true;}catch(_){if(key.startsWith('draft.')||key.startsWith('cast.')){draftMemory.set(key,value);notify('El navegador no puede guardar este borrador entre sesiones. Se conservará mientras esta página siga abierta.',true);}else notify('El navegador no pudo guardar este dato.',true);return false;}};
  const remove = key => {try{localStorage.removeItem('umbral.'+key);draftMemory.delete(key);}catch(_){if(key.startsWith('draft.')||key.startsWith('cast.'))draftMemory.set(key,'');}};
  const records = (kind) => (s.snapshot?.records||[]).filter(r=>!kind||r.kind===kind);
  const record = id => records().find(r=>r.id===id);
  const name = id => record(id)?.name || 'Sin establecer';
  const campaign = () => record(s.cid);
  const world = () => record(campaign()?.world_id);
  const version = () => s.snapshot?.universe.version||0;
  const el = (tag,text,cls) => {const n=document.createElement(tag); if(text!==undefined)n.textContent=String(text); if(cls)n.className=cls; return n;};
  function notify(message,error=false){const n=$('notification'); n.textContent=message; n.className='notification'+(error?' error':''); n.hidden=false;}
  function showError(error){
    const dialog=document.querySelector('dialog[open]');
    if(dialog){let n=dialog.querySelector('.dialog-error'); if(!n){n=el('p','','notification error dialog-error'); n.setAttribute('role','alert');dialog.prepend(n);}n.textContent=error.message; n.scrollIntoView({block:'nearest'});}
    else notify(error.message,true);
  }
  function safe(fn){return async function(event){try{await fn(event);}catch(error){showError(error);}};}
  async function busy(button,fn){if(button.disabled)return; button.disabled=true;try{return await fn();}finally{button.disabled=false;}}
  function button(text,fn,cls='text-button'){const n=el('button',text,cls);n.type='button';n.addEventListener('click',safe(()=>busy(n,fn)));return n;}
  function badge(text,cls=''){return el('span',text,'badge '+cls);}
  function options(select,items,value='',blank=null){select.replaceChildren();if(blank!==null)select.append(new Option(blank,''));for(const item of items)select.append(new Option(item.name,item.id));select.value=value;}
  function empty(container,title,text,cta){const n=el('div',undefined,'empty');n.append(el('h3',title),el('p',text));if(cta)n.append(button(cta[0],cta[1],'button primary'));container.append(n);}
  function openDialog(id){const d=$(id);d.querySelectorAll('.dialog-error').forEach(n=>n.remove());d.showModal();}
  function details(title,value){const d=el('details');d.append(el('summary',title),el('pre',typeof value==='string'?value:JSON.stringify(value,null,2)));return d;}
  async function api(path,body){
    let response;
    try{response=await fetch('/api'+path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':s.csrf},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store'});}catch(_){throw Error('No se pudo conectar. Tu texto sigue aquí; comprueba que el archivo esté abierto y vuelve a intentar.');}
    const data=await response.json();
    if(!response.ok){let msg=data.detail||'No se pudo completar la operación.';if(Array.isArray(msg))msg=msg.map(x=>x.loc?.join(' → ')+': '+x.msg).join('\n');else if(typeof msg==='object')msg=msg.message+'\n'+(msg.issues||[]).map(x=>x.message).join('\n');const error=Error(msg);error.status=response.status;throw error;}return data;
  }
  async function bootstrap(preferredU,preferredC){
    const b=await api('/bootstrap');s.csrf=b.csrf_token;s.universes=b.universes;s.provider=b.provider;s.cloud=b.cloud;$('chatgpt-summary').textContent=s.cloud?'El conector privado de Umbral está disponible para universos con acceso autorizado.':'Usa un asistente compatible mediante el puente local stdio, cuando ese puente esté en ejecución.';
    const chosen=preferredU||s.uid||read('universe');s.uid=s.universes.some(u=>u.id===chosen)?chosen:(s.universes[0]?.id||'');
    options($('universe-select'),s.universes,s.uid,s.universes.length?null:'Tu primer universo');
    document.querySelector('.side-footer').replaceChildren(el('span',s.cloud?'● Archivo en la nube':'● Archivo en tu computador'),el('small','Tu mundo conserva su memoria.'));
    if(s.uid)await loadUniverse(s.uid,preferredC||read('campaign.'+s.uid));else{$('welcome').hidden=false;$('workspace').hidden=true;}
  }
  async function loadUniverse(uid,cid){
    const current=++sequence;const snap=await api('/universes/'+uid);if(current!==sequence)return;
    const choices=(snap.records||[]).filter(r=>r.kind==='campaign');
    const nextCid=choices.some(c=>c.id===cid)?cid:(choices[0]?.id||'');
    const nextTurns=nextCid?(await api('/campaigns/'+nextCid+'/turns')).turns:[];
    if(current!==sequence)return;
    saveDraft();
    s.uid=uid;s.snapshot=snap;s.cid=nextCid;s.turns=nextTurns;write('universe',uid);
    options($('campaign-select'),choices,s.cid,choices.length?null:'Crea una campaña');write('campaign.'+uid,s.cid);
    $('welcome').hidden=true;$('workspace').hidden=false;render();restoreDraft();
  }
  async function refresh(){if(s.uid)await loadUniverse(s.uid,s.cid);}
  function navigate(tab){s.tab=tab;document.querySelectorAll('.view').forEach(n=>n.hidden=n.id!=='view-'+tab);document.querySelectorAll('[data-tab]').forEach(n=>n.classList.toggle('active',n.dataset.tab===tab));$('page-title').textContent=labels[tab];if(tab==='memory'&&memory.scope!==memoryScope())searchMemory(0).catch(showError);if(tab==='history')loadChatgptGrants();}
  function stateRow(container,title,value){const n=el('div',undefined,'state-row');n.append(el('dt',title),el('dd',value));container.append(n);}
  function render(){
    $('universe-title').textContent=s.snapshot.universe.name;$('version-label').textContent='Memoria · v'+version();$('demo-label').hidden=!s.snapshot.universe.is_demo;
    const pending=s.snapshot.proposals.filter(p=>p.status==='pending').length;$('pending-count').textContent=pending;
    const strip=$('summary-strip');strip.replaceChildren();for(const [label,value] of [['Protagonista',name(campaign()?.data.protagonist_id)],['Mundo',world()?.name||'Por descubrir'],['Tiempo local',world()?'Día '+world().data.current_day:'Sin establecer'],['Por revisar',pending+' propuestas']]){const n=el('div',undefined,'summary-cell');n.append(el('span',label),el('strong',value));strip.append(n);}
    renderStory();renderRecords();renderCharacters();renderTimeline();renderRelationships();renderProposals();renderHistory();navigate(s.tab);
  }
  function renderStory(){
    const c=campaign();$('campaign-title').textContent=c?.name||'Abre una nueva campaña';$('campaign-subtitle').textContent=c?.data.premise||'Descubre el mundo a tu ritmo. Lo que aún no has definido permanece abierto.';
    for(const id of ['turn-content','turn-role','show-context','switch-protagonist','story-new-event','story-advance'])$(id).disabled=!c;
    let narratorNotice=$('narrator-availability');
    if(!narratorNotice){narratorNotice=el('p',undefined,'story-note');narratorNotice.id='narrator-availability';narratorNotice.setAttribute('role','status');$('turn-form').before(narratorNotice);}
    narratorNotice.hidden=s.provider!=='manual';
    narratorNotice.textContent='Sin narrador automático en esta versión: guardar conserva tu intervención, pero no genera una respuesta. La conexión directa con tu plan ChatGPT aún no está habilitada aquí.';
    $('turn-form').querySelector('[type=submit]').textContent=s.provider==='manual'?'Guardar intervención · sin respuesta automática':'Guardar intervención ↗';
    $('turn-form').querySelector('[type=submit]').disabled=!c||s.savingTurn;$('generate-area').hidden=!c||s.provider==='manual';$('provider-label').textContent=s.provider==='manual'?'Narrador automático no conectado':['chatgpt-plan','chatgpt-plan-self-hosted'].includes(s.provider)?'Plan ChatGPT · conexión en panel':'Narrador conectado';renderCastPicker();
    const list=$('turn-list');list.replaceChildren();
    if(!s.turns.length)empty(list,c?'Tu primera escena está por comenzar':'Una historia te espera',c?'Escribe la primera decisión de tu personaje. Quedará guardada para continuar cuando quieras.':'Crea una campaña nueva o selecciona una existente.',c?['Escribir mi primera decisión',()=>$('turn-content').focus()]:['Crear campaña',openWizard]);
    for(const t of s.turns){const pendingPlayer=t.role==='player_pending';const rejectedPlayer=t.role==='player_rejected';const confirmedChatgpt=t.role==='user'&&t.provenance?.producer==='mcp';const n=el('article',undefined,'turn '+t.role);const header=el('div',undefined,'turn-header');const speaker=pendingPlayer?'ChatGPT · por confirmar':rejectedPlayer?'ChatGPT · atribución rechazada':confirmedChatgpt?name(t.protagonist_id)+' · ChatGPT confirmado':t.role==='user'?name(t.protagonist_id):'Narrador';header.append(el('strong',speaker),el('span',new Date(t.created_at).toLocaleString('es',{dateStyle:'short',timeStyle:'short'})));n.append(header,el('div',t.content,'turn-content'));
      if(pendingPlayer){n.append(badge('Mensaje recibido de ChatGPT · por confirmar','warning'));n.append(button('Sí, este es mi mensaje',()=>reviewPlayerTurn(t,'confirm'),'button primary'),button('No es mi decisión',()=>reviewPlayerTurn(t,'reject'),'text-button'));}
      else if(rejectedPlayer)n.append(badge('Mensaje recibido de ChatGPT · atribución rechazada','warning'));
      else if(confirmedChatgpt)n.append(badge('Origen ChatGPT · confirmado','accepted'));
      if(t.proposal_id){const p=s.snapshot.proposals.find(x=>x.id===t.proposal_id);const revised=p?.status==='rejected'&&p.decision.startsWith('Sustituida por una propuesta corregida:');const validated=s.snapshot.proposals.find(x=>x.status==='accepted'&&x.record.kind==='event'&&x.record.source_ref?.turn_id===t.id);n.append(badge(validated?'Revisión incorporada al archivo':'Borrador · aún no es canon',validated?'accepted':'warning'));n.append(button(p?.status==='accepted'?'Ver registro revisado':revised?'Ver revisión de esta escena':p?.status==='rejected'?'Propuesta descartada':'Revisar esta escena',()=>navigate('review')));}n.append(button('Conservar un detalle',()=>detailFromTurn(t)));list.append(n);}
    const state=$('campaign-state');state.replaceChildren();if(c){stateRow(state,'Tu protagonista',name(c.data.protagonist_id));stateRow(state,'Dónde está',name(record(c.data.protagonist_id)?.data.location_id));stateRow(state,'Estado',record(c.data.protagonist_id)?.data.status||'Sin establecer');stateRow(state,'Forma de descubrir',c.data.mode==='discover'?'Durante la narración':'Con un mundo definido');}
  }
  async function reviewPlayerTurn(turn,decision){
    const uid=s.uid,cid=s.cid;
    const result=await api('/campaigns/'+cid+'/turns/'+turn.id+'/review-player',{
      decision,expected_version:s.snapshot.universe.version,content:turn.content
    });
    await refreshSelectedUniverse(uid);
    if(s.cid===cid){renderStory();notify(decision==='confirm'?'Mensaje confirmado como tu texto exacto.':'Atribución rechazada; el mensaje queda visible como no autorizado.');}
    return result;
  }
  function card(r){const n=el('article',undefined,'record-card');const top=el('div',undefined,'card-top');top.append(badge(kinds[r.kind]));if(r.data.certainty)top.append(badge(certainty[r.data.certainty],r.data.certainty));if(r.data.visibility==='narrator')top.append(badge('Narrador','narrator'));n.append(top,el('h3',r.name));
    const summary=r.data.statement||r.data.summary||r.data.description||r.data.premise||r.data.explanation||r.data.routine||'';if(summary)n.append(el('p',summary.length>500?summary.slice(0,500)+'…':summary));
    n.append(details('Ver ficha completa',r.data),el('div',(r.world_id?name(r.world_id)+' · ':'')+'Revisión '+r.revision,'card-meta'));
    if(r.source_id)n.append(button('Consultar fuente',()=>showSource(r.source_id)));
    n.append(button('Proponer corrección',()=>openEditor(r.kind,r)));return n;
  }
  function renderRecords(){const list=$('record-list');list.replaceChildren();const q=$('record-search').value.toLocaleLowerCase('es');const type=$('record-kind').value;const rows=records(type).filter(r=>(r.name+' '+JSON.stringify(r.data)).toLocaleLowerCase('es').includes(q));for(const r of rows)list.append(card(r));if(!rows.length)empty(list,'Todavía hay espacio por descubrir','Añade solo aquello que quieras establecer o conserva una pregunta como información desconocida.');}
  function renderCharacters(){const list=$('character-list');list.replaceChildren();for(const r of records('character'))list.append(card(r));if(!list.childElementCount)empty(list,'Nuevas vidas por conocer','Puedes crear personajes con objetivos y rutinas propios.');}
  function renderTimeline(){const list=$('timeline-list');list.replaceChildren();const worlds=[...records('world'),{id:null,name:'Sin calendario asignado',data:{}}];for(const w of worlds){const events=records('event').filter(r=>r.world_id===w.id).sort((a,b)=>(a.data.day??Infinity)-(b.data.day??Infinity));if(!events.length&&!w.id)continue;const section=el('section',undefined,'timeline-world');section.append(el('h3',w.name+(w.id?' · día '+w.data.current_day:'')));if(!events.length)empty(section,'Un calendario abierto','Los acontecimientos aparecerán aquí al validarlos.');for(const r of events){const row=el('div',undefined,'timeline-entry');row.append(el('div',r.data.day===null?'Sin fecha':'Día '+r.data.day+(r.data.end_day!==null?' a '+r.data.end_day:''),'timeline-day'),card(r));section.append(row);}list.append(section);}}
  function renderRelationships(){const list=$('relationship-list');list.replaceChildren();for(const r of records('relationship')){const n=el('article',undefined,'record-card');n.append(el('h3',name(r.data.from_id)+' → '+name(r.data.to_id),'relation-route'));for(const [key,value] of Object.entries(r.data.dimensions)){const d=el('div',undefined,'dimension');d.append(el('span',key),el('strong',value));n.append(d);}n.append(el('p',r.data.explanation),details('Evidencias',r.data.evidence_ids.map(name)),button('Proponer evolución',()=>openEditor('relationship',r)));list.append(n);}if(!list.childElementCount)empty(list,'Los vínculos tienen dos miradas','Registra por separado lo que una persona siente hacia otra. No necesitas reducirlo a una puntuación.');}
  function memoryScope(){return [s.uid,s.cid,$('memory-audience').value,version(),s.snapshot?.proposals.map(p=>p.id+':'+p.status+':'+p.updated_at).join(','),s.turns.map(t=>t.id+':'+t.role).join(',')].join('/');}
  function memoryParams(){const audience=$('memory-audience').value;const params=new URLSearchParams({audience});if(audience==='character'&&s.cid)params.set('campaign_id',s.cid);return params;}
  function memoryLabel(hit){
    if(hit.origin==='record')return hit.canonical?'Canon · '+(certainty[hit.certainty]||'Registro vigente'):'Registro histórico · no vigente';
    if(hit.origin==='source')return 'Fuente original · no confirma canon';
    const state={pending:'pendiente',accepted:'revisada',rejected:'descartada',archived:'histórica'}[hit.status]||'sin validar';
    return (hit.origin==='turn'?'Conversación':'Propuesta')+' · '+state;
  }
  async function searchMemory(offset=0){
    if(!s.uid)return;
    const epoch=++memory.sequence,scope=memoryScope(),uid=s.uid;
    memory.scope=scope;memory.offset=offset;memory.next=null;
    $('memory-results').replaceChildren();$('memory-status').textContent='Buscando en lo guardado…';
    $('memory-prev').disabled=true;$('memory-next').disabled=true;
    const params=memoryParams();params.set('q',$('memory-query').value.trim());params.set('offset',offset);params.set('limit',25);
    try{
      const data=await api('/universes/'+uid+'/memory?'+params);
      if(epoch!==memory.sequence||scope!==memoryScope())return;
      memory.next=data.next_offset;
      $('memory-status').textContent=data.total?`${data.total} ${data.total===1?'coincidencia':'coincidencias'} · mostrando ${offset+1}–${offset+data.results.length}`:'No hay coincidencias con esas palabras. Prueba otra descripción; el archivo no deduce sinónimos ni dibujos.';
      for(const hit of data.results){
        const card=el('article',undefined,'record-card memory-card');card.append(badge(memoryLabel(hit),hit.canonical?'accepted':'warning'),el('h3',hit.title),el('p',hit.excerpt,'memory-excerpt'));
        const location=[hit.world_id?name(hit.world_id):'',hit.campaign_id?name(hit.campaign_id):''].filter(Boolean).join(' · ');if(location)card.append(el('p',location,'card-meta'));
        card.append(button('Leer completo y ver procedencia',()=>readMemoryItem(hit,scope,uid,params),'button secondary'));
        $('memory-results').append(card);
      }
      $('memory-prev').disabled=offset===0;$('memory-next').disabled=memory.next===null||memory.next===undefined;
    }catch(error){if(epoch===memory.sequence&&scope===memoryScope()){$('memory-status').textContent='La consulta no pudo completarse. Tu archivo no se ha modificado.';memory.scope='';throw error;}}
  }
  async function readMemoryItem(hit,scope,uid,params){
    const data=await api('/universes/'+uid+'/memory/'+encodeURIComponent(hit.origin)+'/'+encodeURIComponent(hit.id)+'?'+params);
    if(scope!==memoryScope())return;
    const nodes=[el('p',memoryLabel(data))];
    if(['source','turn'].includes(data.origin))nodes.push(el('pre',data.content,'memory-full'));
    else {const payload=data.origin==='proposal'?data.payload.record:data.payload;const value=payload.data||{};nodes.push(el('p',value.statement||value.summary||value.description||data.title,'memory-excerpt'));if(value.features){const features=el('dl');for(const [key,val] of Object.entries(value.features))stateRow(features,key,val);nodes.push(features);}if(value.tags?.length)nodes.push(el('p','Palabras de referencia: '+value.tags.join(' · ')));nodes.push(details('Ver todos los datos guardados',payload));}
    const relations=(data.origin==='proposal'?data.payload.record:data.payload)?.data?.connections||[];
    if(relations.length){const group=el('section',undefined,'memory-connections');group.append(el('h3','Semejanzas y conexiones guardadas'));for(const relation of relations){const row=el('div',undefined,'panel quiet');row.append(badge(certainty[relation.certainty]||'Sin determinar'),el('strong',name(relation.target_id)),el('p',relation.relation));group.append(row);}nodes.push(group);}
    nodes.push(details('Procedencia del resultado',{identificador:data.id,tipo:data.origin,estado:data.status,certeza:certainty[data.certainty]||data.certainty,fuente:data.source_id||'Ver referencias del registro',referencias:data.payload?.source_ref||data.payload?.record?.source_ref||{}}));
    if(data.origin==='turn'&&$('memory-audience').value==='narrator')nodes.push(button('Conservar un detalle de esta intervención',()=>{$('action-dialog').close();detailFromTurn(data.payload);},'button secondary'));
    action(data.title,nodes,null);$('action-submit').hidden=true;
  }
  function detailFromTurn(turn){
    const selected=window.getSelection()?.toString().trim();
    const excerpt=selected&&turn.content.includes(selected)?selected:turn.content;
    openEditor('detail');
    const originCampaign=record(turn.campaign_id),proposal=s.snapshot.proposals.find(p=>p.id===turn.proposal_id);
    editor.sourceId=proposal?.record.source_id||null;editor.sourceRef={turn_id:turn.id,excerpt};
    editor.data.statement=excerpt;editor.data.day=null;
    $('editor-name').value='';$('editor-name').placeholder='Dale un nombre para encontrarlo después';
    $('editor-campaign').value=turn.campaign_id;$('editor-world').value=originCampaign?.world_id||'';
    basicFields();$('editor-json').value=JSON.stringify(editor.data,null,2);
    $('editor-origin').hidden=false;$('editor-origin').append(el('p','La intervención original ya está guardada. Esta ficha conserva su referencia y queda pendiente de revisión; no establece quién vio el detalle ni lo convierte en un hecho.'),details('Texto original que fundamenta el detalle',excerpt));
    $('editor-decision').value='Conservar un detalle de esta intervención para futuras consultas.';
  }
  function detailConnections(){
    const root=$('basic-fields'),container=el('div');container.id='detail-connections';
    root.append(el('h3','Semejanzas y conexiones'),el('p','Puedes registrar un parecido como hipótesis. Confirmar una conexión requiere evidencia que cite ambos detalles y tu revisión. No son automáticamente el mismo objeto.','help-note'),container);
    const self=editor.target?.id||editor.proposal?.target_id;
    function row(value={target_id:'',relation:'',certainty:'hypothesis',evidence_ids:[]}){
      const wrap=el('div',undefined,'panel detail-connection');
      const target=el('select');target.className='detail-target';options(target,records('detail').filter(r=>r.id!==self),value.target_id,'Selecciona otro detalle');
      const targetLabel=el('label','Otro detalle guardado');targetLabel.append(target);
      const description=el('textarea');description.className='detail-relation';description.rows=2;description.value=value.relation;description.placeholder='Qué se parece o qué relación propones';const relationLabel=el('label','Relación observada o sospechada');relationLabel.append(description);
      const confidence=el('select');confidence.className='detail-certainty';options(confidence,Object.entries(certainty).map(([id,name])=>({id,name})),value.certainty);const confidenceLabel=el('label','Certeza de esta conexión');confidenceLabel.append(confidence);
      const evidence=el('select');evidence.className='detail-evidence';evidence.multiple=true;evidence.size=3;options(evidence,records().filter(r=>['fact','event'].includes(r.kind)&&r.data.certainty==='confirmed'));for(const option of evidence.options)option.selected=(value.evidence_ids||[]).includes(option.value);const evidenceLabel=el('label','Evidencia confirmada que cita ambos detalles');evidenceLabel.append(evidence);
      wrap.append(targetLabel,relationLabel,confidenceLabel,evidenceLabel,button('Quitar esta conexión del borrador',()=>wrap.remove()));container.append(wrap);
    }
    for(const value of editor.data.connections||[])row(value);
    root.append(button('+ Registrar una semejanza o conexión',()=>row(),'button secondary'));
  }
  function issueList(container,issues){for(const issue of issues){const n=el('div',undefined,'issue '+issue.severity);n.append(el('strong',issue.severity==='warning'?'Revisión necesaria':'Contradicción'),el('p',issue.message),el('p',issue.suggestion));container.append(n);}}
  function renderProposals(){const list=$('proposal-list');list.replaceChildren();const pending=s.snapshot.proposals.filter(p=>p.status==='pending');if(!pending.length)empty(list,'Todo revisado','Las nuevas escenas y descubrimientos aparecerán aquí antes de formar parte de la memoria del mundo.');
    for(const p of pending){const n=el('article',undefined,'proposal-card');n.dataset.proposal=p.id;n.append(el('h3',p.title),el('p','Preparado sobre la versión '+p.base_version+' · '+(certainty[p.record.data.certainty]||kinds[p.record.kind]),'proposal-meta'),el('p',p.record.data.summary||p.record.data.statement||p.record.name));n.append(details('Contenido de la propuesta',p.record.data));if(p.record.source_id)n.append(button('Leer fuente original',()=>showSource(p.record.source_id)));issueList(n,p.issues);
      const blocked=p.issues.some(i=>['error','critical'].includes(i.severity));const protectedChange=p.issues.some(i=>i.code==='protected_character_change');const stale=p.base_version!==version();if(stale)n.append(el('p','El mundo cambió desde esta propuesta. Vuelve a comprobarla antes de decidir.','help-note'));
      const form=el('form',undefined,'proposal-decision');const group=el('div');const label=el('label','Tu decisión y su motivo');label.htmlFor='decision-'+p.id;const input=el('textarea');input.id=label.htmlFor;input.required=true;input.rows=2;input.placeholder='Qué validas o por qué lo descartas';group.append(label,input);const checkLabel=el('label',undefined,'check-label');const check=el('input');check.type='checkbox';checkLabel.append(check,document.createTextNode('He contrastado el contenido y revisado las advertencias.'));group.append(checkLabel);form.append(group);
      const authorize=el('input');authorize.type='checkbox';if(protectedChange){const comparisons=[];const previous=record(p.target_id);if(previous)comparisons.push({personaje:previous.name,antes:{nombre:previous.name,personalidad:previous.data.personality,psicologia:previous.data.psychology,papeles:previous.data.roles},despues:{nombre:p.record.name,personalidad:p.record.data.personality,psicologia:p.record.data.psychology,papeles:p.record.data.roles},causa:p.record.data.change_basis});if(p.record.kind==='event'){for(const action of p.record.data.actions||[]){if(action.type!=='emotion'||!action.change_basis)continue;const actor=record(action.character_id);if(!actor)continue;comparisons.push({personaje:actor.name,antes:actor.data.psychology,despues:{...actor.data.psychology,...action.changes},causa:action.change_basis});}}n.append(details('Comparar antes y después',comparisons));const authLabel=el('label',undefined,'check-label');authLabel.append(authorize,document.createTextNode('Autorizo expresamente este cambio de identidad, personalidad o estado psicológico y su justificación.'));group.append(authLabel);}
      const approve=el('button','Incorporar al canon','button primary');approve.type='submit';approve.disabled=blocked||stale;form.append(approve,button('Comprobar otra vez',async()=>{await api('/proposals/'+p.id+'/review',{});await refresh();},'button secondary'),button('Corregir propuesta',()=>openEditor(p.record.kind,null,p),'button secondary'),button('Descartar',async()=>{if(!input.value.trim()){input.reportValidity();return;}await api('/proposals/'+p.id+'/reject',{decision:input.value});await refresh();notify('Propuesta descartada. Su fuente sigue conservada.');},'button secondary'));
      form.addEventListener('submit',safe(async e=>{e.preventDefault();await busy(approve,async()=>{if(!check.checked)throw Error('Marca que has revisado el contenido antes de validarlo.');if(protectedChange&&!authorize.checked)throw Error('Este personaje está protegido. Revisa y autoriza específicamente el cambio.');await api('/proposals/'+p.id+'/accept',{expected_version:p.base_version,decision:input.value,acknowledge_warnings:check.checked,authorize_character_change:authorize.checked});await refresh();notify('Decisión registrada. La memoria del mundo está guardada.');});}));n.append(form);list.append(n);
    }
    const done=s.snapshot.proposals.filter(p=>p.status!=='pending');if(done.length)list.append(details('Decisiones anteriores ('+done.length+')',done.map(p=>({titulo:p.title,estado:p.status==='accepted'?'Validada':'Descartada',motivo:p.decision}))));
  }
  function renderHistory(){ $('export-markdown').href='/api/universes/'+s.uid+'/export.md';$('chatgpt-grants').hidden=!s.cloud;const list=$('revision-list');list.replaceChildren();for(const r of s.snapshot.revisions){const n=el('article',undefined,'revision');const body=el('div');body.append(el('p',r.summary),el('small',new Date(r.created_at).toLocaleString('es')),button('Ver cambio completo',async()=>showData('Cambio v'+r.version,await api('/universes/'+s.uid+'/revisions/'+r.version))));n.append(el('strong','v'+r.version),body);list.append(n);}const sources=$('source-list');sources.replaceChildren();for(const r of s.snapshot.sources){const n=el('article',undefined,'record-card');n.append(el('h3',r.title),button('Leer original',()=>showSource(r.id)));sources.append(n);}}
  async function loadChatgptGrants(){
    const host=$('chatgpt-grants'),list=$('chatgpt-grants-list');if(!s.cloud){host.hidden=true;return;}host.hidden=false;
    const epoch=++grantsSequence,uid=s.uid;list.replaceChildren(el('p','Consultando accesos autorizados…','help-note'));
    try{
      const data=await api('/bridge/grants');if(epoch!==grantsSequence||uid!==s.uid||s.tab!=='history')return;
      list.replaceChildren();const grants=data.grants||[];
      if(!grants.length){empty(list,'Sin accesos autorizados','Todavía no hay accesos de ChatGPT autorizados para este archivo.');return;}
      for(const grant of grants){
        const card=el('article',undefined,'record-card');const universe=s.universes.find(item=>item.id===grant.universe_id);
        const active=!!grant.active;const state=active?'Activo':grant.revoked_at?'Revocado':'Vencido';
        card.append(badge(state,active?'accepted':'warning'),el('h3',grant.label||'Acceso sin nombre'),el('p',universe?.name||'Universo no disponible'));
        const expiry=grant.expires_at?new Date(grant.expires_at).toLocaleString('es',{dateStyle:'medium',timeStyle:'short'}):'Sin fecha de vencimiento';
        card.append(el('p','Vence: '+expiry,'card-meta'));
        if(active){card.append(button('Revocar acceso',()=>confirmGrantRevoke(grant),'text-button'));}
        list.append(card);
      }
    }catch(error){
      if(epoch!==grantsSequence||uid!==s.uid||s.tab!=='history')return;
      list.replaceChildren();empty(list,error.status===404?'Gestión de accesos no disponible':'No se pudieron consultar los accesos',error.status===404?'Esta instalación todavía no ofrece la gestión de accesos con ChatGPT.':'La consulta no se completó. Vuelve a abrir Historial y copias para intentarlo de nuevo.');
    }
  }
  function confirmGrantRevoke(grant){
    action('Revocar acceso a ChatGPT',[el('p','ChatGPT dejará de poder usar este acceso autorizado para '+(s.universes.find(item=>item.id===grant.universe_id)?.name||'este universo')+'.')],async()=>{
      const uid=s.uid,epoch=grantsSequence;await api('/bridge/grants/'+encodeURIComponent(grant.id)+'/revoke',{});$('action-dialog').close();
      if(uid===s.uid&&epoch===grantsSequence&&s.tab==='history')await loadChatgptGrants();
      notify('Acceso revocado.');
    },'Revocar acceso');
  }
  async function showSource(id){const data=await api('/universes/'+s.uid+'/sources/'+id);showData(data.title,data.content);}
  function showData(title,data){$('action-title').textContent=title;$('action-fields').replaceChildren(el('pre',typeof data==='string'?data:JSON.stringify(data,null,2)));$('action-submit').hidden=true;actionSubmit=null;openDialog('action-dialog');}
  function draftKey(cid=s.cid,role=$('turn-role').value){return 'draft.'+cid+'.'+role;}
  function castKey(cid=s.cid){return 'cast.'+cid;}
  async function requestNarration(cid){
    const key='umbral.generation-pending.'+cid;
    let payload;
    try{
      const raw=localStorage.getItem(key);
      payload=raw?JSON.parse(raw):{request_id:crypto.randomUUID(),expected_version:version(),expected_last_turn_id:s.turns.at(-1)?.id||null};
      if(!payload.request_id||!Number.isInteger(payload.expected_version))throw Error('receipt');
      if(!raw){const serialized=JSON.stringify(payload);localStorage.setItem(key,serialized);if(localStorage.getItem(key)!==serialized)throw Error('storage');}
    }catch(_){throw Error('No se pudo proteger el envío del narrador en este navegador. No se solicitó una respuesta.');}
    try{
      const result=await api('/campaigns/'+cid+'/generate',payload);
      try{localStorage.removeItem(key);}catch(_){}
      return result;
    }catch(error){
      if([400,409,422].includes(error.status)){try{localStorage.removeItem(key);}catch(_){}}
      throw error;
    }
  }
  function pendingTurnKey(cid,role,id){return 'turn-pending.'+cid+'.'+role+(id?'.'+id:'');}
  function pendingTurnsKey(cid,role){return 'turn-pending-list.'+cid+'.'+role;}
  function receiptStorageError(){return Error('No se pudo revisar el envío pendiente en este navegador. No se envió la intervención; vuelve a cargar la página e inténtalo otra vez.');}
  function receiptJSON(raw){try{return JSON.parse(raw);}catch(_){throw receiptStorageError();}}
  function persistReceipt(cid,role,payload){
    const key='umbral.'+pendingTurnKey(cid,role,payload.id),serialized=JSON.stringify(payload);
    try{localStorage.setItem(key,serialized);return localStorage.getItem(key)===serialized;}catch(_){return false;}
  }
  function readPendingTurns(cid,role){
    const prefix='umbral.'+pendingTurnKey(cid,role)+'.';
    let receipts=[],legacyKeys=[],legacyReceipts=[];
    try{
      for(let index=0;index<localStorage.length;index++){
        const key=localStorage.key(index);
        if(key?.startsWith(prefix)){
          const raw=localStorage.getItem(key);if(raw===null)continue;
          const item=receiptJSON(raw),id=key.slice(prefix.length);
          if(!item||item.id!==id||item.role!==role||typeof item.content!=='string'||!Number.isInteger(item.expected_version)||!Array.isArray(item.cast))throw receiptStorageError();
          receipts.push(item);
        }
      }
      for(const legacyKey of [pendingTurnKey(cid,role),pendingTurnsKey(cid,role)]){
        const raw=localStorage.getItem('umbral.'+legacyKey);
        if(raw===null)continue;
        const parsed=receiptJSON(raw),items=legacyKey===pendingTurnsKey(cid,role)?parsed:[parsed];
        if(!Array.isArray(items))throw receiptStorageError();
        legacyKeys.push('umbral.'+legacyKey);legacyReceipts.push(...items);
      }
    }catch(_){throw receiptStorageError();}
    for(const old of legacyReceipts){
      if(!old||typeof old.id!=='string'||old.role!==role||typeof old.content!=='string'||!Number.isInteger(old.expected_version)||!Array.isArray(old.cast))throw receiptStorageError();
      const present=receipts.find(item=>item.id===old.id);
      if(present){if(JSON.stringify(present)!==JSON.stringify(old))throw receiptStorageError();continue;}
      if(!persistReceipt(cid,role,old))throw Error('No se pudo conservar un envío pendiente. Libera espacio en el navegador y vuelve a intentarlo.');
      receipts.push(old);
    }
    if(legacyKeys.length){try{for(const key of legacyKeys)localStorage.removeItem(key);}catch(_){/* The verified UUID copies remain authoritative. */}}
    return receipts;
  }
  function addPendingTurn(cid,role,payload){readPendingTurns(cid,role);return persistReceipt(cid,role,payload);}
  function removePendingTurn(cid,role,requestId){try{localStorage.removeItem('umbral.'+pendingTurnKey(cid,role,requestId));}catch(_){/* Keeping a receipt is safer than forgetting an uncertain send. */}}
  function findPendingTurn(cid,role,content,cast){return readPendingTurns(cid,role).find(item=>item.role===role&&item.content===content&&sameCast(item.cast,cast)&&typeof item.id==='string'&&Number.isInteger(item.expected_version));}
  function castIds(items){return (items||[]).map(item=>typeof item==='string'?item:item.character_id).sort();}
  function sameCast(left,right){return JSON.stringify(castIds(left))===JSON.stringify(castIds(right));}
  function clearSavedTurnState(role,payload,draftKeyAtSubmit,castKeyAtSubmit,currentCast){
    if(read(draftKeyAtSubmit)!==payload.content)return false;
    if(role==='narrator'){
      let savedCast=[];try{savedCast=JSON.parse(read(castKeyAtSubmit)||'[]');}catch(_){}
      if(!sameCast(savedCast,payload.cast)||(currentCast!==null&&!sameCast(currentCast,payload.cast)))return false;
      remove(castKeyAtSubmit);
    }
    remove(draftKeyAtSubmit);
    return true;
  }
  async function refreshSelectedUniverse(uid){if(s.uid===uid&&$('universe-select').value===uid)await loadUniverse(uid,$('campaign-select').value||s.cid);}
  function renderCastPicker(){const root=$('cast-picker');root.replaceChildren();const isNarrator=$('turn-role').value==='narrator';root.hidden=!isNarrator;if(!isNarrator)return;root.append(el('strong','Personajes presentes en esta escena'));root.append(el('p','Elige solo a quienes están en escena; nadie se incluye automáticamente. Las referencias ayudan a comparar la continuidad, y la prosa propuesta siempre queda para revisión humana.','help-note'));const choices=records('character').filter(r=>r.world_id===campaign()?.world_id);let selected=[];try{selected=JSON.parse(read(castKey())||'[]');if(!Array.isArray(selected))selected=[];}catch(_){selected=[];}for(const character of choices){const label=el('label',undefined,'cast-option');const input=el('input');input.type='checkbox';input.value=character.id;input.checked=selected.includes(character.id);input.addEventListener('change',()=>{const ids=Array.from(root.querySelectorAll('input[type=checkbox]:checked')).map(item=>item.value);write(castKey(),JSON.stringify(ids));});label.append(input,el('span',character.name));root.append(label);}if(!choices.length)root.append(el('p','Aún no hay personajes en el mundo de esta campaña.','help-note'));}
  function saveDraft(){if(s.cid)write(draftKey(),$('turn-content').value);}
  function restoreDraft(){$('turn-content').value=s.cid?read(draftKey()):'';$('draft-status').textContent=$('turn-content').value?'Borrador pendiente · solo en este navegador':'Las intervenciones se guardan al enviarlas.';}
  async function openWizard(){ $('wizard-form').reset();options($('wizard-universe'),s.universes,s.uid,'Un universo independiente nuevo');await wizardUniverse();$('world-rules-group').hidden=true;openDialog('wizard-dialog');}
  async function wizardUniverse(){const uid=$('wizard-universe').value;$('wizard-new-universe').hidden=!!uid;$('wizard-existing-world').hidden=!uid;wizardRecords=uid?(await api('/universes/'+uid)).records:[];options($('wizard-world'),wizardRecords.filter(r=>r.kind==='world'),uid===s.uid?campaign()?.world_id||'':'','Crear un mundo nuevo');wizardWorld();}
  function wizardWorld(){const wid=$('wizard-world').value;$('wizard-world-name').hidden=!!wid;$('wizard-existing-protagonist').hidden=!wid;options($('wizard-protagonist'),wizardRecords.filter(r=>r.kind==='character'&&r.world_id===wid),'','Crear un protagonista nuevo');wizardHero();}
  function wizardHero(){$('wizard-new-protagonist').hidden=!!$('wizard-protagonist').value;$('protagonist-name').required=!$('wizard-protagonist').value;}
  // Friendly field editors preserve every unexposed property when correcting a record.
  function defaults(kind){const wid=campaign()?.world_id;return {world:{mode:'discover',description:'',current_day:0},campaign:{protagonist_id:campaign()?.data.protagonist_id||'',premise:'',current_day:world()?.data.current_day||0},character:{description:'',personality:{},psychology:{},life:{},goals:[],routine:'',balance:0,location_id:null},place:{description:'',parent_id:null},item:{description:'',owner_id:null,quantity:1,created_day:null},relationship:{from_id:'',to_id:'',dimensions:{},explanation:''},knowledge:{character_id:'',fact_id:'',belief:'believes',learned_from:''},detail:{statement:'',certainty:'unknown',visibility:'narrator',known_by:[],tags:[],features:{},place_id:null,item_id:null,evidence_ids:[],connections:[],day:null},fact:{statement:'',certainty:'unknown',visibility:'narrator',known_by:[]},event:{summary:'',certainty:'unknown',visibility:'narrator',known_by:[],day:world()?.data.current_day??null,actions:[]},rule:{statement:'',certainty:'confirmed',forbidden_abilities:[],visibility:'narrator',known_by:[]},faction:{description:'',members:[],resources:{}},temporal_link:{from_world_id:wid||'',to_world_id:'',certainty:'unknown',relation:'Equivalencia desconocida'}}[kind];}
  function field(key,label,type='text',choices=null){const root=$('basic-fields');const id='field-'+key;const title=el('label',label);title.htmlFor=id;const input=el(choices?'select':['textarea','json','lines','description','dimensions'].includes(type)?'textarea':'input');input.id=id;input.dataset.key=key;input.dataset.format=type;if(choices)options(input,choices,'',type==='optional'?'Sin establecer':null);else if(input.tagName==='INPUT')input.type=type==='number'||type==='optional-number'?'number':'text';if(input.tagName==='TEXTAREA')input.rows=3;const value=editor.data[key];input.value=type==='json'?JSON.stringify(value??{},null,2):type==='lines'?(value||[]).join('\n'):type==='description'?value?.description||'':type==='dimensions'?Object.entries(value||{}).map(([k,v])=>k+': '+v).join('\n'):(value??'');root.append(title,input);return input;}
  function basicFields(){const k=$('editor-kind').value;$('basic-fields').replaceChildren();const refs=kind=>records(kind).filter(r=>!$('editor-world').value||r.world_id===$('editor-world').value);const chars=refs('character');
    if(['world','character','place','item','faction'].includes(k))field('description','Descripción','textarea');
    if(k==='world'){field('mode','Cómo se descubre','text',[{id:'discover',name:'Durante la narración'},{id:'defined',name:'Definido inicialmente'}]);field('current_day','Día local actual','number');}
    if(k==='campaign'){field('protagonist_id','Protagonista','text',chars);field('premise','Punto de partida','textarea');}
    if(k==='character'){field('personality','Cómo es · valores, hábitos y contradicciones','description');field('psychology','Cómo se siente y qué lo explica','description');field('life','Vida, trabajo y obligaciones','description');field('routine','Rutina habitual','textarea');field('goals','Objetivos · uno por línea','lines');field('location_id','Ubicación','optional',refs('place'));field('balance','Dinero disponible · unidades enteras','number');}
    if(k==='character')characterSafeguards();
    if(k==='place')field('parent_id','Forma parte de','optional',refs('place'));
    if(k==='item'){field('owner_id','En manos de','optional',chars);field('quantity','Cantidad','number');field('created_day','Día de creación · vacío si se desconoce','optional-number');}
    if(k==='relationship'){field('from_id','Lo que siente','text',chars);field('to_id','Hacia','text',chars);field('dimensions','Matices · uno por línea, por ejemplo: Confianza: cautelosa','dimensions');field('explanation','Acontecimientos que lo explican','textarea');}
    if(k==='knowledge'){field('character_id','Quién tiene este conocimiento','text',chars);field('fact_id','Información','text',records().filter(r=>['fact','event','rule','detail'].includes(r.kind)));field('belief','Cómo lo considera','text',[{id:'knows',name:'Lo sabe'},{id:'believes',name:'Lo cree'},{id:'suspects',name:'Lo sospecha'},{id:'hides',name:'Lo oculta'}]);field('learned_from','Cómo y de quién lo aprendió','textarea');}
    if(['fact','event','rule','detail'].includes(k)){field(k==='event'?'summary':'statement',k==='event'?'Qué sucede':k==='detail'?'Qué se observó':'Información o regla','textarea');field('certainty','Qué tan establecido está','text',Object.entries(certainty).map(([id,name])=>({id,name})));field('visibility','Clasificación','text',[{id:'narrator',name:'Reservado al narrador'},{id:'shared',name:'Información compartida'}]);const known=field('known_by','Personajes que lo conocen · selecciona todos los que correspondan','text',chars);known.multiple=true;known.size=Math.min(4,Math.max(chars.length,2));Array.from(known.options).forEach(o=>o.selected=(editor.data.known_by||[]).includes(o.value));known.dataset.format='multiple';if(['event','detail'].includes(k))field('day','Día local · vacío si se desconoce','optional-number');}
    if(k==='detail'){field('tags','Palabras para encontrarlo · una por línea','lines');field('features','Aspecto observado · por ejemplo, Símbolo: espiral abierta','dimensions');field('place_id','Lugar donde se observó','optional',refs('place'));field('item_id','Objeto al que corresponde · si está identificado','optional',refs('item'));detailConnections();}
    if(k==='temporal_link'){field('from_world_id','Mundo de origen','text',records('world'));field('to_world_id','Mundo de destino','text',records('world'));field('relation','Relación temporal conocida','textarea');field('certainty','Certeza','text',Object.entries(certainty).map(([id,name])=>({id,name})));}
  }
  function characterSafeguards(){
    const root=$('basic-fields');const roles=el('div');const title=el('h3','Papeles que el mundo recuerda');root.append(title,el('p','Por ejemplo: recepcionista en el gremio. Un puesto exclusivo no puede cambiar de persona sin un relevo registrado.','help-note'),roles);
    function roleRow(value={place_id:'',label:'',exclusive:true}){const row=el('div',undefined,'panel role-row');const place=el('select');place.className='role-place';options(place,records('place').filter(r=>r.world_id===$('editor-world').value),value.place_id,'Selecciona el lugar');const placeLabel=el('label','Lugar');placeLabel.append(place);const label=el('input');label.className='role-label';label.value=value.label;label.placeholder='Por ejemplo, recepcionista';const roleLabel=el('label','Papel o puesto');roleLabel.append(label);const check=el('input');check.type='checkbox';check.className='role-exclusive';check.checked=value.exclusive;const exclusive=el('label',undefined,'check-label');exclusive.append(check,document.createTextNode('Este puesto corresponde a una sola persona'));row.append(placeLabel,roleLabel,exclusive,button('Quitar este papel del borrador',()=>row.remove()));roles.append(row);}
    (editor.data.roles||[]).forEach(roleRow);roles.id='character-roles';root.append(button('+ Añadir un papel',()=>roleRow(),'button secondary'));
    const target=editor.target||record(editor.proposal?.target_id);if(!target)return;
    const basis=editor.proposal?editor.data.change_basis:null;const mode=field('_change_mode','Si modificas su identidad o carácter, ¿por qué?','optional',[{id:'evolution',name:'Evolución por acontecimientos confirmados'},{id:'correction',name:'Corrijo explícitamente un dato anterior'}]);mode.value=basis?.mode||'';const why=field('_change_reason','Causa concreta del cambio','textarea');why.value=basis?.reason||'';const ev=field('_change_evidence','Acontecimientos que explican la evolución','text',records('event').filter(r=>r.data.certainty==='confirmed'));ev.multiple=true;ev.size=3;ev.dataset.format='multiple';Array.from(ev.options).forEach(o=>o.selected=(basis?.evidence_ids||[]).includes(o.value));
  }
  function openEditor(kind='fact',target=null,proposal=null){$('editor-form').reset();$('editor-origin').hidden=true;$('editor-origin').replaceChildren();editor={target,proposal,base:version(),data:structuredClone(target?.data||proposal?.record.data||defaults(kind)),advanced:false};options($('editor-kind'),Object.entries(kinds).map(([id,name])=>({id,name})),kind);$('editor-kind').disabled=!!target||!!proposal;$('editor-title').textContent=target?'Corregir '+target.name:proposal?'Revisar el borrador':'Añadir '+kinds[kind].toLocaleLowerCase('es');$('editor-name').value=target?.name||proposal?.record.name||'';options($('editor-world'),records('world'),target?.world_id||proposal?.record.world_id||campaign()?.world_id||'','Sin mundo asignado');options($('editor-campaign'),records('campaign'),target?.campaign_id||proposal?.record.campaign_id||(['event','detail'].includes(kind)?s.cid:'')||'','No exclusiva de una campaña');if(kind==='world')$('editor-world').value='';$('editor-json').value=JSON.stringify(editor.data,null,2);$('editor-references').textContent=records().map(r=>kinds[r.kind]+' · '+r.name+'\n'+r.id).join('\n\n');$('advanced-editor').open=false;basicFields();openDialog('editor-dialog');}
  function action(title,fields,submit,label='Continuar'){$('action-title').textContent=title;$('action-fields').replaceChildren(...fields);$('action-submit').hidden=false;$('action-submit').textContent=label;actionSubmit=submit;openDialog('action-dialog');}
  function actionField(label,type='textarea',choices){const wrap=el('div');const id='action-input-'+Math.random().toString(36).slice(2);const title=el('label',label);title.htmlFor=id;const input=el(type==='select'?'select':type==='textarea'?'textarea':'input');input.id=id;input.required=true;if(type==='select')options(input,choices,choices[0]?.id);else if(type==='number'){input.type='number';input.min='1';input.max='36500';input.value='1';}wrap.append(title,input);return {wrap,input};}
  function advance(){const c=campaign();const w=actionField('En qué mundo','select',records('world'));w.input.value=c?.world_id||w.input.value;const days=actionField('Cuántos días locales transcurren','number');action('El mundo sigue viviendo',[el('p','Se resumirán rutinas establecidas. Las situaciones sin resolver seguirán abiertas hasta que las revises.'),w.wrap,days.wrap],async()=>{await api('/universes/'+s.uid+'/advance',{world_id:w.input.value,days:Number(days.input.value),relevant_character_ids:[]});$('action-dialog').close();await refresh();navigate('review');notify('Avance preparado. El reloj cambiará cuando lo valides.');},'Preparar avance');}
  async function context(){if(!s.cid)return;const data=await api('/campaigns/'+s.cid+'/context?audience='+$('context-audience').value);$('context-content').textContent=JSON.stringify(data,null,2);}
  document.querySelectorAll('.close-dialog').forEach(n=>n.addEventListener('click',()=>n.closest('dialog').close()));
  document.querySelectorAll('[data-tab]').forEach(n=>n.addEventListener('click',()=>navigate(n.dataset.tab)));
  $('universe-select').addEventListener('change',safe(async()=>{saveDraft();await loadUniverse($('universe-select').value,read('campaign.'+$('universe-select').value));}));
  $('campaign-select').addEventListener('change',safe(async()=>{saveDraft();await loadUniverse(s.uid,$('campaign-select').value);}));
  ['new-campaign','welcome-create'].forEach(id=>$(id).addEventListener('click',safe(openWizard)));
  $('wizard-universe').addEventListener('change',safe(wizardUniverse));$('wizard-world').addEventListener('change',wizardWorld);$('wizard-protagonist').addEventListener('change',wizardHero);
  document.querySelectorAll('[name=creation-mode]').forEach(n=>n.addEventListener('change',()=>$('world-rules-group').hidden=n.value!=='defined'));
  $('wizard-form').addEventListener('submit',safe(async e=>{e.preventDefault();await busy(e.submitter,async()=>{const hero=$('wizard-protagonist').value;const data=await api('/wizard',{universe_id:$('wizard-universe').value||null,world_id:$('wizard-world').value||null,protagonist_id:hero||null,universe_name:$('universe-name').value.trim()||'Mi universo',campaign_name:$('campaign-name').value,world_name:$('world-name').value,mode:document.querySelector('[name=creation-mode]:checked').value,premise:$('premise').value,world_rules:$('world-rules').value,protagonist_name:hero?wizardRecords.find(r=>r.id===hero).name:$('protagonist-name').value,personality:$('protagonist-personality').value});$('wizard-dialog').close();await bootstrap(data.universe_id,data.campaign_id);navigate('story');notify('Tu campaña está creada y guardada. Puedes empezar cuando quieras.');});}));
  $('turn-content').addEventListener('input',()=>{saveDraft();$('draft-status').textContent='Borrador pendiente · solo en este navegador';});$('turn-role').addEventListener('change',()=>{restoreDraft();renderCastPicker();$('turn-content').placeholder=$('turn-role').value==='user'?'¿Qué hace o dice tu personaje?':'Escribe una escena propuesta. Se guardará para revisión.';});
  $('turn-form').addEventListener('submit',safe(async e=>{
    e.preventDefault();
    if(s.savingTurn)return;

    const content=$('turn-content').value;
    const role=$('turn-role').value;
    const cid=s.cid;
    const uid=s.uid;
    const campaignName=campaign()?.name||'la campaña';
    const versionAtSubmit=version();
    const draftAtSubmit=draftKey(cid,role);
    const castKeyAtSubmit=castKey(cid);
    if(!content.trim())throw Error('Escribe una intervención antes de enviarla.');

    const cast=role==='narrator'
      ?Array.from($('cast-picker').querySelectorAll('input[type=checkbox]:checked'))
        .map(input=>record(input.value))
        .filter(r=>r&&r.kind==='character'&&r.world_id===campaign()?.world_id)
        .map(r=>({character_id:r.id,name:r.name,personality:r.data.personality||{}}))
      :[];
    const previous=findPendingTurn(cid,role,content,cast);
    let payload;
    if(previous){
      payload=previous;
    }else{
      payload={id:crypto.randomUUID(),content,role,cast,expected_version:versionAtSubmit};
      if(!addPendingTurn(cid,role,payload)){
        notify('No se pudo guardar el envío pendiente en este navegador. El texto sigue aquí; libera espacio y vuelve a intentarlo.',true);
        return;
      }
    }

    s.savingTurn=true;
    $('turn-form').querySelector('[type=submit]').disabled=true;
    try{
      try{
        await api('/campaigns/'+cid+'/turns',{
          role:payload.role,
          content:payload.content,
          cast:payload.cast,
          request_id:payload.id,
          expected_version:payload.expected_version
        });
      }catch(error){
        if([400,409,422].includes(error.status)){
          removePendingTurn(cid,role,payload.id);
          saveDraft();
          try{await refreshSelectedUniverse(uid);}catch(_){}
        }
        throw error;
      }

      removePendingTurn(cid,role,payload.id);
      const isSubmittedContext=s.uid===uid&&s.cid===cid&&$('turn-role').value===role;
      const currentCast=role==='narrator'&&isSubmittedContext?Array.from($('cast-picker').querySelectorAll('input[type=checkbox]:checked')).map(input=>input.value):null;
      const isSubmittedIntentOpen=isSubmittedContext&&$('turn-content').value===payload.content&&(role!=='narrator'||sameCast(currentCast,payload.cast));
      clearSavedTurnState(role,payload,draftAtSubmit,castKeyAtSubmit,currentCast);
      if(isSubmittedIntentOpen){
        $('turn-content').value='';
        $('draft-status').textContent='Las intervenciones se guardan al enviarlas.';
      }
      saveDraft();
      await refreshSelectedUniverse(uid);
      if(s.cid===cid){
        $('turn-list').scrollTop=$('turn-list').scrollHeight;
        notify('Intervención guardada en «'+campaignName+'». Puedes cerrar y retomar esta campaña.');
        if(role==='user'&&['chatgpt-plan','chatgpt-plan-self-hosted'].includes(s.provider)){
          notify('Tu decisión está guardada. El narrador está consultando la memoria…');
          try{
            if(!window.UmbralPlan)throw Error('La conexión del narrador no está cargada. Vuelve a abrir Umbral; tu decisión ya está guardada.');
            if(!await window.UmbralPlan.ensureAutomatic())return;
            if(s.cid!==cid||s.uid!==uid)throw Error('Cambiaste de campaña. Abre la campaña original y pulsa Generar respuesta del narrador.');
            await requestNarration(cid);
            await refreshSelectedUniverse(uid);
            notify('Tu decisión y la respuesta del narrador están guardadas. La escena queda por revisar.');
          }catch(error){
            notify('Tu decisión sí está guardada. El narrador no pudo responder: '+error.message,true);
          }
        }
      }
    }finally{
      s.savingTurn=false;
      if(s.snapshot)renderStory();
    }
  }));  $('generate-turn').addEventListener('click',safe(()=>busy($('generate-turn'),async()=>{const cid=s.cid,uid=s.uid;await requestNarration(cid);await refreshSelectedUniverse(uid);notify('Respuesta del narrador guardada como propuesta.');})));
  $('show-context').addEventListener('click',safe(async()=>{await context();openDialog('context-dialog');}));$('context-audience').addEventListener('change',safe(context));
  $('chatgpt-help').addEventListener('click',()=>{const container=$('action-fields');container.replaceChildren();$('action-title').textContent='ChatGPT y tu mundo';const sections=s.cloud?[['Usar el conector privado','En un chat nuevo de ChatGPT, elige @Umbral · Memoria para ChatGPT. Consulta en Historial y copias qué universo tiene acceso autorizado. Antes de cada escena, consulta el contexto; al terminar, guarda la narración como propuesta para revisarla en Umbral.'],['Qué permite el acceso','El permiso autoriza acceso al universo seleccionado. Ver un permiso activo aquí no verifica que ChatGPT esté conectado ni que el conector esté funcionando. No se necesita una clave de modelo de pago.'],['Tus chats y decisiones','ChatGPT puede enviar aquí el texto exacto que escribiste; aparecerá pendiente hasta que lo confirmes o rechaces en esta página. Confirma solo palabras que realmente enviaste. ChatGPT no debe inventar, completar ni atribuirte decisiones.']]:[['Puente local stdio','Un asistente compatible puede consultar y preparar propuestas mediante el puente stdio cuando está configurado y en ejecución. Este modo no consulta por sí solo el archivo alojado en la nube.'],['Cómo usarlo','Configura el puente local en tu asistente. Consulta el contexto antes de cada escena y guarda como propuestas las narraciones que quieras revisar en Umbral.']];for(const [title,body] of sections){const n=el('section',undefined,'connection-note');n.append(el('strong',title),el('p',body));container.append(n);}if(s.cloud){container.append(button('Ver accesos autorizados en Historial',()=>{$('action-dialog').close();navigate('history');},'text-button'));}else{const link=el('a','Documentación del protocolo MCP');link.href='https://modelcontextprotocol.io/introduction';link.target='_blank';link.rel='noopener noreferrer';container.append(link);}$('action-submit').hidden=true;actionSubmit=null;openDialog('action-dialog');});
  $('record-search').addEventListener('input',renderRecords);$('record-kind').addEventListener('change',renderRecords);options($('record-kind'),Object.entries(kinds).map(([id,name])=>({id,name})),'','Todos los tipos');
  [['new-detail','detail'],['new-record','fact'],['new-character','character'],['new-event','event'],['story-new-event','event'],['new-relationship','relationship']].forEach(([id,kind])=>$(id).addEventListener('click',()=>openEditor(kind)));
  $('editor-kind').addEventListener('change',()=>{editor.data=defaults($('editor-kind').value);editor.advanced=false;$('editor-json').value=JSON.stringify(editor.data,null,2);basicFields();});$('editor-world').addEventListener('change',()=>{if(!editor.advanced)captureBasic();basicFields();});
  function captureBasic(){
    for(const input of $('basic-fields').querySelectorAll('[data-key]')){
      const key=input.dataset.key;if(key.startsWith('_'))continue;
      const fmt=input.dataset.format;let value=input.value;
      if(fmt==='json')value=JSON.parse(value);
      else if(fmt==='multiple')value=Array.from(input.selectedOptions).map(o=>o.value);
      else if(fmt==='lines')value=value.split('\n').map(x=>x.trim()).filter(Boolean);
      else if(fmt==='description'){const previous=editor.data[key]||{};value=(value||Object.hasOwn(previous,'description'))?{...previous,description:value}:{...previous};}
      else if(fmt==='dimensions'){const entries=value.split('\n').filter(x=>x.trim()).map(line=>{const split=line.indexOf(':');if(split<1||!line.slice(split+1).trim())throw Error('Escribe cada matiz como Confianza: cautelosa.');return [line.slice(0,split).trim(),line.slice(split+1).trim()];});value=Object.fromEntries(entries);}
      else if(fmt==='number')value=Number(value);
      else if(fmt==='optional-number')value=value===''?null:Number(value);
      else if(fmt==='optional')value=value||null;
      editor.data[key]=value;
    }
    if($('editor-kind').value==='detail')editor.data.connections=Array.from($('detail-connections').querySelectorAll('.detail-connection')).map(row=>({target_id:row.querySelector('.detail-target').value,relation:row.querySelector('.detail-relation').value,certainty:row.querySelector('.detail-certainty').value,evidence_ids:Array.from(row.querySelector('.detail-evidence').selectedOptions).map(o=>o.value)}));
    if($('editor-kind').value==='character'){
      editor.data.roles=Array.from($('character-roles').querySelectorAll('.role-row')).map(row=>({place_id:row.querySelector('.role-place').value,label:row.querySelector('.role-label').value,exclusive:row.querySelector('.role-exclusive').checked}));
      const target=editor.target||record(editor.proposal?.target_id);
      if(target){const mode=$('field-_change_mode').value;editor.data.change_basis=mode?{mode,reason:$('field-_change_reason').value,based_on_revision:target.revision,evidence_ids:Array.from($('field-_change_evidence').selectedOptions).map(o=>o.value)}:null;}
    }
  }
  $('basic-fields').addEventListener('input',()=>{if(editor&&!editor.advanced){try{captureBasic();$('editor-json').value=JSON.stringify(editor.data,null,2);}catch(_){/* incomplete JSON stays editable */}}});$('editor-json').addEventListener('input',()=>editor.advanced=true);
  $('editor-form').addEventListener('submit',safe(async e=>{e.preventDefault();await busy(e.submitter,async()=>{if(!editor.advanced)captureBasic();const old=editor.proposal;const target=editor.target;const rec={kind:$('editor-kind').value,name:$('editor-name').value,world_id:$('editor-world').value||null,campaign_id:$('editor-campaign').value||null,source_id:target?.source_id||old?.record.source_id||editor.sourceId||null,source_ref:{...(target?.source_ref||old?.record.source_ref||editor.sourceRef||{}),proposal_reason:$('editor-decision').value},data:editor.advanced?JSON.parse($('editor-json').value):editor.data};await api('/universes/'+s.uid+'/proposals',{title:$('editor-decision').value.slice(0,240),expected_version:editor.base,operation:target?'replace':old?.operation||'create',target_id:target?.id||old?.target_id||null,record:rec});if(old)await api('/proposals/'+old.id+'/reject',{decision:'Sustituida por una propuesta corregida: '+$('editor-decision').value});$('editor-dialog').close();await refresh();navigate('review');notify('Propuesta preparada. Revisa su contenido antes de incorporarla.');});}));
  ['advance-time','story-advance'].forEach(id=>$(id).addEventListener('click',advance));
  $('switch-protagonist').addEventListener('click',()=>{const c=campaign(),v=version();const hero=actionField('Quién será tu protagonista','select',records('character').filter(r=>r.world_id===c.world_id&&r.id!==c.data.protagonist_id));const reason=actionField('Por qué cambia la perspectiva');action('Continuar con otro protagonista',[hero.wrap,reason.wrap],async()=>{await api('/campaigns/'+c.id+'/protagonist',{character_id:hero.input.value,expected_version:v,reason:reason.input.value});$('action-dialog').close();await refresh();notify('Protagonista actualizado. La historia anterior sigue conservada.');},'Cambiar protagonista');});
  $('revert-last').addEventListener('click',()=>{const v=version();const reason=actionField('Motivo de la reversión');action('Revertir el último cambio',[el('p',s.snapshot.revisions[0]?.summary||'No hay cambios anteriores.'),el('p','Se deshará únicamente esta modificación del canon y sus efectos. Quedará registrada una nueva versión.'),reason.wrap],async()=>{await api('/universes/'+s.uid+'/revert',{expected_version:v,decision:reason.input.value});$('action-dialog').close();await refresh();notify('Reversión registrada. Puedes consultar ambas versiones en el historial.');},'Revertir cambio');});
  $('action-form').addEventListener('submit',safe(async e=>{e.preventDefault();if(actionSubmit)await busy(e.submitter,actionSubmit);}));
  $('import-form').addEventListener('submit',safe(async e=>{e.preventDefault();await busy(e.submitter,async()=>{const file=$('import-file').files[0];if(file.size>2*1024*1024)throw Error('El archivo supera 2 MB. Divídelo en sesiones conservando sus títulos.');const result=await api('/universes/'+s.uid+'/import',{filename:file.name,content:await file.text(),campaign_id:s.cid||null,world_id:campaign()?.world_id||null});$('import-result').hidden=false;$('import-result').textContent=result.count+' candidatos preparados.\n'+JSON.stringify({avisos:result.warnings,nombres_por_revisar:result.name_matches},null,2);await refresh();notify('Original conservado. Hay '+result.count+' candidatos para revisar.');});}));
  // Refresh shared changes without replacing an open form or a review in progress.
  async function sync(){if(s.loading||!s.uid||document.hidden||document.querySelector('dialog[open]')||s.tab==='review')return;s.loading=true;try{const snap=await api('/universes/'+s.uid);const turnData=s.cid?await api('/campaigns/'+s.cid+'/turns'):null;if(snap.universe.version!==version()||JSON.stringify(snap.proposals)!==JSON.stringify(s.snapshot.proposals)||(turnData&&JSON.stringify(turnData.turns)!==JSON.stringify(s.turns))){saveDraft();await refresh();notify('Archivo actualizado con los cambios guardados.');}}catch(error){if(error.status===401)notify('Tu sesión terminó. Recarga la página para entrar otra vez.',true);}finally{s.loading=false;}}
  setInterval(sync,15000);window.addEventListener('focus',sync);
  $('memory-form').addEventListener('submit',safe(async e=>{e.preventDefault();await searchMemory(0);}));
  $('memory-audience').addEventListener('change',safe(()=>searchMemory(0)));
  $('memory-prev').addEventListener('click',safe(()=>searchMemory(Math.max(0,memory.offset-25))));
  $('memory-next').addEventListener('click',safe(()=>memory.next===null?null:searchMemory(memory.next)));
  bootstrap().catch(error=>{if(error.status!==401)showError(error);});
})();
