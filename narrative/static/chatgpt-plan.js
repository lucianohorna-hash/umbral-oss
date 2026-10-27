/* Personal plan connection: OAuth credentials stay in the local server. */
'use strict';
(() => {
  let csrf = '', panel, status, connect, models, disconnect;
  const request = async (path, body) => {
    const r = await fetch('/api/chatgpt-plan'+path, body === undefined ? {cache:'no-store'} : {
      method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body)});
    const data = await r.json();
    if(!r.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'No se pudo completar la conexión.');
    return data;
  };
  const action = fn => async () => { try { await fn(); } catch(e) { status.textContent=e.message; } };
  async function refresh() {
    const state = await request('');
    if(!state.available) return;
    panel.hidden=false;
    connect.hidden=state.connected;disconnect.hidden=!state.connected;models.hidden=!state.connected;
    document.getElementById('plan-auto-narrate').disabled=!state.connected;
    document.getElementById('generate-turn').disabled=!state.connected||!state.model;
    status.textContent=state.connected ? 'Cuenta conectada'+(state.account?' · '+state.account:'')+(state.model?' · '+state.model:' · Elige un modelo para empezar.') : state.message;
    if(state.connected) {
      const list = await request('/models');
      models.replaceChildren(new Option('Elige el modelo de tu plan',''));
      for(const m of list.models) models.add(new Option(m.name,m.id));
      models.value=state.model||'';
    }
  }
  async function init() {
    const boot = await fetch('/api/bootstrap',{cache:'no-store'}).then(r=>r.json());
    if(boot.provider!=='chatgpt-plan'||boot.cloud) return;
    csrf=boot.csrf_token;
    panel=document.createElement('section');panel.className='panel quiet';panel.hidden=true;
    const title=document.createElement('h3');title.textContent='Jugar aquí con mi plan ChatGPT';
    status=document.createElement('p');status.setAttribute('role','status');
    connect=document.createElement('button');connect.type='button';connect.className='button secondary';connect.textContent='Continue with ChatGPT';
    models=document.createElement('select');models.setAttribute('aria-label','Modelo de mi plan ChatGPT');
    disconnect=document.createElement('button');disconnect.type='button';disconnect.className='text-button';disconnect.textContent='Desconectar mi plan';
    const note=document.createElement('p');note.className='help-note';note.textContent='Escribe y guarda tu decisión abajo. El narrador responderá aquí; puedes desactivar la respuesta automática. La respuesta se conserva como propuesta. No se activa facturación de API al agotar tu plan.';
    const automatic=document.createElement('label');
    const toggle=document.createElement('input');toggle.type='checkbox';toggle.id='plan-auto-narrate';toggle.checked=true;
    automatic.append(toggle,document.createTextNode(' El narrador responde al guardar mi decisión'));
    panel.append(title,status,connect,models,disconnect,automatic,note);
    document.querySelector('.story-aside').prepend(panel);
    document.getElementById('chatgpt-help').closest('.panel').hidden=true;
    connect.addEventListener('click',action(async()=>{
      connect.disabled=true;
      try { const data=await request('/connect',{}); window.location.assign(data.url); }
      finally {connect.disabled=false;}
    }));
    models.addEventListener('change',action(async()=>{if(models.value) {await request('/model',{model:models.value});await refresh();}}));
    disconnect.addEventListener('click',action(async()=>{const result=await request('/disconnect',{});await refresh();status.textContent=result.message;}));
    await refresh();
  }
  init().catch(()=>{});
})();
