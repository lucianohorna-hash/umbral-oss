// Run the actual interface script with a tiny DOM/transport fixture, not a model.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.resolve(__dirname,'../../narrative/static/chatgpt-plan.js'),'utf8');

async function boot(provider, initial, failures={}){
  const nodes=new Map(), calls=[];
  let state={available:true,connected:false,message:'Sin conexión',...initial};
  class Element{
    constructor(tag){this.tag=tag;this.children=[];this.events={};this.hidden=false;this.disabled=false;this.value='';this.files=[];}
    set id(value){this._id=value;nodes.set(value,this);}get id(){return this._id;}
    append(...items){this.children.push(...items);}prepend(...items){this.children.unshift(...items);}
    replaceChildren(...items){this.children=items;}add(item){this.children.push(item);}
    setAttribute(key,value){this[key]=value;}closest(){return helpPanel;}
    addEventListener(name,fn){(this.events[name]??=[]).push(fn);}
    async emit(name){for(const fn of this.events[name]||[])await fn({target:this});}
  }
  const aside=new Element('aside'),helpPanel=new Element('section');
  for(const id of ['generate-turn','chatgpt-help']){const n=new Element('button');n.id=id;}
  const document={createElement:tag=>new Element(tag),createTextNode:text=>({textContent:text}),
    getElementById:id=>nodes.get(id),querySelector:selector=>selector==='.story-aside'?aside:null};
  const fetch=async(url,options={})=>{
    calls.push({url,options});
    let data;
    if(url==='/api/bootstrap')data={provider,cloud:provider==='chatgpt-plan-self-hosted',csrf_token:'fictitious-csrf'};
    else if(failures[url])return {ok:false,json:async()=>({detail:'Fallo simulado'})};
    else if(url==='/api/chatgpt-plan')data={...state};
    else if(url==='/api/chatgpt-plan/models'){
      if(state.needs_verification)state={...state,connected:true,needs_verification:false,model:'small-test'};
      data={models:[{id:'small-test',name:'Modelo ficticio'}]};
    }else if(url==='/api/chatgpt-plan/prepare-transfer'){
      assert.equal(JSON.parse(options.body).target_origin,'https://umbral.example');
      data={ciphertext:'encrypted-only',target_origin:'https://umbral.example'};state={...state,connected:false,model:''};
    }else if(url==='/api/chatgpt-plan/handoff')data={target_origin:'https://umbral.example',public_key:'PUBLIC-ONLY'};
    else throw Error('Unexpected fixture request: '+url);
    return {ok:true,json:async()=>data};
  };
  vm.runInNewContext(source,{document,fetch,Option:function(name,value){this.textContent=name;this.value=value;},
    window:{location:{origin:'https://umbral.example',assign(){}}},Blob,URL,setTimeout,JSON,Error});
  for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));
  const all=()=>{const result=[];const walk=n=>{result.push(n);for(const child of n.children||[])walk(child);};walk(aside);return result;};
  return {nodes,calls,aside,all,byText:text=>all().find(n=>n.textContent===text)};
}

test('server panel appears on the hosted app and enables the selected model',async()=>{
  const ui=await boot('chatgpt-plan-self-hosted',{connected:true,session_present:true,model:'small-test'});
  assert.equal(ui.aside.children[0].hidden,false);
  assert.ok(ui.byText('Narrador de tu servidor'));
  assert.equal(ui.byText('Continue with ChatGPT').hidden,true);
  assert.equal(ui.nodes.get('plan-auto-narrate').checked,true);
  assert.equal(ui.nodes.get('plan-auto-narrate').disabled,false);
  assert.equal(ui.nodes.get('generate-turn').disabled,false);
  assert.ok(ui.calls.some(c=>c.url.endsWith('/models')));
});

test('unprovisioned server never promises an automatic answer or starts OAuth',async()=>{
  const ui=await boot('chatgpt-plan-self-hosted',{});
  assert.equal(ui.nodes.get('plan-auto-narrate').disabled,true);
  assert.equal(ui.nodes.get('generate-turn').disabled,true);
  assert.equal(ui.byText('Continue with ChatGPT').hidden,true);
  assert.deepEqual(ui.calls.map(c=>c.url),['/api/bootstrap','/api/chatgpt-plan']);
});

test('catalogue failure keeps the panel visible and permits verification retry',async()=>{
  const ui=await boot('chatgpt-plan-self-hosted',{connected:true,session_present:true,model:'small-test'},
    {'/api/chatgpt-plan/models':true});
  assert.equal(ui.aside.children[0].hidden,false);
  assert.equal(ui.nodes.get('plan-auto-narrate').disabled,true);
  assert.equal(ui.nodes.get('generate-turn').disabled,true);
  assert.equal(ui.byText('Verificar conexión').hidden,false);
  assert.equal(ui.byText('Desconectar mi plan').hidden,false);
});

test('verification of received session enables play without opening ChatGPT',async()=>{
  const ui=await boot('chatgpt-plan-self-hosted',{session_present:true,needs_verification:true});
  assert.equal(ui.nodes.get('plan-auto-narrate').disabled,true);
  await ui.byText('Verificar conexión').emit('click');
  assert.equal(ui.nodes.get('plan-auto-narrate').disabled,false);
  assert.equal(ui.nodes.get('generate-turn').disabled,false);
  assert.ok(!ui.calls.some(c=>c.url.endsWith('/connect')));
});

test('local transfer uses explicit file and target, and exposes only encrypted output',async()=>{
  const ui=await boot('chatgpt-plan',{connected:true,model:'small-test'});
  const file=ui.nodes.get('local-handoff-file');
  file.files=[{size:100,text:async()=>JSON.stringify({version:1,target_origin:'https://umbral.example',public_key:'PUBLIC-ONLY',host_id:'fictitious',transfer_id:'fictitious',expires:1234})}];
  await file.emit('change');
  assert.equal(ui.nodes.get('local-handoff-origin').value,'https://umbral.example');
  assert.equal(ui.nodes.get('local-handoff-origin').readOnly,true);
  await ui.nodes.get('local-handoff-prepare').emit('click');
  assert.equal(JSON.parse(ui.nodes.get('local-handoff-packet').value).ciphertext,'encrypted-only');
  assert.equal(ui.nodes.get('plan-auto-narrate').disabled,true);
});

test('manual provider does not add a connection panel',async()=>{
  const ui=await boot('manual',{});
  assert.equal(ui.aside.children.length,0);
});

test('raw credential files are rejected before any server upload',async()=>{
  const ui=await boot('chatgpt-plan-self-hosted',{});
  ui.nodes.get('server-handoff-file').files=[{size:100,text:async()=>JSON.stringify({access_token:'DO-NOT-UPLOAD',refresh_token:'DO-NOT-UPLOAD'})}];
  await ui.nodes.get('server-handoff-import').emit('click');
  assert.ok(!ui.calls.some(c=>c.url.endsWith('/import')));
  assert.ok(!ui.all().some(n=>(n.textContent||'').includes('DO-NOT-UPLOAD')));
});
