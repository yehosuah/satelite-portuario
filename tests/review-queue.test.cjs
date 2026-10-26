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
function harness({query = '', scope = null, queueId = 'train-01234567890123456789',
  detail = value => value, geometry = null, queueResponse = value => value, firstPassFailure = null} = {}) {
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
  const entries=['first','second'].map(id=>({observation_id:id,port_id:'pbr',sensor:'sentinel-2',zone_id:'pbr_canal',
    captured_at:'2025-01-01T00:00:00Z',observable_fraction:1,zone_geometry:square,
    ...(scope ? {search_scope:scope} : {}),
    assets:[{asset_id:id+'-raw',sha256:'a'.repeat(64),mime_type:'image/png',crs:'EPSG:4326',bounds:[0,0,1,1]}]}));
  const pending=new Map();
  const context={document:{getElementById:get,createElement:()=>new Element(),createElementNS:()=>new Element()},
    window:{addEventListener(){},location:{search:query}},ResizeObserver:class {observe(){}},AbortController,Date,JSON,Math,Number,Map,Error,encodeURIComponent,URLSearchParams,
    crypto:{randomUUID:()=>`c28713bc-e26b-4674-9116-546a1954d58${++assetNumber}`},
    URL:{createObjectURL:()=>`blob:${++assetNumber}`,revokeObjectURL(){}},
    Image:class {constructor(){this.naturalWidth=100;this.naturalHeight=100;} async decode(){}},
    fetch:async(path,options)=>{
      requests.push({path,options});
      const url=new URL(path,'http://fixture.local');
      if(url.pathname==='/v1/review-queue')return {ok:true,json:async()=>queueResponse({available:true,queue_id:queueId,split:'train',items:entries,distribution:{}})};
      if(url.pathname.includes('/observations/')) {
        const id=url.pathname.split('/').pop();
        return new Promise(resolve=>pending.set(id,()=>resolve({ok:true,json:async()=>detail({...entries.find(e=>e.observation_id===id),
          queue_id:queueId,
          assets:entries.find(e=>e.observation_id===id).assets.map(a=>({...a,
            href:`/v1/review-queue/assets/${id}/${a.asset_id}?queue_id=${queueId}`,
            metadata:{zone_pixel_geometry:square,...(scope ? {search_pixel_geometry:scope.geometry} : {})}}))})})));
      }
      if(path.includes('/assets/'))return {ok:true,blob:async()=>({})};
      if(path==='/v1/review-queue/geometry')return {ok:true,json:async()=>geometry || {
        geometry:square,pixel_geometry:square,...(scope ? {location:'search_margin'} : {})}};
      if(path==='/v1/review-queue/first-pass') {
        const body=JSON.parse(options.body);
        if(requests.filter(r=>r.path===path).length===1) {
          if(firstPassFailure)return {ok:false,status:firstPassFailure.status,json:async()=>({detail:firstPassFailure.detail})};
          throw new Error('Lost response');
        }
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
  assert.equal(images.length,1); assert.equal(images[0].path,`/v1/review-queue/assets/second/second-raw?queue_id=${app.queueId}`);
  assert.equal(app.get('continue-review').hidden,true);
  assert.equal(app.get('continue-review').href,undefined);
  assert.ok(app.requests.every(r=>r.path.startsWith('/v1/review-queue')));
});

test('UI retains the frozen queue and pending draft across reload, then retries identical bytes', async () => {
  const app=harness(); await app.tick(); app.pending.get('first')(); await app.tick();
  app.get('author').value='AI reviewer'; app.get('reviewer-kind').value='ai_visual_review'; app.get('token').value='fixture-secret';
  await app.get('auth-form').trigger('submit');
  assert.equal(app.get('token').value,'');
  app.get('note').value='Ambiguous scene'; await app.get('note').trigger('input');
  await app.get('first-pass-form').trigger('submit');
  assert.equal(app.get('note').disabled,true);
  assert.equal(app.get('continue-review').hidden,true);
  const reloading=app.get('reload').trigger('click'); await app.tick();
  app.pending.get('first')(); await reloading; await app.tick();
  assert.equal(app.get('note').value,'Ambiguous scene');
  assert.equal(app.get('note').disabled,true);
  assert.equal(app.get('save').textContent,'Reintentar el mismo envío');
  assert.equal(app.requests.filter(r=>new URL(r.path,'http://fixture.local').pathname==='/v1/review-queue')[1].path,
    `/v1/review-queue?queue_id=${app.queueId}`);
  await app.get('first-pass-form').trigger('submit');
  const submissions=app.requests.filter(r=>r.path.endsWith('/first-pass'));
  assert.equal(submissions.length,2); assert.equal(submissions[0].options.body,submissions[1].options.body);
  assert.equal(app.get('continue-review').href,'/?observation=first');
  assert.equal(app.get('continue-review').hidden,false);
  assert.equal(app.get('save').disabled,true);
  assert.ok(!submissions[0].options.body.includes('fixture-secret'));
});

test('a legacy queue retains its reference boundary without a search margin or badge', async () => {
  const app=harness(); await app.tick(); app.pending.get('first')(); await app.tick();
  assert.equal(app.requests[0].path,'/v1/review-queue');
  assert.equal(app.get('search-legend').hidden,true);
  assert.deepEqual(app.get('overlay').children.map(node=>node.attrs.class),['q-zone']);
  assert.match(app.get('search-context').textContent,/zona de referencia amarilla delimita el área de búsqueda/i);
  assert.match(app.get('scene-metadata').textContent,/Observabilidad en zona de referencia 100%/);
  app.get('author').value='Reviewer'; app.get('token').value='fixture-secret';
  await app.get('auth-form').trigger('submit');
  app.get('rectangle-kind').value='candidate';
  for(const [id,value] of [['rect-left',10],['rect-top',10],['rect-right',20],['rect-bottom',20]])app.get(id).value=String(value);
  await app.get('rectangle-form').trigger('submit'); await app.tick();
  assert.equal(app.get('candidate-list').children[0].children[0].children.length,0);
});

test('the selected queue ID binds the queue, observation, image and geometry requests', async () => {
  const queueId='train-abcdef0123456789abcd';
  const app=harness({query:`?queue=${queueId}`,queueId});
  await app.tick(); app.pending.get('first')(); await app.tick();
  assert.equal(app.requests[0].path,`/v1/review-queue?queue_id=${queueId}`);
  for(const request of app.requests.filter(r=>r.path.includes('/observations/') || r.path.includes('/assets/'))) {
    assert.equal(new URL(request.path,'http://fixture.local').searchParams.get('queue_id'),queueId);
  }
  app.get('author').value='Reviewer'; app.get('token').value='fixture-secret';
  await app.get('auth-form').trigger('submit');
  app.get('rectangle-kind').value='area';
  for(const [id,value] of [['rect-left',0],['rect-top',0],['rect-right',80],['rect-bottom',80]])app.get(id).value=String(value);
  await app.get('rectangle-form').trigger('submit'); await app.tick();
  assert.equal(JSON.parse(app.requests.find(r=>r.path.endsWith('/geometry')).options.body).queue_id,queueId);
  assert.equal(app.get('scope-status').textContent,'Área delimitada dentro del área de búsqueda');
});

test('an expanded search shows its blue contour and marks margin candidates without assigning activity', async () => {
  const scope={radius_m:500,frame_clipped:true,geometry:{type:'Polygon',coordinates:[[[-.1,-.1],[1.1,-.1],[1.1,1.1],[-.1,1.1],[-.1,-.1]]]}};
  const app=harness({scope}); await app.tick(); app.pending.get('first')(); await app.tick();
  assert.equal(app.get('search-legend').hidden,false);
  assert.equal(app.get('search-legend').textContent,'Azul: búsqueda 500 m');
  assert.deepEqual(app.get('overlay').children.map(node=>node.attrs.class),['q-search','q-zone']);
  assert.equal(app.get('overlay').children[0].attrs.d,helpers.polygonPath(scope.geometry));
  assert.match(app.get('search-context').textContent,/no mide el área ampliada/);
  assert.match(app.get('search-context').textContent,/limitada por el encuadre disponible/);
  app.get('author').value='Reviewer'; app.get('token').value='fixture-secret';
  await app.get('auth-form').trigger('submit');
  app.get('rectangle-kind').value='candidate';
  for(const [id,value] of [['rect-left',10],['rect-top',10],['rect-right',20],['rect-bottom',20]])app.get(id).value=String(value);
  await app.get('rectangle-form').trigger('submit'); await app.tick();
  const badge=app.get('candidate-list').children[0].children[0].children[0];
  assert.equal(badge.textContent,'Fuera de la zona de referencia');
  assert.equal(badge.className,'q-location-badge');
  const reloading=app.get('reload').trigger('click'); await app.tick();
  app.pending.get('first')(); await reloading; await app.tick();
  assert.equal(app.get('candidate-list').children[0].children[0].children[0].textContent,badge.textContent);
  assert.deepEqual(app.get('overlay').children.map(node=>node.attrs.class),['q-search','q-zone','q-candidate']);
});

test('a response for another requested queue never loads observations', async () => {
  const app=harness({query:'?queue=train-abcdef0123456789abcd'}); await app.tick();
  assert.equal(app.requests.length,1);
  assert.equal(app.pending.size,0);
  assert.match(app.get('feedback').textContent,/otra cola/);
});

test('invalid queue links do not silently select the active queue', async () => {
  for(const query of ['?queue=unknown','?queue=']) {
    const app=harness({query}); await app.tick();
    assert.equal(app.requests.length,0);
    assert.match(app.get('feedback').textContent,/cola TRAIN congelada válida/);
  }
});

test('source identity, scope and image links must agree before the image is fetched', async () => {
  const scope={radius_m:500,geometry:square};
  for(const detail of [
    value=>({...value,observation_id:'second'}),
    value=>({...value,queue_id:undefined}),
    value=>({...value,queue_id:'train-abcdef0123456789abcd'}),
    value=>({...value,port_id:'stc'}),
    value=>({...value,search_scope:undefined}),
    value=>({...value,search_scope:{...scope,radius_m:400}}),
    value=>({...value,assets:value.assets.map(asset=>({...asset,href:asset.href.replace('first/','second/')}))}),
    value=>({...value,assets:value.assets.map(asset=>({...asset,href:asset.href.replace('01234567890123456789','abcdef0123456789abcd')}))}),
    value=>({...value,assets:value.assets.map(asset=>({...asset,metadata:{zone_pixel_geometry:square}}))}),
  ]) {
    const app=harness({scope,detail}); await app.tick(); app.pending.get('first')(); await app.tick();
    assert.equal(app.requests.filter(r=>r.path.includes('/assets/')).length,0);
    assert.equal(app.get('load-status').textContent,'No disponible');
    assert.equal(app.get('image-wrap').hidden,true);
  }
});

test('a rejected search area allows explicit correction while retaining the draft and inspection ID', async () => {
  const app=harness({scope:{radius_m:500,geometry:square},firstPassFailure:{status:422,
    detail:'Inspected area must remain inside the configured search scope'}});
  await app.tick(); app.pending.get('first')(); await app.tick();
  app.get('author').value='Reviewer'; app.get('token').value='fixture-secret';
  await app.get('auth-form').trigger('submit');
  app.get('rectangle-kind').value='area';
  for(const [id,value] of [['rect-left',0],['rect-top',0],['rect-right',80],['rect-bottom',80]])app.get(id).value=String(value);
  await app.get('rectangle-form').trigger('submit'); await app.tick();
  app.get('note').value='Uncertain edge'; await app.get('note').trigger('input');
  await app.get('first-pass-form').trigger('submit');
  assert.equal(app.get('edit-rejected').hidden,false);
  assert.equal(app.get('note').disabled,true);
  await app.get('edit-rejected').trigger('click');
  assert.equal(app.get('note').disabled,false);
  assert.equal(app.get('note').value,'Uncertain edge');
  assert.deepEqual(app.get('overlay').children.map(node=>node.attrs.class),['q-search','q-zone','q-area']);
  app.get('note').value='Corrected uncertainty'; await app.get('note').trigger('input');
  await app.get('first-pass-form').trigger('submit');
  const submissions=app.requests.filter(request=>request.path.endsWith('/first-pass')).map(request=>JSON.parse(request.options.body));
  assert.equal(submissions[0].inspection_id,submissions[1].inspection_id);
  assert.deepEqual(submissions[0].reviewed_area,submissions[1].reviewed_area);
  assert.equal(submissions[1].note,'Corrected uncertainty');
  assert.equal(app.get('continue-review').hidden,false);
});
