/* Device/browser speech only. No audio is uploaded to the Umbral backend. */
'use strict';
(() => {
  const input=document.getElementById('turn-content'),form=document.getElementById('turn-form');
  if(!input||!form)return;
  const node=(tag,text)=>{const n=document.createElement(tag);if(text)n.textContent=text;return n;};
  const button=(text,fn)=>{const b=node('button',text);b.type='button';b.className='button secondary';b.addEventListener('click',fn);return b;};
  const box=node('section');box.className='voice-controls';box.setAttribute('aria-label','Voz y lectura');
  const status=node('p');status.id='voice-status';status.setAttribute('role','status');
  const hint=node('p','También puedes tocar el campo y usar el micrófono del teclado del teléfono. Revisa el texto y pulsa Enviar y narrar cuando esté listo.');hint.className='help-note';
  const privacy=node('p','El dictado y las voces los gestiona tu navegador o dispositivo y pueden necesitar internet. Umbral no guarda el audio ni añade una API de voz de pago.');privacy.className='help-note';
  let listening=null,reading=0;
  const context=()=>['universe-select','campaign-select','turn-role'].map(id=>document.getElementById(id)?.value||'').join('|');
  const stopReading=()=>{reading++;window.speechSynthesis?.cancel();if(window.speechSynthesis?.paused)window.speechSynthesis.resume();};
  const chunks=text=>{
    const result=[];let part='';
    for(const word of text.split(/\s+/)){if(part.length+word.length>900&&part){result.push(part);part='';}part+=(part?' ':'')+word;}
    if(part)result.push(part);return result;
  };
  const canRead=!!(window.speechSynthesis&&window.SpeechSynthesisUtterance);
  function read(text){
    if(!canRead){status.textContent='Este navegador no ofrece lectura en voz alta. Puedes usar la lectura de pantalla del dispositivo.';return;}
    if(listening){status.textContent='Termina el dictado antes de escuchar la narración.';return;}
    stopReading();const token=reading,parts=chunks(text);let index=0;
    const next=()=>{
      if(token!==reading)return;
      if(index>=parts.length){status.textContent='Lectura terminada.';return;}
      const utterance=new window.SpeechSynthesisUtterance(parts[index++]);utterance.lang='es-ES';
      const voices=window.speechSynthesis.getVoices();utterance.voice=voices.find(v=>/^es[-_]/i.test(v.lang))||null;
      utterance.onend=next;utterance.onerror=()=>{if(token===reading){reading++;status.textContent='No se pudo continuar la lectura. Pulsa Escuchar para reintentar.';}};
      status.textContent='Leyendo narración…';window.speechSynthesis.speak(utterance);
    };
    next();
  }
  const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
  function finishDictation(abort=false){
    const session=listening;if(!session)return;
    if(abort){listening=null;input.readOnly=session.wasReadOnly;session.recognition.abort();dictate.textContent='🎙 Dictar';}
    else session.recognition.stop();
  }
  const dictate=button('🎙 Dictar',()=>{
    if(listening){finishDictation();return;}
    if(input.disabled){status.textContent='Selecciona una campaña y espera a que termine el envío antes de dictar.';return;}
    if(!Recognition){input.focus();status.textContent='Usa el micrófono del teclado de tu teléfono para dictar en este campo.';return;}
    stopReading();
    const recognition=new Recognition();recognition.lang='es-ES';recognition.continuous=true;recognition.interimResults=true;
    const session={recognition,scope:context(),base:input.value,start:input.selectionStart??input.value.length,end:input.selectionEnd??input.value.length,wasReadOnly:input.readOnly,finals:new Map()};
    listening=session;input.readOnly=true;
    recognition.onresult=event=>{
      if(listening!==session)return;
      if(session.scope!==context()){finishDictation(true);status.textContent='Dictado detenido al cambiar de campaña. No se añadió texto a otro contexto.';return;}
      let interim='';
      for(let i=0;i<event.results.length;i++){if(event.results[i].isFinal)session.finals.set(i,event.results[i][0].transcript);else interim+=event.results[i][0].transcript;}
      const text=[...session.finals.entries()].sort((a,b)=>a[0]-b[0]).map(x=>x[1].trim()).filter(Boolean).join(' ');
      if(text){const before=session.base.slice(0,session.start),after=session.base.slice(session.end);input.value=before+(before&&!/\s$/.test(before)?' ':'')+text+(after&&!/^\s/.test(after)?' ':'')+after;input.dispatchEvent(new Event('input',{bubbles:true}));}
      status.textContent=interim?'Escuchando: '+interim:'Escuchando… Pulsa Terminar dictado cuando acabes.';
    };
    recognition.onerror=event=>{if(listening===session)status.textContent=event.error==='not-allowed'?'El micrófono no está autorizado. Puedes usar el dictado del teclado o revisar los permisos del navegador.':'El dictado se interrumpió. El texto ya transcrito se conserva; puedes continuar con el teclado.';};
    recognition.onend=()=>{if(listening!==session)return;listening=null;input.readOnly=session.wasReadOnly;dictate.textContent='🎙 Dictar';if(status.textContent.startsWith('Escuchando'))status.textContent='Dictado terminado. Revisa el texto antes de enviarlo.';};
    try{recognition.start();dictate.textContent='Terminar dictado';status.textContent='Escuchando… El navegador puede pedir permiso para el micrófono.';}
    catch(_){listening=null;input.readOnly=session.wasReadOnly;status.textContent='No se pudo iniciar el dictado. Usa el micrófono del teclado del teléfono.';}
  });
  const pause=button('Pausar / continuar',()=>{if(canRead){if(window.speechSynthesis.paused)window.speechSynthesis.resume();else window.speechSynthesis.pause();}});
  const stop=button('Detener lectura',()=>{stopReading();status.textContent='Lectura detenida.';});pause.hidden=stop.hidden=!canRead;
  box.append(dictate,pause,stop,status,hint,privacy);form.before(box);
  for(const id of ['universe-select','campaign-select','turn-role'])document.getElementById(id)?.addEventListener('change',()=>{finishDictation(true);dictate.textContent='🎙 Dictar';stopReading();});
  form.addEventListener('submit',event=>{
    if(listening){event.preventDefault();event.stopImmediatePropagation();finishDictation();status.textContent='Revisa la transcripción y pulsa Enviar y narrar para enviarla.';}
    stopReading();
  },true);
  document.addEventListener('visibilitychange',()=>{if(document.hidden){finishDictation(true);dictate.textContent='🎙 Dictar';stopReading();}});
  window.addEventListener('pagehide',()=>{finishDictation(true);stopReading();});
  window.UmbralVoice={read,canRead,stop:stopReading};
})();
