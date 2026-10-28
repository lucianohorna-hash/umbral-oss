const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
function setup(){let queue=[],y=0;const events={};
 const list={children:[],scrollHeight:600,clientHeight:600,scrollTop:0,getBoundingClientRect:()=>({top:0}),addEventListener(){}};
 const form={getBoundingClientRect:()=>({top:1200-y}),scrollIntoView(){y=800;}};
 const jump={addEventListener(n,f){events[n]=f;}};
 const window={innerHeight:600,get scrollY(){return y;},scrollBy(x,d){y+=d;},scrollTo(x,d){y=d;},addEventListener(){}};
 const document={getElementById:id=>id==='turn-list'?list:form,createElement:()=>jump,body:{append(){}}};
 vm.runInNewContext(fs.readFileSync('narrative/static/story-scroll.js','utf8'),{window,document,requestAnimationFrame:f=>queue.push(f)});
 const turn=(id,role='user',top=0)=>({dataset:{turnId:id},classList:{contains:v=>v===role},getBoundingClientRect:()=>({top:top-y,bottom:top+200-y}),scrollIntoView(){y=top;}});
 return {window,list,jump,turn,flush(){queue.splice(0).forEach(f=>f());},get y(){return y;},set y(v){y=v;},events};}
test('opening and returning to a campaign goes to composer',()=>{const t=setup();t.list.children=[t.turn('1')];t.window.UmbralScroll.after('a',t.window.UmbralScroll.before());t.flush();assert.equal(t.y,800);t.y=0;t.window.UmbralScroll.show(false);t.window.UmbralScroll.show(true,true);assert.equal(t.y,800);});
test('reading older messages preserves position and announces new response',()=>{const t=setup();t.list.children=[t.turn('1')];t.window.UmbralScroll.after('a');t.flush();t.y=0;const saved=t.window.UmbralScroll.before();t.list.children=[t.turn('1'),t.turn('2','narrator',1000)];t.window.UmbralScroll.after('a',saved);t.flush();assert.equal(t.y,0);assert.equal(t.jump.hidden,false);assert.equal(t.jump.textContent,'↓ Nueva respuesta');t.events.click();assert.equal(t.y,800);});
test('new long response starts at its beginning when following conversation',()=>{const t=setup();t.list.children=[t.turn('1')];t.window.UmbralScroll.after('a');t.flush();const saved=t.window.UmbralScroll.before();t.list.children.push(t.turn('2','narrator',1000));t.window.UmbralScroll.after('a',saved);t.flush();assert.equal(t.y,1000);});
test('desktop nested reading position survives rerender and hidden views do not jump',()=>{const t=setup();t.list.scrollHeight=2000;t.list.clientHeight=650;t.list.children=[t.turn('1')];t.window.UmbralScroll.after('a');t.flush();t.list.scrollTop=150;const saved=t.window.UmbralScroll.before();t.window.UmbralScroll.after('a',saved);t.flush();assert.equal(t.list.scrollTop,150);t.window.UmbralScroll.show(false);t.y=0;t.window.UmbralScroll.after('b');t.flush();assert.equal(t.y,0);assert.equal(t.jump.hidden,true);});
