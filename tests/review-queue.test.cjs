const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const helpers = require('../src/puertogt/web/review-queue.js');
const source = fs.readFileSync(require.resolve('../src/puertogt/web/review-queue.js'), 'utf8');
const square = {type: 'Polygon', coordinates: [[[0,0],[1,0],[1,1],[0,1],[0,0]]]};

test('normalized drawing is invariant under zoom and scrolling', () => {
  assert.deepEqual(helpers.normalizedPoint(110, 70, {left: 10, top: 20, width: 400, height: 200}), [.25,.25]);
  assert.deepEqual(helpers.normalizedPoint(-50, 0, {left: -250, top: -100, width: 800, height: 400}), [.25,.25]);
  assert.deepEqual(helpers.normalizedRectangle([.8,.9], [.2,.1]), [.2,.1,.8,.9]);
  assert.throws(() => helpers.normalizedRectangle([0,0],[1,Infinity]));
  assert.throws(() => helpers.normalizedRectangle([.5,.1],[.5,.8]));
});

test('polygon paths retain holes and disjoint clipped areas', () => {
  const hole = [[.2,.2],[.3,.2],[.3,.3],[.2,.2]];
  const path = helpers.polygonPath({type:'MultiPolygon',coordinates:[[square.coordinates[0],hole],square.coordinates]});
  assert.equal((path.match(/M/g) || []).length, 3);
  assert.equal((path.match(/Z/g) || []).length, 3);
  assert.throws(() => helpers.polygonPath({type: 'Point', coordinates:[0,0]}));
  assert.throws(() => helpers.polygonPath({type: 'Polygon', coordinates: [[[NaN,0],[0,0],[0,1],[NaN,0]]]}));
});

test('ambiguous save retry keeps UUID, elapsed time, author and exact request bytes', () => {
  const draft = helpers.newInspection('c28713bc-e26b-4674-9116-546a1954d58d');
  draft.startedAt = 1000; draft.decision = 'empty'; draft.area = {geometry:square};
  const context = {queueId:'train-01234567890123456789',observationId:'safe-train',author:'Reviewer',reviewerKind:'human_visual_review'};
  const original = helpers.freezeSubmission(draft, context, 5100);
  draft.note = 'Changed after POST'; draft.candidates.push({geometry:square});
  assert.equal(helpers.freezeSubmission(draft, {...context,author:'Another reviewer'},999999),original);
  const body = JSON.parse(original);
  assert.equal(body.elapsed_seconds,4);
  assert.equal(body.note,''); assert.equal(body.candidates.length,0);
  assert.equal(body.author,'Reviewer');
  assert.equal(body.inspection_id,'c28713bc-e26b-4674-9116-546a1954d58d');
});

test('an unrelated or unverified response never unlocks the model viewer', () => {
  const body = JSON.stringify({queue_id:'train-one',observation_id:'train-observation',inspection_id:'inspection-one'});
  const receipt = {...JSON.parse(body),sha256:'a'.repeat(64),is_training_label:false,is_independent_validation:false};
  assert.equal(helpers.matchesReceipt(receipt,body),true);
  for (const change of [{observation_id:'other'}, {queue_id:'train-two'}, {inspection_id:'other'},
    {is_training_label:true}, {is_independent_validation:true}, {sha256:'invalid'}]) {
    assert.equal(helpers.matchesReceipt({...receipt,...change},body),false);
  }
});

// A small DOM harness exercises async routing and the retry user flow without
// requiring a browser package or reading any real imagery/reviews.
function harness() {
  class Element {
    constructor(id) { this.id=id; this.listeners={}; this.attrs={}; this.children=[]; this.value=''; this.hidden=false; this.disabled=false;
      this.dataset={}; this.style={}; this.classList={toggle(){}}; this.clientWidth=800; this.clientHeight=400; }
    addEventListener(type,fn) { this.listeners[type]=fn; }
    async trigger(type) { return this.listeners[type]?.({preventDefault(){},target:this}); }
    setAttribute(key,value) { this.attrs[key]=value; }
    removeAttribute(key) { delete this.attrs[key]; if(key==='href') delete this.href; }
    replaceChildren(...nodes) { this.children=nodes; }
    append(...nodes) { this.children.push(...nodes); }
    querySelectorAll() { return []; }
    scrollTo() {} focus() {}
  }
  const elements = new Map();
  const get = id => { if(!elements.has(id)) elements.set(id,new Element(id)); return elements.get(id); };
  const requests=[];
  let assetNumber=0;
  const queueId='train-01234567890123456789';
  const entries=['first','second'].map(id=>({observation_id:id,port_id:'pbr',sensor:'sentinel-2',zone_id:'pbr_canal',
    captured_at:'2025-01-01T00:00:00Z',observable_fraction:1,zone_geometry:square,
    assets:[{asset_id:id+'-raw',sha256:'a'.repeat(64),mime_type:'image/png',crs:'EPSG:4326',bounds:[0,0,1,1]}]}));
  const pending=new Map();
  const context={document:{getElementById:get,createElement:()=>new Element(),createElementNS:()=>new Element()},
    window:{addEventListener(){}},ResizeObserver:class {observe(){}},AbortController,Date,JSON,Math,Number,Map,Error,encodeURIComponent,
    crypto:{randomUUID:()=>`c28713bc-e26b-4674-9116-546a1954d58${++assetNumber}`},
    URL:{createObjectURL:()=>`blob:${++assetNumber}`,revokeObjectURL(){}},
    Image:class {constructor(){this.naturalWidth=100;this.naturalHeight=100;} async decode(){}},
    fetch:async(path,options)=>{
      requests.push({path,options});
      if(path==='/v1/review-queue')return {ok:true,json:async()=>({available:true,queue_id:queueId,split:'train',items:entries,distribution:{}})};
      if(path.includes('/observations/')) {
        const id=path.split('/').pop();
        return new Promise(resolve=>pending.set(id,()=>resolve({ok:true,json:async()=>({...entries.find(e=>e.observation_id===id),
          assets:entries.find(e=>e.observation_id===id).assets.map(a=>({...a,metadata:{zone_pixel_geometry:square}}))})})));
      }
      if(path.includes('/assets/'))return {ok:true,blob:async()=>({})};
      if(path==='/v1/review-queue/first-pass') {
        const body=JSON.parse(options.body);
        if(requests.filter(r=>r.path===path).length===1)throw new Error('Lost response');
        return {ok:true,json:async()=>({...body,recorded_at:'now',sha256:'b'.repeat(64),is_training_label:false,is_independent_validation:false})};
      }
      throw new Error('Unexpected request: '+path);
    }};
  vm.createContext(context); vm.runInContext(source,context);
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  return {get,requests,pending,queueId,tick};
}

test('a stale scene response cannot reveal another image or overwrite current scene', async () => {
  const app=harness(); await app.tick();
  assert.ok(app.pending.has('first'));
  app.get('scene-list').children[1].trigger('click'); await app.tick();
  assert.ok(app.pending.has('second'));
  app.pending.get('second')(); await app.tick();
  app.pending.get('first')(); await app.tick();
  const images=app.requests.filter(r=>r.path.includes('/assets/'));
  assert.equal(images.length,1); assert.match(images[0].path,/second\/second-raw$/);
  assert.equal(app.get('continue-review').hidden,true);
  assert.equal(app.get('continue-review').href,undefined);
  assert.ok(app.requests.every(r=>r.path.startsWith('/v1/review-queue')));
});

test('UI retries an interrupted first pass identically and unlocks only its receipt', async () => {
  const app=harness(); await app.tick(); app.pending.get('first')(); await app.tick();
  app.get('author').value='AI reviewer'; app.get('reviewer-kind').value='ai_visual_review'; app.get('token').value='fixture-secret';
  await app.get('auth-form').trigger('submit');
  assert.equal(app.get('token').value,'');
  app.get('note').value='Ambiguous scene'; await app.get('note').trigger('input');
  await app.get('first-pass-form').trigger('submit');
  assert.equal(app.get('note').disabled,true);
  assert.equal(app.get('continue-review').hidden,true);
  await app.get('first-pass-form').trigger('submit');
  const submissions=app.requests.filter(r=>r.path.endsWith('/first-pass'));
  assert.equal(submissions.length,2); assert.equal(submissions[0].options.body,submissions[1].options.body);
  assert.equal(app.get('continue-review').href,'/?observation=first');
  assert.equal(app.get('continue-review').hidden,false);
  assert.equal(app.get('save').disabled,true);
  assert.ok(!submissions[0].options.body.includes('fixture-secret'));
});
