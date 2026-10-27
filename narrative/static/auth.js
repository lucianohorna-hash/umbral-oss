/* Access layer; the narrative UI remains independent of account providers. */
(async function () {
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
  function note(text) { const n = document.getElementById('notification'); if(n) { n.hidden = false; n.textContent = text; } }
  window.addEventListener('offline', () => note('Sin conexión. Tu texto pendiente permanece en este navegador; todavía no se ha guardado en el servidor.'));
  window.addEventListener('online', () => note('Conexión recuperada. Revisa y envía tu texto pendiente; no se enviará automáticamente.'));
  try {
    const auth = await fetch('/api/auth', {cache:'no-store'}).then(r => r.json());
    if (!auth.cloud) return;
    if (auth.authenticated) {
      const button = document.createElement('button'); button.className='text-button'; button.textContent='Cerrar sesión';
      button.addEventListener('click', async () => {
        await fetch('/api/logout', {method:'POST',headers:{'X-CSRF-Token':auth.csrf_token}});
        for (let i=localStorage.length-1;i>=0;i--) { const k=localStorage.key(i); if(k && (k.startsWith('umbral') || k.startsWith('multimundo'))) localStorage.removeItem(k); }
        location.reload();
      });
      document.querySelector('.topbar .actions').append(button);
      return;
    }
    const dialog=document.createElement('dialog'); dialog.setAttribute('aria-label','Entrar en tu archivo privado');
    const form=document.createElement('form');
    const title=document.createElement('h2');title.textContent='Tu universo, en privado';
    const description=document.createElement('p');description.textContent='Usa la misma dirección y contraseña en PC y iPhone para continuar la misma historia.';
    const label=document.createElement('label');label.textContent='Contraseña';label.htmlFor='cloud-password';
    const password=document.createElement('input');password.type='password';password.id='cloud-password';password.required=true;password.autocomplete='current-password';
    const result=document.createElement('p'); result.setAttribute('role','status');
    const button=document.createElement('button');button.type='submit';button.className='button primary';button.textContent='Entrar';
    form.append(title,description,label,password,result,button); dialog.append(form);document.body.append(dialog);
    dialog.addEventListener('cancel', e=>e.preventDefault());
    form.addEventListener('submit',async e=>{
      e.preventDefault();button.disabled=true;
      try{
        const fresh=await fetch('/api/auth',{cache:'no-store'}).then(r=>r.json());
        const response=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':fresh.csrf_token},body:JSON.stringify({password:password.value})});
        password.value='';
        if(!response.ok) { const data=await response.json(); throw Error(data.detail || 'No se pudo iniciar sesión.'); }
        location.reload();
      }catch(error){result.textContent=error.message;button.disabled=false;}
    });
    dialog.showModal();
  } catch (_) { note('No se pudo conectar al archivo. Comprueba que el servidor esté iniciado o que tengas conexión.'); }
})();
