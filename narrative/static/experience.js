/* Optional UI conveniences: all narrative mutations stay behind reviewed server use cases. */
'use strict';
window.createUmbralExperience=({state,api,el,button,action,actionField,refresh,bootstrap,navigate,notify})=>{
  const $=id=>document.getElementById(id), statuses=new Map();
  const status=el('p','','play-status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');$('turn-form').before(status);
  const tools=el('div',undefined,'experience-tools');
  $('turn-form').before(tools);
  const same=(uid,cid)=>state().uid===uid&&state().cid===cid;
  function displayStatus(){
    const s=state(),current=statuses.get(s.cid);
    status.textContent=current||(!s.cid?'':s.turns.at(-1)?.role==='user'?'Tu última intervención está guardada. Si falta la respuesta, pulsa Reintentar respuesta.':'Tus intervenciones y respuestas se conservan en esta campaña.');
  }
  function stage(cid,text){statuses.set(cid,text);if(state().cid===cid)displayStatus();}
  async function resume(){
    const {uid,cid}=state(),data=await api('/campaigns/'+cid+'/resume');if(!same(uid,cid))return;
    const nodes=[el('p',data.protagonist+' · '+data.location+' · día '+data.day),el('p','Estado: '+data.status)];
    if(data.injuries.length)nodes.push(el('p','Lesiones: '+data.injuries.join('; ')));
    nodes.push(el('h3','Lo que recuerda tu protagonista'));
    if(!data.reminders.length)nodes.push(el('p','Aún no hay recuerdos validados para esta perspectiva. Las escenas guardadas siguen en la conversación.'));
    for(const r of data.reminders)nodes.push(el('p',r.name+': '+r.text+' ('+(r.belief||r.certainty)+')'));
    nodes.push(el('p',data.pending_scenes+' propuestas pendientes de revisión.'),el('p',data.notice,'help-note'));
    action('Retomar mi historia',nodes,null);$('action-submit').hidden=true;
  }
  async function preferences(){
    const {uid,cid,snapshot}=state(),version=snapshot.universe.version;
    const data=await api('/campaigns/'+cid+'/preferences');if(!same(uid,cid))return;
    const choices={pace:['Ritmo',[['balanced','Equilibrado'],['brisk','Ágil · resumir trayectos'],['detailed','Pausado · desarrollar escenas']]],length:['Extensión',[['brief','Breve'],['medium','Media'],['long','Amplia']]],tone:['Tono',[['natural','Natural'],['adventure','Aventura'],['intimate','Cercano'],['dark','Oscuro']]],dialogue:['Diálogo',[['balanced','Equilibrado'],['more','Más diálogo'],['less','Conciso']]]};
    const fields=Object.entries(choices).map(([key,[label,options]])=>[key,actionField(label,'select',options.map(([id,name])=>({id,name})))]);
    for(const [key,f] of fields)f.input.value=data[key];
    action('Cómo quieres que se narre',[...fields.map(([,f])=>f.wrap),el('p','Estas preferencias no autorizan cambios de canon, decisiones por tu personaje ni encuentros forzados. Se guardan solo en esta campaña.','help-note')],async()=>{
      if(!same(uid,cid))throw Error('Vuelve a abrir las preferencias en la campaña elegida.');
      await api('/campaigns/'+cid+'/preferences',{expected_version:version,preferences:Object.fromEntries(fields.map(([k,f])=>[k,f.input.value]))});
      $('action-dialog').close();await refresh();notify('Preferencias guardadas para esta campaña.');
    },'Guardar preferencias');
  }
  function correct(turn){
    const {uid,cid,snapshot}=state(),version=snapshot.universe.version;
    const text=actionField('Escena corregida'),reason=actionField('Qué corriges y por qué');text.input.value=turn.content;text.input.rows=10;
    action('Corregir esta escena',[el('p','El original se conserva. Prepararás una propuesta para revisión; no cambia el canon automáticamente. Los efectos materiales requieren su propia revisión.'),text.wrap,reason.wrap],async()=>{
      if(!same(uid,cid))throw Error('Vuelve a abrir la corrección desde su campaña.');
      const result=await api('/campaigns/'+cid+'/turns/'+turn.id+'/correction',{expected_version:version,content:text.input.value,reason:reason.input.value});
      $('action-dialog').close();await refresh();navigate('review');notify('Corrección preparada. '+result.later_turns.length+' escenas posteriores por contrastar. '+result.warning);
    },'Preparar corrección');
  }
  function lookup(query=''){
    const {uid,cid}=state();const input=actionField('Nombre, objeto, símbolo o fragmento','text');input.input.value=query;
    const audience=actionField('Consultar como','select',[{id:'character',name:'Mi protagonista · sin secretos'},{id:'narrator',name:'Narrador · puede revelar secretos'}]);
    const results=el('div');let sequence=0;
    async function search(){
      const ticket=++sequence;const q=input.input.value.trim();if(!q)return;
      const data=await api('/universes/'+uid+'/memory?q='+encodeURIComponent(q)+'&audience='+audience.input.value+'&campaign_id='+cid+'&limit=12');
      if(ticket!==sequence||!same(uid,cid)||!results.isConnected)return;
      results.replaceChildren(el('p',data.results?.length?'Coincidencias del archivo. Una semejanza no confirma identidad ni conexión.':'Sin coincidencias visibles con estas palabras. Puedes probar una variante o consultar Huellas.'));
      for(const r of data.results||[]){const n=el('article',undefined,'record-card');n.append(el('strong',r.title||r.name||'Recuerdo'),el('p',r.excerpt||r.text||''),el('small',r.canonical?'Canon · '+(r.certainty||'registrado'):'Archivo original · no equivale a canon'));n.append(button('Leer entrada completa',async()=>{const detail=await api('/universes/'+uid+'/memory/'+r.origin+'/'+r.id+'?audience='+audience.input.value+'&campaign_id='+cid);if(!same(uid,cid))return;action(detail.title,[el('pre',detail.content||detail.text)],null);$('action-submit').hidden=true;},'text-button'));results.append(n);}
    }
    action('Consultar recuerdos',[input.wrap,audience.wrap,results],search,'Buscar');
    if(query)search().catch(error=>{results.textContent=error.message;});
  }
  function decorate(node){
    const s=state();const text=node.textContent;
    const names=[...new Set((s.snapshot?.records||[]).filter(r=>['character','item','detail','place'].includes(r.kind)&&(!r.world_id||r.world_id===s.snapshot.records.find(c=>c.id===s.cid)?.world_id)).flatMap(r=>[r.name,...(r.data.aliases||[])])).values()].filter(n=>n.length>=3&&text.includes(n)).sort((a,b)=>b.length-a.length);
    if(!names.length)return;
    const pattern=new RegExp(names.map(n=>n.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|'),'gu');let offset=0;const fragments=[];
    for(const m of text.matchAll(pattern)){if(fragments.length>120)break;fragments.push(document.createTextNode(text.slice(offset,m.index)));const link=button(m[0],()=>lookup(m[0]),'memory-link');link.setAttribute('aria-label','Consultar recuerdos de '+m[0]);fragments.push(link);offset=m.index+m[0].length;}
    fragments.push(document.createTextNode(text.slice(offset)));node.replaceChildren(...fragments);
  }
  async function recovery(){
    const {uid}=state(),box=$('automatic-recovery');if(!uid||!box)return;
    box.textContent='Consultando copias…';
    try{
      const {points}=await api('/universes/'+uid+'/recovery');if(state().uid!==uid)return;
      box.replaceChildren(el('p',points.length?'Última copia: '+new Date(points[0].created_at).toLocaleString('es'):'La primera copia se creará al guardar el próximo cambio.'),el('p','Se conserva el estado más reciente de hasta siete horas con actividad. Estas copias están en la misma base; descarga también una copia externa.','help-note'));
      for(const p of points){const row=el('div',undefined,'recovery-row');row.append(el('span',new Date(p.created_at).toLocaleString('es')+' · canon v'+p.version),button('Recuperar como universo separado',()=>{
        const label=actionField('Nombre del universo recuperado','text');label.input.value='Recuperado · '+new Date(p.created_at).toLocaleString('es');
        action('Recuperar sin sobrescribir',[el('p','Se crearán copias de los personajes, campañas, conversaciones e historial. Tu universo actual se conserva.'),label.wrap],async()=>{if(state().uid!==uid)throw Error('Vuelve a abrir esta copia desde su universo.');const data=await api('/universes/'+uid+'/recovery/'+p.id+'/restore',{name:label.input.value});$('action-dialog').close();await bootstrap(data.universe_id);notify('Copia recuperada en un universo separado.');},'Crear universo recuperado');
      },'button secondary'));box.append(row);}
    }catch(error){box.textContent=error.message;}
  }
  tools.append(button('Retomar mi historia',resume,'button secondary'),button('Preferencias de narración',preferences,'button secondary'),button('Consultar recuerdos',()=>lookup(),'button secondary'));
  return {stage,render(){displayStatus();tools.hidden=!state().cid;},correct,decorate,lookup,recovery};
};
