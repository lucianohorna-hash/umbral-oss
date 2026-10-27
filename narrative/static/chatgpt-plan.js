/* Personal plan connection: OAuth credentials stay in the local server. */
'use strict';
(() => {
  let csrf = '', panel, status, connect, models, disconnect, verify, prepare, remote=false;
  const request = async (path, body) => {
    const r = await fetch('/api/chatgpt-plan'+path, body === undefined ? {cache:'no-store'} : {
      method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body)});
    const data = await r.json();
    if(!r.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'No se pudo completar la conexión.');
    return data;
  };
  const action = fn => async () => { try { await fn(); } catch(e) { status.textContent=e.message; } };
  async function readFile(file,limit,keys){
    if(!file||file.size>limit)throw Error('Selecciona el archivo de conexión adecuado.');
    let data;try{data=JSON.parse(await file.text());}catch(_){throw Error('El archivo de conexión no tiene un formato válido.');}
    if(!data||typeof data!=='object'||Array.isArray(data)||Object.keys(data).sort().join(',')!==keys.slice().sort().join(','))throw Error('El archivo no corresponde a este paso de conexión.');
    return data;
  }
  async function refresh() {
    let state = await request(''), list, catalogueError;
    if(!state.available) return;
    panel.hidden=false;
    if(state.connected){try{list=await request('/models');state=await request('');}catch(error){catalogueError=error;}}
    connect.hidden=remote||state.connected;disconnect.hidden=!(state.connected||state.session_present);models.hidden=!state.connected||!!catalogueError;
    verify.hidden=!state.needs_verification&&!catalogueError;
    if(remote)prepare.disabled=!!state.session_present;
    document.getElementById('plan-auto-narrate').disabled=!state.connected||!state.model||!!catalogueError;
    document.getElementById('generate-turn').disabled=!state.connected||!state.model||!!catalogueError;
    status.textContent=catalogueError?'No se pudo comprobar la conexión del plan. Puedes verificarla de nuevo; tu campaña sigue guardada.':state.connected ? 'Cuenta conectada'+(state.account?' · '+state.account:'')+(state.model?' · '+state.model:' · Elige un modelo para empezar.') : state.message;
    if(state.connected&&list) {
      models.replaceChildren(new Option('Elige el modelo de tu plan',''));
      for(const m of list.models) models.add(new Option(m.name,m.id));
      models.value=state.model||'';
    }
  }
  function control(tag,text,id){const node=document.createElement(tag);if(text)node.textContent=text;if(id)node.id=id;return node;}
  function fileField(label,id){const wrap=control('label',label),input=control('input','',id);input.type='file';input.accept='.json';wrap.append(input);return {wrap,input};}
  function fileOutput(title,id,filename){
    const details=control('details'),summary=control('summary',title),text=control('textarea','',id);
    text.readOnly=true;text.rows=4;text.setAttribute('aria-label',title);details.append(summary,text);
    const save=control('button','Descargar archivo');save.type='button';save.className='button secondary';save.disabled=true;
    save.addEventListener('click',()=>{const url=URL.createObjectURL(new Blob([text.value],{type:'application/json'}));const link=control('a');link.href=url;link.download=filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
    return {details,text,save};
  }
  function setupTransfer(){
    const setup=control('details'),summary=control('summary',remote?'Configurar la conexión del servidor':'Usar esta conexión en mi servidor');setup.append(summary);
    if(remote){
      setup.append(control('p','Esta preparación se hace una sola vez con la instalación del computador. Después podrás jugar desde aquí sin mantenerlo encendido.'));
      prepare=control('button','Preparar archivo de conexión');prepare.type='button';prepare.className='button secondary';prepare.id='server-handoff-prepare';
      const descriptor=fileOutput('Ver archivo de conexión','server-handoff-descriptor','umbral-servidor.json');
      prepare.addEventListener('click',action(async()=>{const data=await request('/handoff',{});descriptor.text.value=JSON.stringify(data,null,2);descriptor.save.disabled=false;status.textContent='Archivo de conexión preparado. Úsalo en la instalación del computador; tiene una vigencia de 15 minutos.';}));
      const incoming=fileField('Paquete cifrado del computador','server-handoff-file');
      const receive=control('button','Conectar con el paquete cifrado');receive.type='button';receive.className='button secondary';receive.id='server-handoff-import';
      receive.addEventListener('click',action(async()=>{const packet=await readFile(incoming.input.files[0],220000,['version','target_origin','host_id','transfer_id','expires','nonce','wrapped_key','ciphertext']);if(packet.version!==1||packet.target_origin!==window.location.origin||!['nonce','wrapped_key','ciphertext'].every(key=>typeof packet[key]==='string'&&packet[key]))throw Error('El paquete cifrado no corresponde a este servidor.');await request('/import',{packet});await refresh();try{await request('/models');await refresh();}catch(_){status.textContent='Paquete recibido. No se pudo verificar el plan todavía; usa «Verificar conexión» para reintentar.';}}));
      setup.append(prepare,descriptor.save,descriptor.details,incoming.wrap,receive);
    }else{
      setup.append(control('p','Selecciona el archivo que preparó tu servidor. Al crear el paquete, el computador dejará de usar esta sesión. Solo el servidor elegido podrá abrir el paquete cifrado.'));
      const incoming=fileField('Archivo de conexión de mi servidor','local-handoff-file');
      const targetLabel=control('label','Servidor elegido'),target=control('input','','local-handoff-origin');target.type='url';target.readOnly=true;targetLabel.append(target);
      const seal=control('button','Preparar traslado cifrado');seal.type='button';seal.className='button secondary';seal.id='local-handoff-prepare';seal.disabled=true;
      let descriptor;
      incoming.input.addEventListener('change',action(async()=>{seal.disabled=true;descriptor=null;target.value='';descriptor=await readFile(incoming.input.files[0],12000,['version','target_origin','host_id','transfer_id','expires','public_key']);if(typeof descriptor.target_origin!=='string')throw Error('El archivo no contiene un servidor válido.');target.value=descriptor.target_origin;seal.disabled=false;}));
      const output=fileOutput('Ver paquete cifrado','local-handoff-packet','umbral-conexion-cifrada.json');
      seal.addEventListener('click',action(async()=>{if(!descriptor)throw Error('Selecciona el archivo de tu servidor.');const packet=await request('/prepare-transfer',{descriptor,target_origin:target.value});output.text.value=JSON.stringify(packet,null,2);output.save.disabled=false;await refresh();status.textContent='Paquete cifrado preparado. La sesión local quedó desactivada. Cárgalo en tu servidor para terminar la conexión; puedes recuperar este mismo paquete si se perdió la confirmación.';}));
      setup.append(incoming.wrap,targetLabel,seal,output.save,output.details);
    }
    panel.append(setup);
  }
  async function init() {
    const boot = await fetch('/api/bootstrap',{cache:'no-store'}).then(r=>r.json());
    remote=boot.provider==='chatgpt-plan-self-hosted';
    if(!remote&&(boot.provider!=='chatgpt-plan'||boot.cloud)) return;
    csrf=boot.csrf_token;
    panel=document.createElement('section');panel.className='panel quiet';panel.hidden=true;
    const title=document.createElement('h3');title.textContent=remote?'Narrador de tu servidor':'Jugar aquí con mi plan ChatGPT';
    status=document.createElement('p');status.setAttribute('role','status');
    connect=document.createElement('button');connect.type='button';connect.className='button secondary';connect.textContent='Continue with ChatGPT';
    models=document.createElement('select');models.setAttribute('aria-label','Modelo de mi plan ChatGPT');
    disconnect=document.createElement('button');disconnect.type='button';disconnect.className='text-button';disconnect.textContent='Desconectar mi plan';
    verify=control('button','Verificar conexión');verify.type='button';verify.className='button secondary';verify.hidden=true;
    const note=document.createElement('p');note.className='help-note';note.textContent='Cuando la conexión esté verificada y hayas elegido un modelo, guarda tu decisión abajo: el narrador responderá aquí. Puedes desactivar la respuesta automática. La escena queda como propuesta. No se activa una API de pago al agotar tu plan.';
    const automatic=document.createElement('label');
    const toggle=document.createElement('input');toggle.type='checkbox';toggle.id='plan-auto-narrate';toggle.checked=true;toggle.disabled=true;
    document.getElementById('generate-turn').disabled=true;
    automatic.append(toggle,document.createTextNode(' El narrador responde al guardar mi decisión'));
    panel.append(title,status,connect,models,verify,disconnect,automatic,note);
    setupTransfer();
    document.querySelector('.story-aside').prepend(panel);
    document.getElementById('chatgpt-help').closest('.panel').hidden=true;
    connect.addEventListener('click',action(async()=>{
      connect.disabled=true;
      try { const data=await request('/connect',{}); window.location.assign(data.url); }
      finally {connect.disabled=false;}
    }));
    models.addEventListener('change',action(async()=>{if(models.value) {await request('/model',{model:models.value});await refresh();}}));
    verify.addEventListener('click',action(async()=>{await request('/models');await refresh();}));
    disconnect.addEventListener('click',action(async()=>{const result=await request('/disconnect',{});await refresh();status.textContent=result.message;}));
    await refresh();
  }
  init().catch(error=>{if(status){panel.hidden=false;status.textContent=error.message;}});
})();
