const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.resolve(__dirname,'../../narrative/static/voice.js'),'utf8');

function boot({recognition=true,synthesis=true}={}){
  const nodes=new Map(),instances=[],spoken=[],events={},windowEvents={};let canceled=0;
  class Element{
    constructor(tag){this.tag=tag;this.children=[];this.events={};this.value='';this.readOnly=false;this.disabled=false;this.selectionStart=0;this.selectionEnd=0;}
    set id(v){this._id=v;nodes.set(v,this);} get id(){return this._id;}
    append(...items){this.children.push(...items);}before(n){this.beforeNode=n;}
    setAttribute(k,v){this[k]=v;}focus(){}
    addEventListener(name,fn){(this.events[name]??=[]).push(fn);}
    dispatchEvent(e){for(const fn of this.events[e.type]||[])fn(e);}
    click(){this.dispatchEvent({type:'click'});}
  }
  for(const id of ['turn-content','turn-form','universe-select','campaign-select','turn-role']){const n=new Element('div');n.id=id;}
  nodes.get('campaign-select').value='campaign-a';
  class Recognition{constructor(){instances.push(this);}start(){this.started=true;}stop(){this.onend?.();}abort(){this.aborted=true;this.onend?.();}}
  const document={createElement:tag=>new Element(tag),getElementById:id=>nodes.get(id),hidden:false,addEventListener:(name,fn)=>events[name]=fn};
  const window={addEventListener:(name,fn)=>windowEvents[name]=fn};
  if(recognition)window.SpeechRecognition=Recognition;
  if(synthesis){window.SpeechSynthesisUtterance=function(text){this.text=text;};window.speechSynthesis={cancel(){canceled++;},getVoices:()=>[{lang:'es-ES'}],speak:u=>spoken.push(u),pause(){this.paused=true;},resume(){this.paused=false;}};}
  vm.runInNewContext(source,{window,document,Event:class{constructor(type){this.type=type;}},Map});
  const box=nodes.get('turn-form').beforeNode;
  return {nodes,instances,spoken,window,document,events,box,button:text=>box.children.find(n=>n.textContent===text),status:()=>nodes.get('voice-status').textContent,canceled:()=>canceled};
}
const result=(text,final=true)=>Object.assign([{transcript:text}],{isFinal:final});

test('dictation preserves existing text, replaces selection and never submits',()=>{
  const ui=boot(),input=ui.nodes.get('turn-content');input.value='Antes viejo después';input.selectionStart=6;input.selectionEnd=11;
  let edits=0,submits=0;input.addEventListener('input',()=>edits++);ui.nodes.get('turn-form').addEventListener('submit',()=>submits++);
  ui.button('🎙 Dictar').click();const mic=ui.instances[0];assert.equal(input.readOnly,true);
  mic.onresult({results:[result('nuevo')]});
  mic.onresult({results:[result('nuevo'),result('texto')]});
  assert.equal(input.value,'Antes nuevo texto después');assert.equal(submits,0);assert.equal(edits,2);
  ui.button('Terminar dictado').click();assert.equal(input.readOnly,false);
});
test('interim speech is not committed and a late result cannot enter another campaign',()=>{
  const ui=boot(),input=ui.nodes.get('turn-content');input.value='Original';input.selectionStart=input.selectionEnd=8;
  ui.button('🎙 Dictar').click();const mic=ui.instances[0];mic.onresult({results:[result('provisional',false)]});assert.equal(input.value,'Original');
  ui.nodes.get('campaign-select').value='campaign-b';mic.onresult({results:[result('secreto')]});
  assert.equal(input.value,'Original');assert.equal(mic.aborted,true);assert.equal(input.readOnly,false);
  mic.onresult({results:[result('tardío')]});assert.equal(input.value,'Original');
});
test('permission failure preserves the draft and submission stops microphone',()=>{
  const ui=boot(),input=ui.nodes.get('turn-content');input.value='Mi decisión';ui.button('🎙 Dictar').click();const mic=ui.instances[0];
  mic.onerror({error:'not-allowed'});mic.onend();assert.match(ui.status(),/no está autorizado/);assert.equal(input.value,'Mi decisión');assert.equal(input.readOnly,false);
  ui.button('🎙 Dictar').click();let blocked=false;ui.nodes.get('turn-form').dispatchEvent({type:'submit',preventDefault(){blocked=true;},stopImmediatePropagation(){}});assert.equal(blocked,true);assert.equal(input.readOnly,false);
});
test('unsupported browsers show a keyboard fallback without microphone access',()=>{
  const ui=boot({recognition:false,synthesis:false});ui.button('🎙 Dictar').click();assert.match(ui.status(),/micrófono del teclado/);assert.equal(ui.instances.length,0);assert.equal(ui.window.UmbralVoice.canRead,false);
});
test('reading is explicit, chunks long narration, and stop prevents stale continuation',()=>{
  const ui=boot();assert.equal(ui.spoken.length,0);
  ui.window.UmbralVoice.read('Una narración larga. '.repeat(180));assert.equal(ui.spoken.length,1);assert.ok(ui.spoken[0].text.length<=900);assert.equal(ui.spoken[0].lang,'es-ES');
  ui.spoken[0].onend();assert.equal(ui.spoken.length,2);
  ui.button('Pausar / continuar').click();assert.equal(ui.window.speechSynthesis.paused,true);ui.button('Pausar / continuar').click();assert.equal(ui.window.speechSynthesis.paused,false);
  ui.button('Detener lectura').click();ui.spoken[1].onend();assert.equal(ui.spoken.length,2);
  ui.window.speechSynthesis.pause();ui.window.UmbralVoice.read('Nueva lectura después de pausar');assert.equal(ui.window.speechSynthesis.paused,false);
  ui.window.UmbralVoice.read('Otro texto');ui.document.hidden=true;ui.events.visibilitychange();const count=ui.spoken.length;ui.spoken.at(-1).onend();assert.equal(ui.spoken.length,count);
});
