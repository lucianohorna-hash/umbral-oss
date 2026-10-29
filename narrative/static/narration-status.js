/* Clear distinction between a saved reply and canonical review. No model requests. */
'use strict';
(() => {
 const list=document.getElementById('turn-list'),status=document.querySelector('.play-status'),role=document.getElementById('turn-role');
 if(!list)return;
 const helper=document.createElement('p');helper.className='help-note';helper.id='narration-role-help';role?.closest('.composer-heading')?.after(helper);
 function update(){
  const last=list.querySelector('.turn:last-child');
  const busy=document.querySelector('#turn-form [type=submit]')?.disabled;
  for(const badge of list.querySelectorAll('.turn.narrator .badge')){
   if(badge.textContent==='Borrador · aún no es canon')badge.textContent='Respuesta guardada · canon pendiente de revisión';
  }
  const currentStatus=document.querySelector('.play-status');
  if(last?.classList.contains('narrator')&&!busy&&currentStatus&&!/guardando|escribiendo|consultando|en espera|no se pudo|no pudo|reintentar|interrump/i.test(currentStatus.textContent)){
   const text='La respuesta está guardada. Puedes seguir jugando; la revisión del canon no bloquea la conversación.';
   if(currentStatus.textContent!==text)currentStatus.textContent=text;
  }
  const text=role?.value==='narrator'?'Modo narrador: guardas una escena escrita por ti. Para jugar y recibir respuesta automática, selecciona Jugador.':'Modo jugador: Enviar y narrar guarda tu decisión y solicita la respuesta. Una generación puede tardar; no hace falta reenviar el texto.';
  if(helper.textContent!==text)helper.textContent=text;
 }
 new MutationObserver(update).observe(list,{childList:true,subtree:true});
 role?.addEventListener('change',update);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden){window.dispatchEvent(new Event('focus'));update();}});
 window.addEventListener('pageshow',()=>{window.dispatchEvent(new Event('focus'));update();});
 update();
})();
