const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
function boot(){
 const nodes=new Map(),fields=new Map(),requests=[],actions=[];const state={uid:'u',cid:'c',turns:[],snapshot:{universe:{version:1},records:[]}};
 class Element{
  constructor(tag,text=''){this.tag=tag;this.children=[];this._text=text;this.events={};this.value='';this.isConnected=false;this.open=false;}
  append(...items){for(const n of items){n.isConnected=this.isConnected;this.children.push(n);}}
  replaceChildren(...items){const detach=n=>{n.isConnected=false;for(const c of n.children||[])detach(c);};for(const c of this.children)detach(c);this.children=[];this._text='';this.append(...items);}
  before(){}setAttribute(){}addEventListener(type,fn){this.events[type]=fn;}
  get textContent(){return this._text+this.children.map(n=>n.textContent||'').join(' ');}set textContent(value){this.replaceChildren();this._text=value;}
 }
 for(const id of ['turn-form','action-dialog','action-fields','action-submit']){const n=new Element('div');n.isConnected=true;nodes.set(id,n);}
 const el=(tag,text='',cls)=>new Element(tag,text||'');const button=(text,fn)=>{const b=el('button',text);b.click=fn;return b;};
 const api=url=>new Promise((resolve,reject)=>requests.push({url,resolve,reject}));
 const actionField=(label,type,choices)=>{const wrap=el('div'),input=el('input');input.value=choices?.[0]?.id||'';wrap.append(input);const f={wrap,input};fields.set(label,f);return f;};
 function action(title,items,submit){const box=nodes.get('action-fields');box.replaceChildren(...items);const connect=n=>{n.isConnected=true;for(const c of n.children)connect(c);};connect(box);nodes.get('action-dialog').open=true;actions.push({title,items,submit});}
 const window={};const document={getElementById:id=>nodes.get(id)};
 vm.runInNewContext(fs.readFileSync('narrative/static/experience.js','utf8'),{window,document});
 const exp=window.createUmbralExperience({state:()=>state,api,el,button,action,actionField,refresh(){},bootstrap(){},navigate(){},notify(){}});
 const flush=()=>new Promise(resolve=>setImmediate(resolve));
 return {exp,nodes,fields,requests,actions,state,flush,get result(){return nodes.get('action-fields').children.at(-1);}};
}
const secret={results:[{id:'1',origin:'source',title:'Secreto',excerpt:'El rey es un dragón',status:'original'}]};
test('late narrator results cannot appear after returning to protagonist perspective',async()=>{
 const t=boot();t.exp.lookup('placa');const audience=t.fields.get('Consultar como').input;
 audience.value='narrator';audience.events.change();audience.value='character';audience.events.change();
 t.requests[1].resolve(secret);await t.flush();assert.doesNotMatch(t.result.textContent,/dragón/);
 t.requests[2].resolve({results:[]});await t.flush();assert.match(t.result.textContent,/Sin coincidencias/);
 t.requests[0].resolve(secret);await t.flush();assert.doesNotMatch(t.result.textContent,/dragón/);
});
test('changing words immediately clears old results and rejects late responses',async()=>{
 const t=boot();t.exp.lookup('placa');t.requests[0].resolve(secret);await t.flush();assert.match(t.result.textContent,/dragón/);
 const pending=t.actions.at(-1).submit();const input=t.fields.get('Nombre, objeto, símbolo o fragmento').input;input.value='árbol';input.events.input();assert.doesNotMatch(t.result.textContent,/dragón/);
 t.requests[1].resolve(secret);await pending;assert.doesNotMatch(t.result.textContent,/dragón/);
});
test('closed dialog and changed campaign ignore late search results',async()=>{
 const t=boot();t.exp.lookup('placa');t.nodes.get('action-dialog').open=false;t.requests[0].resolve(secret);await t.flush();assert.doesNotMatch(t.result.textContent,/dragón/);
 t.exp.lookup('placa');t.state.cid='another';t.requests[1].resolve(secret);await t.flush();assert.doesNotMatch(t.result.textContent,/dragón/);
});
test('late full entry cannot reopen the dialog after perspective changes',async()=>{
 const t=boot();t.exp.lookup('placa');t.requests[0].resolve(secret);await t.flush();const article=t.result.children[1];const read=article.children.find(n=>n.tag==='button');const pending=read.click();
 const audience=t.fields.get('Consultar como').input;audience.value='narrator';audience.events.change();
 t.requests[1].resolve({title:'Secreto completo',content:'Un secreto'});await pending;assert.equal(t.actions.length,1);
});
test('discarded scenes are labeled as archive rather than confirmed canon',async()=>{
 const t=boot();t.exp.lookup('placa');t.requests[0].resolve({results:[{id:'x',origin:'proposal',title:'Escena',excerpt:'Texto descartado',status:'rejected',canonical:false}]});await t.flush();assert.match(t.result.textContent,/Descartado/);
});
