/* Reading position is UI-only: no canon, drafts, or server requests. */
'use strict';
(() => {
  const list=document.getElementById('turn-list'), form=document.getElementById('turn-form');
  if(!list||!form)return;
  const jump=document.createElement('button');
  jump.type='button';jump.className='button primary story-jump';jump.hidden=true;
  jump.textContent='↓ Ir al último mensaje';document.body.append(jump);
  let scope='',ids=[],pending=false,visible=true,opening=false,epoch=0;
  const nested=()=>list.scrollHeight>list.clientHeight+2;
  const bottom=()=>nested()?list.scrollHeight-list.clientHeight-list.scrollTop<100:
    form.getBoundingClientRect().top<window.innerHeight+100;
  const update=()=>{jump.hidden=!visible||opening||!ids.length||bottom();jump.textContent=pending?'↓ Nueva respuesta':'↓ Ir al último mensaje';};
  const latest=()=>{if(!visible)return;pending=false;list.scrollTop=list.scrollHeight;form.scrollIntoView({block:'end',behavior:'instant'});update();};
  const before=()=>{
    const top=nested()?list.getBoundingClientRect().top:0;
    const anchor=[...list.children].find(n=>n.dataset.turnId&&n.getBoundingClientRect().bottom>top);
    return {atBottom:bottom(),id:anchor?.dataset.turnId,offset:anchor?.getBoundingClientRect().top,scrollY:window.scrollY,scrollTop:list.scrollTop};
  };
  const after=(next,saved)=>{
    const changed=next!==scope,previous=new Set(ids);
    scope=next;ids=[...list.children].map(n=>n.dataset.turnId).filter(Boolean);
    const fresh=[...list.children].filter(n=>n.dataset.turnId&&!previous.has(n.dataset.turnId));
    const ticket=++epoch;
    if(changed)opening=true;
    requestAnimationFrame(()=>{
      if(ticket!==epoch)return;
      if(changed){opening=false;if(visible)latest();return;}
      if(!visible)return;
      if(fresh.length&&saved?.atBottom){
        const response=fresh.find(n=>n.classList.contains('narrator'));
        if(response){pending=false;response.scrollIntoView({block:'start',behavior:'instant'});}
        else latest();
      }else if(saved){
        const anchor=[...list.children].find(n=>n.dataset.turnId===saved.id);
        if(nested())list.scrollTop=saved.scrollTop;
        if(anchor&&saved.offset!==undefined){const delta=anchor.getBoundingClientRect().top-saved.offset;
          if(nested())list.scrollTop+=delta;else window.scrollBy(0,delta);
        }else window.scrollTo(0,saved.scrollY);
        if(fresh.some(n=>n.classList.contains('narrator')))pending=true;
      }
      update();
    });
  };
  jump.addEventListener('click',latest);
  window.addEventListener('scroll',update,{passive:true});list.addEventListener('scroll',update,{passive:true});
  window.addEventListener('resize',update,{passive:true});
  window.UmbralScroll={before,after,show(active,reopened=false){visible=active;if(active&&reopened)latest();update();}};
})();
