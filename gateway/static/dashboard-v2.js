
(() => {
  const qs=(s,r=document)=>r.querySelector(s);
  const qsa=(s,r=document)=>[...r.querySelectorAll(s)];
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const token=new URLSearchParams(location.search).get('token')||localStorage.getItem('hermus_gateway_token')||'';
  if(token)localStorage.setItem('hermus_gateway_token',token);

  const state={
    view:'overview', online:false, modelCatalog:[], selectedModels:{}, agents:[], projects:[], project:null, file:null,
    settings:JSON.parse(localStorage.getItem('hermus_dashboard_settings')||'{}'),
    messages:[{who:'jarvis',text:'JARVIS is ready. Tell HERMUS what you want to accomplish.',time:'Now'}],
    activeRoom:'main-hall', voice:null
  };

  const views={
    overview:['Overview','Your operational center. See what matters, what is running, and where to act.'],
    chat:['Chat','Talk to HERMUS directly and keep the conversation close to the rest of the system.'],
    build:['Workshop','Build, inspect and edit workspace files with HERMUS context beside you.'],
    agents:['Agents','Manage the persistent agent fleet and delegate work without leaving the environment.'],
    models:['Models','Route roles to runtime-discovered deployments and see what is actually available.'],
    tools:['Tools','Browse capabilities as usable tools, not raw registries.'],
    missions:['Missions','Run work, manage routines and recover interrupted missions.'],
    knowledge:['Knowledge','Review learning records and save durable project knowledge.'],
    personal:['Personal Space','A bounded private space where HERMUS can explore useful ideas and ask for approval.'],
    browser:['Browser','Bring live web context into HERMUS through the browser layer.'],
    terminal:['Terminal','Send a terminal task through HERMUS and follow the run.'],
    image:['Image Lab','Turn an idea into a visual request and follow execution from the work rail.'],
    video:['Video Studio','Plan a shot or sequence and send it into the media pipeline.'],
    space:['3D Space','Move through HERMUS environments and choose the workspace you want to inhabit.'],
    settings:['Settings','Control local dashboard behavior and inspect core runtime health.']
  };

  async function api(path,options={}){
    const headers=new Headers(options.headers||{});
    headers.set('Accept','application/json');
    if(token)headers.set('X-Hermus-Token',token);
    if(options.body&&!headers.has('Content-Type'))headers.set('Content-Type','application/json');
    const r=await fetch(path,{...options,headers});
    const text=await r.text(); let data={};
    try{data=text?JSON.parse(text):{}}catch{data={message:text}};
    if(!r.ok)throw new Error(String(data.message||data.detail||data.error||('HTTP '+r.status)));
    return data;
  }
  const safe=async(path,options)=>{try{return await api(path,options)}catch{return null}};
  function toast(message,bad=false){
    let n=qs('#toast');
    if(!n){n=document.createElement('div');n.id='toast';n.className='toast';document.body.appendChild(n)}
    n.textContent=message;n.classList.toggle('bad',bad);n.classList.add('show');
    clearTimeout(n._t);n._t=setTimeout(()=>n.classList.remove('show'),1900);
  }
  function setOnline(ok){
    state.online=ok;
    const dot=qs('#topStatusDot'), label=qs('#topStatusLabel'), sub=qs('#topStatusSub');
    dot?.classList.toggle('off',!ok); if(label)label.textContent=ok?'ONLINE':'OFFLINE'; if(sub)sub.textContent=ok?'HERMUS CORE':'GATEWAY UNAVAILABLE';
  }
  function viewHead(key){
    const meta=views[key]||views.overview;
    return '<div class="view-head"><div><div class="eyebrow">'+esc(key==='overview'?'HERMUS NEXUS':'WORKSPACE')+'</div><h1>'+esc(meta[0])+'</h1><p>'+esc(meta[1])+'</p></div><div class="actions">'+(key!=='overview'?'<button class="btn ghost" data-view="overview">OVERVIEW</button>':'')+'</div></div>';
  }
  function stat(label,value,detail){return '<article class="stat"><span>'+esc(label)+'</span><b>'+esc(value)+'</b><small>'+esc(detail)+'</small></article>'}
  function badge(text,kind=''){return '<span class="badge '+esc(kind)+'">'+esc(String(text).toUpperCase())+'</span>'}

  function openView(key,opts={}){
    if(!views[key])key='overview';
    state.view=key;
    history.replaceState(null,'','#'+key);
    qsa('.view').forEach(v=>v.classList.toggle('active',v.id==='view-'+key));
    qsa('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===key));
    const title=qs('#workspaceTitle'), desc=qs('#workspaceDescription');
    if(title)title.textContent=views[key][0];
    if(desc)desc.textContent=views[key][1];
    if(opts.render!==false)loadView(key);
    window.scrollTo({top:0,behavior:'auto'});
  }

  async function sendCommand(command){
    const text=String(command||'').trim(); if(!text)return;
    state.messages.push({who:'user',text,time:'Now'});renderChat();
    toast('Request sent to HERMUS');
    try{
      const session=localStorage.getItem('hermus_session_id')||'';
      const result=await api('/api/v1/commands',{method:'POST',body:JSON.stringify({
        command,text,platform:'web',mode:'chat',stream:true,session_id:session,user_id:'default'
      })});
      if(result.session_id)localStorage.setItem('hermus_session_id',String(result.session_id));
      const run=result.run_id||result.mission_id;
      state.messages.push({who:'jarvis',text:run?'Accepted · live execution started.':'Accepted · HERMUS is processing the request.',time:'Now'});
      renderChat(); refreshOverview();
      return run;
    }catch(e){
      state.messages.push({who:'jarvis',text:'I could not send that request · '+e.message,time:'Now'});
      renderChat(); toast(e.message,true);
    }
  }

  function renderChat(){
    const host=qs('#chatMessages');if(!host)return;
    host.innerHTML=state.messages.slice(-30).map(m=>'<div class="msg '+(m.who==='user'?'user':'')+'"><span class="msg-meta">'+(m.who==='user'?'YOU':'JARVIS')+' · '+esc(m.time)+'</span>'+esc(m.text)+'</div>').join('');
    host.scrollTop=host.scrollHeight;
  }

  async function refreshOverview(){
    const [health,presence,agents,catalog,selected]=await Promise.all([
      safe('/api/v1/system/health'),safe('/presence/kernel?user_id=default&include_events=true'),
      safe('/api/v1/agents/list'),safe('/models/catalog'),safe('/models/selected')
    ]);
    setOnline(!!health);
    const ok=health?Object.values(health).filter(v=>v&&(v.ok===true||v.running===true||v.installed===true)).length:0;
    qs('#overviewHealth').textContent=health?(ok?'HEALTHY':'DEGRADED'):'OFFLINE';
    qs('#overviewHealth')?.classList.toggle('warn',!!health&&!ok);
    const a=Array.isArray(agents?.agents)?agents.agents:[]; state.agents=a;
    qs('#statAgents').textContent=String(a.length);
    qs('#statTasks').textContent=String(Number(presence?.summary?.active_runs||0));
    state.modelCatalog=Array.isArray(catalog?.models)?catalog.models:[];
    state.selectedModels=selected?.selections||{};
    qs('#statModels').textContent=String(state.modelCatalog.length);
    qs('#statAttention').textContent=String(Number(presence?.summary?.attention_count||0));
    const attention=Array.isArray(presence?.attention)?presence.attention:[];
    const ah=qs('#overviewAttention');
    if(ah)ah.innerHTML=attention.length?attention.slice(0,4).map(i=>'<div class="row"><div class="row-main"><strong>'+esc(i.title||'Attention')+'</strong><small>'+esc(i.detail||'Needs review')+'</small></div>'+badge(i.severity||'normal',String(i.severity||'').toLowerCase()==='critical'?'bad':'warn')+'</div>').join(''):'<div class="empty">All systems are calm. Nothing needs your attention.</div>';
    const active=qs('#overviewAgents');
    if(active)active.innerHTML=a.slice(0,5).map(x=>'<div class="row"><div class="row-main"><strong>'+esc(x.name||x.role||'Agent')+'</strong><small>'+esc(String(x.role||'general').replace(/_/g,' '))+' · '+esc(String(x.state||'idle').replace(/_/g,' '))+'</small></div>'+badge(x.state||'idle',['running','working','active'].includes(String(x.state||'').toLowerCase())?'good':'')+'</div>').join('')||'<div class="empty">No persistent agents are currently active.</div>';
    const runs=Array.isArray(presence?.events?.recent)?presence.events.recent:[];
    const rh=qs('#overviewActivity');
    if(rh)rh.innerHTML=runs.slice(-6).reverse().map(e=>'<div class="row"><div class="row-main"><strong>'+esc(e.summary||e.type||'Event')+'</strong><small>'+esc(e.at||'recent')+'</small></div>'+badge(String(e.type||'event').replace(/_/g,' '))+'</div>').join('')||'<div class="empty">No recent execution events.</div>';
  }

  async function loadAgents(){
    const data=await safe('/agents'); const list=Array.isArray(data?.agents)?data.agents:(Array.isArray(state.agents)?state.agents:[]);
    state.agents=list;const host=qs('#agentsHost');if(!host)return;
    host.innerHTML=(list.length?'<div class="agent-grid">'+list.map(a=>{
      const name=String(a.name||a.id||a.role||'Agent'), status=String(a.status||a.state||'idle').toLowerCase();
      const running=['running','active','working'].includes(status);
      const payload=encodeURIComponent(name);
      return '<article class="agent-card"><div class="agent-top"><div><div class="agent-name">'+esc(name)+'</div><div class="meta">'+esc(String(a.role||'general').replace(/_/g,' '))+(a.model?' · '+esc(a.model):'')+'</div></div>'+badge(status,running?'good':status==='error'?'bad':'')+'</div><div class="actions"><button class="btn '+(running?'':'primary')+'" data-agent-action="'+(running?'stop':'start')+'" data-agent="'+payload+'">'+(running?'STOP':'START')+'</button><button class="btn" data-agent-action="chat" data-agent="'+payload+'">CHAT</button></div></article>';
    }).join('')+'</div>':'<div class="card"><div class="card-body"><div class="empty">No persistent agents are registered yet.</div></div></div>');
  }

  async function loadModels(){
    const [cat,sel]=await Promise.all([safe('/models/catalog?probe=false&refresh=false'),safe('/models/selected')]);
    state.modelCatalog=Array.isArray(cat?.models)?cat.models:[]; state.selectedModels=sel?.selections||{};
    const role=qs('#modelRoleInput'), deployment=qs('#modelDeploymentInput');
    if(role&&deployment){
      const current=state.selectedModels[role.value]||'auto';
      deployment.innerHTML='<option value="auto">AUTO · best available</option>'+state.modelCatalog.map(m=>'<option value="'+esc(m.ref)+'">'+esc(m.name||m.id||m.ref)+'</option>').join('');
      if([...deployment.options].some(o=>o.value===current))deployment.value=current;
    }
    const host=qs('#modelsHost');if(!host)return;
    host.innerHTML='<div class="model-grid">'+(state.modelCatalog.length?state.modelCatalog.map(m=>{
      const live=m.source==='live', reach=m.reachable!==false;
      return '<article class="model-card"><div class="model-top"><div><div class="model-name">'+esc(m.name||m.id||m.ref)+'</div><div class="meta">'+esc(m.provider||'provider')+' · '+esc(m.ref||m.id||'model')+'</div></div>'+badge(reach?(live?'LIVE':'CACHED'):'OFFLINE',reach?(live?'good':'bad'))+'</div><div class="meta" style="margin-top:10px">Tools: '+esc(m.capabilities?.tools||'unknown')+' · Vision: '+esc(m.capabilities?.vision||'unknown')+'</div></article>';
    }).join(''):'<div class="card"><div class="card-body"><div class="empty">No deployments discovered. Start a model runtime and refresh.</div></div></div>')+'</div>';
  }

  async function saveModel(){
    const role=qs('#modelRoleInput')?.value||'default', model=qs('#modelDeploymentInput')?.value||'auto';
    try{await api('/models/select',{method:'POST',body:JSON.stringify({role,model})});state.selectedModels[role]=model;toast('Model routing saved');loadModels()}catch(e){toast(e.message,true)}
  }

  async function loadTools(){
    const data=await safe('/api/v1/system/capabilities');const providers=Array.isArray(data?.providers)?data.providers:[];const tools=data?.tools&&typeof data.tools==='object'?Object.entries(data.tools):[];
    const host=qs('#toolsHost');if(!host)return;
    const providerHtml=providers.map(p=>'<article class="tool-card"><div class="tool-top"><div><div class="tool-name">'+esc(p.name||p.id||p.provider||'Provider')+'</div><div class="meta">'+esc(p.base_url||p.detail||'Connected runtime')+'</div></div>'+badge((p.ok??p.reachable??p.healthy??true)?'READY':'DEGRADED',(p.ok??p.reachable??p.healthy??true)?'good':'warn')+'</div></article>').join('');
    const toolHtml=tools.slice(0,80).map(([name,d])=>'<button class="tool-card" type="button" data-tool="'+esc(name)+'"><div class="tool-top"><div><div class="tool-name">'+esc(name.replace(/_/g,' '))+'</div><div class="meta">'+esc(d?.description||d?.summary||'HERMUS capability')+'</div></div>'+badge((d?.enabled??d?.available??true)?'READY':'UNAVAILABLE',(d?.enabled??d?.available??true)?'good':'warn')+'</div></button>').join('');
    host.innerHTML='<div class="grid cols-2"><div class="card"><div class="card-head"><div><strong>Providers</strong><small>Where HERMUS can route execution</small></div></div><div class="card-body list">'+(providerHtml||'<div class="empty">No provider state reported.</div>')+'</div></div><div class="card"><div class="card-head"><div><strong>Quick tools</strong><small>Open a real workspace for common capabilities</small></div></div><div class="card-body actions"><button class="btn primary" data-view="browser">BROWSER</button><button class="btn" data-view="terminal">TERMINAL</button><button class="btn" data-view="image">IMAGE LAB</button><button class="btn" data-view="video">VIDEO STUDIO</button></div></div></div><div class="card" style="margin-top:12px"><div class="card-head"><div><strong>Registered tools</strong><small>Click any tool to inspect it</small></div><span class="badge good">'+tools.length+' AVAILABLE</span></div><div class="card-body"><div class="tool-grid">'+(toolHtml||'<div class="empty">No registered tools reported.</div>')+'</div><div id="toolInspector" class="result">Select a tool to inspect its status and description.</div></div></div>';
  }

  async function loadMissions(){
    const [m,r]=await Promise.all([safe('/missions'),safe('/routines')]);const missions=Array.isArray(m?.missions)?m.missions:[], routines=Array.isArray(r?.routines)?r.routines:[];
    const host=qs('#missionsHost');if(!host)return;
    host.innerHTML='<div class="grid cols-2"><div class="card"><div class="card-head"><div><strong>MISSIONS</strong><small>Recoverable execution history</small></div></div><div class="card-body list">'+(missions.length?missions.map(x=>{const st=String(x.state||x.status||'unknown').toLowerCase();const recover=['failed','blocked','interrupted'].includes(st);return '<div class="row"><div class="row-main"><strong>'+esc(x.goal||x.title||x.id||'Mission')+'</strong><small>'+esc(st)+(x.id?' · '+esc(x.id):'')+'</small></div>'+ (recover?'<button class="btn primary" data-mission-resume="'+esc(x.id)+'">RESUME</button>':badge(st))+'</div>'}).join(''):'<div class="empty">No missions yet.</div>')+'</div></div>'+
      '<div class="card"><div class="card-head"><div><strong>ROUTINES</strong><small>Proactive automation</small></div><button class="btn primary" data-routine-new>NEW</button></div><div class="card-body list">'+(routines.length?routines.map(x=>{const enabled=!!x.enabled,id=x.id||x.name;return '<div class="row"><div class="row-main"><strong>'+esc(x.name||id)+'</strong><small>'+esc((x.event_type||'schedule')+' → '+(x.action_type||'runtime.turn'))+'</small></div><div class="row-actions"><button class="btn" data-routine-toggle data-id="'+esc(id)+'" data-enabled="'+(!enabled)+'">'+(enabled?'DISABLE':'ENABLE')+'</button><button class="btn danger" data-routine-delete data-id="'+esc(id)+'">DELETE</button></div></div>'}).join(''):'<div class="empty">No proactive routines configured.</div>')+'</div></div></div><div id="routineFormHost"></div>';
  }

  function routineForm(){
    const host=qs('#routineFormHost');if(!host)return;
    host.innerHTML='<div class="card" style="margin-top:12px"><div class="card-head"><div><strong>CREATE ROUTINE</strong><small>Keep the workflow bounded and auditable</small></div></div><div class="card-body"><div class="form-grid"><label class="field">NAME<input id="routineName" placeholder="Daily review"></label><label class="field">TRIGGER<input id="routineEvent" placeholder="schedule"></label><label class="field">ACTION<select id="routineAction"><option value="runtime.turn">Runtime turn</option><option value="agent.autonomous">Autonomous agent</option><option value="mission.start">Start mission</option></select></label><label class="field">COOLDOWN<input id="routineCooldown" type="number" min="0" value="60"></label><label class="field full">TASK<textarea id="routineTask" placeholder="Review my current project and surface anything important."></textarea></label></div><div class="actions" style="margin-top:10px"><button class="btn primary" data-routine-save>CREATE</button><button class="btn" data-routine-validate>VALIDATE</button></div><div id="routineResult" class="result">Not submitted.</div></div></div>';
  }

  async function submitRoutine(validateOnly){
    const payload={name:qs('#routineName')?.value||'routine',event_type:qs('#routineEvent')?.value||'',action_type:qs('#routineAction')?.value||'runtime.turn',task:qs('#routineTask')?.value||'',cooldown_seconds:Number(qs('#routineCooldown')?.value||60),enabled:false};
    try{
      const out=await api(validateOnly?'/routines/validate':'/routines',{method:'POST',body:JSON.stringify(payload)});
      const box=qs('#routineResult');if(box)box.textContent=validateOnly?(out.valid?'VALID · ready to create':'NOT VALID · '+((out.errors||[]).join(' · ')||'review the fields')):'Routine created successfully.';
      if(!validateOnly)loadMissions();
    }catch(e){const box=qs('#routineResult');if(box)box.textContent='Error · '+e.message}
  }

  async function loadKnowledge(){
    const data=await safe('/learning?limit=30');const rows=[];for(const k of ['lessons','skills','episodes','items'])if(Array.isArray(data?.[k]))rows.push(...data[k].map(x=>({kind:k,...x})));
    const host=qs('#knowledgeHost');if(!host)return;
    host.innerHTML='<div class="card"><div class="card-head"><div><strong>SAVE KNOWLEDGE</strong><small>Make useful context durable</small></div></div><div class="card-body"><div class="compose"><textarea id="knowledgeNote" placeholder="Write a project note, decision, discovery or reusable lesson…"></textarea><button class="btn primary" data-knowledge-save>SAVE</button></div><div id="knowledgeResult" class="result">No note saved.</div></div></div><div class="card" style="margin-top:12px"><div class="card-head"><div><strong>LEARNING FABRIC</strong><small>What HERMUS has retained</small></div><span class="badge">'+rows.length+' RECORDS</span></div><div class="card-body list">'+(rows.map(x=>'<div class="row"><div class="row-main"><strong>'+esc(x.title||x.name||x.content||'Learning record')+'</strong><small>'+esc(x.kind)+(x.summary?' · '+esc(x.summary):'')+'</small></div>'+badge('SAVED','good')+'</div>').join('')||'<div class="empty">No learning records yet.</div>')+'</div></div>';
  }

  async function loadPersonal(){
    const data=await safe('/personal-space?limit=8');const proposal=Array.isArray(data?.proposals)?data.proposals.find(x=>x.status==='pending')||data.proposals[0]:null;
    const host=qs('#personalHost');if(!host)return;
    host.innerHTML='<div class="grid cols-2"><div class="card"><div class="card-head"><div><strong>HERMUS PRIVATE SPACE</strong><small>Bounded curiosity · approval required</small></div>'+badge(data?.status||'READY',(data?.status||'').includes('proposal')?'warn':'good')+'</div><div class="card-body"><div class="stat-strip" style="margin:0 0 12px">'+stat('IDLE',''+Math.round(Number(data?.idle_for_seconds||0)/60)+'m','time available')+stat('TODAY',String(data?.daily_cycles??0),'/ '+String(data?.daily_cap??3)+' cycles')+'</div><div class="actions"><button class="btn primary" data-personal-run>THINK NOW</button><button class="btn" data-personal-refresh>REFRESH</button></div></div></div>'+
      '<div class="card"><div class="card-head"><div><strong>PROPOSAL</strong><small>Nothing becomes an action without you</small></div></div><div class="card-body">'+(proposal?'<h3 style="margin:0 0 5px;font-size:12px">'+esc(proposal.title||'Discovery')+'</h3><p class="muted" style="font-size:9px;line-height:1.5">'+esc(proposal.summary||proposal.why||'A useful discovery is waiting.')+'</p><div class="actions" style="margin-top:12px"><button class="btn primary" data-proposal="approve" data-proposal-id="'+esc(proposal.id)+'">APPROVE</button><button class="btn danger" data-proposal="dismiss" data-proposal-id="'+esc(proposal.id)+'">DISMISS</button></div>':'<div class="empty">No proposal waiting for approval.</div>')+'</div></div></div>';
  }

  async function loadBrowser(){
    const host=qs('#browserHost');if(!host)return;
    if(!host.dataset.ready){
      host.innerHTML='<div class="grid cols-2"><div class="card"><div class="card-head"><div><strong>VISUAL WEB CONTEXT</strong><small>Fetch a public page through HERMUS</small></div></div><div class="card-body"><div class="toolbar"><label class="field grow">URL<input id="browserUrl" placeholder="https://example.com" aria-label="Browser URL"></label><button class="btn primary" data-browser-open>OPEN PAGE</button></div><div id="browserResult" class="result">No page loaded.</div></div></div><div class="card"><div class="card-head"><div><strong>WORKFLOW</strong><small>Bring the result into your next HERMUS task</small></div></div><div class="card-body"><div class="empty">Use the command rail after a page is loaded to analyze, summarize or save useful findings.</div></div></div></div>';host.dataset.ready='1';
    }
  }
  async function browserOpen(){
    const url=(qs('#browserUrl')?.value||'').trim();if(!url)return;
    const box=qs('#browserResult');box.textContent='Loading page…';
    try{const d=await api('/navigator/fetch',{method:'POST',body:JSON.stringify({url})});box.textContent=(d.title||d.url||'Page loaded')+' · '+String(d.content_length??0)+' characters fetched.';toast('Browser context loaded')}catch(e){box.textContent='Browser error · '+e.message;toast(e.message,true)}
  }

  function toolWorkspace(type){
    const map={terminal:['TERMINAL TASK','Send a safe terminal task through HERMUS','terminalInput','Run a command…','RUN'],image:['IMAGE REQUEST','Describe the visual you want','imageInput','Describe the image…','GENERATE'],video:['VIDEO REQUEST','Describe the shot or sequence','videoInput','Describe the shot…','GENERATE']};
    const m=map[type];const host=qs('#'+type+'Host');if(!host||!m)return;
    host.innerHTML='<div class="card"><div class="card-head"><div><strong>'+m[0]+'</strong><small>'+m[1]+'</small></div></div><div class="card-body"><div class="toolbar"><label class="field grow">'+(type==='terminal'?'TASK':'PROMPT')+'<input id="'+m[2]+'" placeholder="'+m[3]+'"></label><button class="btn primary" data-tool-run="'+type+'">'+m[4]+'</button></div><div id="'+type+'Result" class="result">'+(type==='terminal'?'Ready.':'Nothing generated yet.')+'</div></div></div>';
  }
  async function runToolWorkspace(type){
    const id={terminal:'terminalInput',image:'imageInput',video:'videoInput'}[type],value=(qs('#'+id)?.value||'').trim();if(!value)return;
    const box=qs('#'+type+'Result');box.textContent='Request sent…';
    const command=type==='terminal'?value:(type==='image'?'Create an image: ':'Create a video: ')+value;
    await sendCommand(command);box.textContent='Request accepted · follow the run in Chat or Overview.';
  }

  async function loadBuild(){
    const host=qs('#buildHost');if(!host)return;
    host.innerHTML='<div class="workspace-shell"><div class="workspace-top"><div><div class="eyebrow">WORKSHOP</div><h3 id="buildProjectTitle">Workspace</h3></div><div class="actions"><button class="btn" data-build-refresh>REFRESH</button><button class="btn primary" data-build-ask>ASK HERMUS</button></div></div><div class="workspace-grid"><aside class="pane"><div class="pane-head"><span>PROJECTS</span></div><div id="buildProjects" class="pane-list"></div></aside><section class="pane editor"><div class="pane-head"><span id="buildFileName">No file selected</span><button class="btn" id="buildSave" disabled>SAVE</button></div><textarea id="buildEditor" spellcheck="false" disabled placeholder="Select a text file…"></textarea><div class="editor-foot"><span id="buildMeta">Policy-bound workspace files</span><span id="buildDirty"></span></div></section><aside class="pane"><div class="pane-head"><span>HERMUS CONTEXT</span></div><div id="buildContext" class="context"></div></aside></div></div>';
    await refreshBuild();
  }
  async function refreshBuild(){
    const snap=await safe('/workshop/snapshot');if(!snap){qs('#buildProjects').innerHTML='<div class="empty">Workshop snapshot unavailable.</div>';return}
    state.projects=Array.isArray(snap.projects)?snap.projects:[];state.project=snap.current||snap.project||state.project;
    qs('#buildProjectTitle').textContent=state.project||'No project selected';
    qs('#buildProjects').innerHTML=state.projects.length?state.projects.map(p=>'<button type="button" class="tree-item '+(p.name===state.project?'active':'')+'" data-project="'+esc(p.name)+'">◇ '+esc(p.name)+'</button>').join(''):'<div class="empty">No workspace projects.</div>';
    const ctx=await safe('/context?project='+encodeURIComponent(state.project||'')+'&user_id=default&memory_limit=4');
    qs('#buildContext').innerHTML=[
      ['STATE',ctx?.summary?.state||'idle'],
      ['ATTENTION',Array.isArray(ctx?.attention)?ctx.attention.length+' active':'0 active'],
      ['PROJECT',state.project||'none'],
      ['GOALS',Array.isArray(ctx?.goals)?ctx.goals.map(g=>g.title||g.goal).slice(0,3).join(' · ')||'none':'none']
    ].map(x=>'<div class="context-row"><b>'+esc(x[0])+'</b><span>'+esc(x[1])+'</span></div>').join('');
    if(state.project)qs('#buildProjects').insertAdjacentHTML('beforeend','<div class="eyebrow" style="padding:14px 7px 6px">FILES</div><div id="buildFiles"></div>');
    const files=Array.isArray(snap.tree)?snap.tree:[];
    const fh=qs('#buildFiles');if(fh)fh.innerHTML=files.map(f=>f.type==='file'?'<button type="button" class="tree-item" data-file="'+esc(f.path)+'">· '+esc(f.name)+'</button>':'<div class="tree-item">▸ '+esc(f.name)+'</div>').join('')||'<div class="empty">Project is empty.</div>';
  }
  async function selectProject(name){try{const out=await api('/workshop/project/use',{method:'POST',body:JSON.stringify({name})});if(!out.success)throw new Error(out.error||'project switch failed');state.project=name;state.file=null;qs('#buildEditor').disabled=true;qs('#buildSave').disabled=true;refreshBuild();toast('Workspace · '+name)}catch(e){toast(e.message,true)}}
  async function openFile(path){if(!path||!state.project)return;try{const d=await api('/workshop/file?path='+encodeURIComponent(path)+'&project='+encodeURIComponent(state.project));if(d.editable===false){toast('File is read-only',true);return}state.file=path;qs('#buildFileName').textContent=path;qs('#buildEditor').disabled=false;qs('#buildSave').disabled=false;qs('#buildEditor').value=String(d.content||'');qs('#buildMeta').textContent=String(d.size??0)+' bytes · editable';qs('#buildDirty').textContent=''}catch(e){toast(e.message,true)}}
  async function saveFile(){if(!state.file||!state.project)return;try{await api('/workshop/file',{method:'PUT',body:JSON.stringify({project:state.project,path:state.file,content:qs('#buildEditor').value})});qs('#buildDirty').textContent='SAVED';toast('File saved');}catch(e){toast(e.message,true)}}

  async function loadSettings(){
    const host=qs('#settingsHost');if(!host)return;
    host.innerHTML='<div class="grid cols-2"><div class="card"><div class="card-head"><div><strong>DASHBOARD</strong><small>Local presentation preferences</small></div></div><div class="card-body list"><div class="row"><div class="row-main"><strong>Ambient motion</strong><small>Allow subtle motion in the JARVIS environment</small></div><button class="btn" data-setting="motion">'+(state.settings.motion===false?'OFF':'ON')+'</button></div><div class="row"><div class="row-main"><strong>Compact information</strong><small>Use denser spacing for large work sessions</small></div><button class="btn" data-setting="compact">'+(state.settings.compact?'ON':'OFF')+'</button></div><div class="row"><div class="row-main"><strong>Confirm risky actions</strong><small>Keep a confirmation step before destructive local actions</small></div><button class="btn" data-setting="confirm">'+(state.settings.confirm===false?'OFF':'ON')+'</button></div></div></div><div class="card"><div class="card-head"><div><strong>CORE HEALTH</strong><small>Live gateway status</small></div><button class="btn" data-settings-refresh>REFRESH</button></div><div id="settingsHealth" class="card-body"><div class="empty">Checking…</div></div></div></div>';
    const h=await safe('/api/v1/system/health');setOnline(!!h);
    qs('#settingsHealth').innerHTML=h?Object.entries(h).slice(0,20).map(([k,v])=>'<div class="row"><div class="row-main"><strong>'+esc(k.replace(/_/g,' '))+'</strong><small>'+esc(typeof v==='object'?Object.entries(v).slice(0,4).map(([a,b])=>a+': '+String(b)).join(' · '):String(v))+'</small></div>'+badge((v?.ok??v?.running??v?.installed??true)?'READY':'ATTENTION',(v?.ok??v?.running??v?.installed??true)?'good':'warn')+'</div>').join(''):'<div class="empty">Gateway health is unavailable.</div>';
  }
  function setSetting(name){state.settings[name]=!(state.settings[name]??(name==='confirm'));localStorage.setItem('hermus_dashboard_settings',JSON.stringify(state.settings));applySettings();loadSettings()}
  function applySettings(){document.body.classList.toggle('compact-mode',!!state.settings.compact);document.body.classList.toggle('motion-off',state.settings.motion===false)}

  async function refreshView(key){switch(key){case'overview':return refreshOverview();case'agents':return loadAgents();case'models':return loadModels();case'tools':return loadTools();case'missions':return loadMissions();case'knowledge':return loadKnowledge();case'personal':return loadPersonal();case'build':return loadBuild();case'browser':return loadBrowser();case'terminal':toolWorkspace('terminal');break;case'image':toolWorkspace('image');break;case'video':toolWorkspace('video');break;case'settings':return loadSettings();case'chat':renderChat();break;case'space':break}}
  async function loadView(key){try{await refreshView(key)}catch(e){toast(e.message,true)}}

  const paletteItems=Object.entries(views).map(([key,v])=>[key,v[0],v[1]]);
  function openPalette(){qs('#commandPalette').classList.add('open');qs('#commandPalette').setAttribute('aria-hidden','false');const i=qs('#paletteInput');i.value='';renderPalette();setTimeout(()=>i.focus(),0)}
  function closePalette(){qs('#commandPalette').classList.remove('open');qs('#commandPalette').setAttribute('aria-hidden','true')}
  function renderPalette(){
    const q=qs('#paletteInput').value.toLowerCase();const host=qs('#paletteList');
    host.innerHTML=paletteItems.filter(x=>(x[0]+' '+x[1]+' '+x[2]).toLowerCase().includes(q)).map((x,i)=>'<button class="palette-item" type="button" data-palette-view="'+esc(x[0])+'"><b>'+esc(x[1])+'</b><span>'+esc(x[2])+'</span><kbd>'+(i+1)+'</kbd></button>').join('')||'<div class="empty">No workspace found.</div>';
  }

  document.addEventListener('click',async e=>{
    const view=e.target.closest('[data-view]');
    if(view){e.preventDefault();openView(view.dataset.view);return}
    if(e.target.closest('#topCommand')||e.target.closest('#commandFocus')){openPalette();return}
    if(e.target.closest('#paletteClose')||e.target===qs('#commandPalette')){closePalette();return}
    const pv=e.target.closest('[data-palette-view]');if(pv){closePalette();openView(pv.dataset.paletteView);return}
    if(e.target.closest('[data-command]')){sendCommand(e.target.closest('[data-command]').dataset.command);return}
    const send=e.target.closest('#chatSend');if(send){await sendChat();return}
    if(e.target.closest('#voiceToggle')){toggleVoice();return}
    const a=e.target.closest('[data-agent-action]');if(a){const name=decodeURIComponent(a.dataset.agent);await agentAction(a.dataset.agent,a.dataset.agent_action);return}
    if(e.target.closest('[data-model-save]')){await saveModel();return}
    if(e.target.closest('[data-model-refresh]')){await loadModels();toast('Models refreshed');return}
    if(e.target.closest('[data-tool]')){const n=e.target.closest('[data-tool]').dataset.tool;const d=(await safe('/api/v1/system/capabilities'))?.tools?.[n];const box=qs('#toolInspector');if(box)box.textContent=n+' · '+String(d?.description||d?.summary||'Registered HERMUS capability')+' · '+((d?.enabled??d?.available??true)?'Ready':'Unavailable');return}
    if(e.target.closest('[data-mission-resume]')){const id=e.target.closest('[data-mission-resume]').dataset.missionResume;try{await api('/missions/'+encodeURIComponent(id)+'/resume',{method:'POST',body:'{}'});toast('Mission resume requested');loadMissions()}catch(err){toast(err.message,true)}return}
    if(e.target.closest('[data-routine-new]')){routineForm();return}
    if(e.target.closest('[data-routine-save]')){submitRoutine(false);return}
    if(e.target.closest('[data-routine-validate]')){submitRoutine(true);return}
    if(e.target.closest('[data-routine-toggle]')){const b=e.target.closest('[data-routine-toggle]');try{await api('/routines/'+encodeURIComponent(b.dataset.id)+'/enable',{method:'POST',body:JSON.stringify({enabled:b.dataset.enabled==='true'})});toast('Routine updated');loadMissions()}catch(err){toast(err.message,true)}return}
    if(e.target.closest('[data-routine-delete]')){const b=e.target.closest('[data-routine-delete]');try{await api('/routines/'+encodeURIComponent(b.dataset.id),{method:'DELETE'});toast('Routine deleted');loadMissions()}catch(err){toast(err.message,true)}return}
    if(e.target.closest('[data-knowledge-save]')){const text=qs('#knowledgeNote')?.value.trim();if(text){try{await api('/memory2/remember',{method:'POST',body:JSON.stringify({kind:'semantic',content:text,importance:5,project:state.project||null})});qs('#knowledgeNote').value='';qs('#knowledgeResult').textContent='Saved to HERMUS memory.';toast('Knowledge saved')}catch(err){toast(err.message,true)}}return}
    if(e.target.closest('[data-personal-run]')){try{await api('/personal-space/run',{method:'POST',body:'{}'});toast('HERMUS curiosity cycle started');loadPersonal()}catch(err){toast(err.message,true)}return}
    if(e.target.closest('[data-personal-refresh]')){loadPersonal();return}
    if(e.target.closest('[data-proposal]')){const b=e.target.closest('[data-proposal]'),action=b.dataset.proposal,id=b.dataset.proposalId;try{await api('/personal-space/proposals/'+encodeURIComponent(id)+'/'+(action==='approve'?'approve':'dismiss'),{method:'POST',body:'{}'});toast('Proposal '+action+'d');loadPersonal()}catch(err){toast(err.message,true)}return}
    if(e.target.closest('[data-browser-open]')){browserOpen();return}
    if(e.target.closest('[data-tool-run]')){runToolWorkspace(e.target.closest('[data-tool-run]').dataset.toolRun);return}
    if(e.target.closest('[data-build-refresh]')){refreshBuild();return}
    if(e.target.closest('[data-build-ask]')){openView('chat');qs('#chatInput')?.focus();return}
    if(e.target.closest('[data-project]')){selectProject(e.target.closest('[data-project]').dataset.project);return}
    if(e.target.closest('[data-file]')){openFile(e.target.closest('[data-file]').dataset.file);return}
    if(e.target.closest('#buildSave')){saveFile();return}
    if(e.target.closest('[data-setting]')){setSetting(e.target.closest('[data-setting]').dataset.setting);return}
    if(e.target.closest('[data-settings-refresh]')){loadSettings();return}
  },false);

  async function agentAction(encoded,action){
    const name=decodeURIComponent(encoded);
    try{
      if(action==='chat'){openView('chat');state.messages.push({who:'jarvis',text:'Agent chat ready for '+name+'.',time:'Now'});renderChat();return}
      await api(action==='start'?'/agents/start':'/agents/stop',{method:'POST',body:JSON.stringify({name})});toast((action==='start'?'Started ':'Stopped ')+name);loadAgents();refreshOverview();
    }catch(e){toast(e.message,true)}
  }

  async function sendChat(){const i=qs('#chatInput'),text=i?.value.trim();if(!text)return;i.value='';await sendCommand(text)}
  async function toggleVoice(){
    if(state.voice){state.voice.stop();state.voice=null;return}
    if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){toast('Voice input is unavailable in this browser',true);return}
    try{
      const stream=await navigator.mediaDevices.getUserMedia({audio:true}),rec=new MediaRecorder(stream),chunks=[];state.voice=rec;
      qs('#voiceToggle').textContent='STOP';
      rec.ondataavailable=e=>e.data.size&&chunks.push(e.data);
      rec.onstop=async()=>{stream.getTracks().forEach(t=>t.stop());state.voice=null;qs('#voiceToggle').textContent='VOICE';try{const fd=new Blob(chunks,{type:rec.mimeType||'audio/webm'});const q=localStorage.getItem('hermus_session_id')||'';const r=await fetch('/voice/command?session_id='+encodeURIComponent(q)+'&user_id=default',{method:'POST',headers:{'Content-Type':fd.type,...(token?{'X-Hermus-Token':token}:{})},body:fd});if(!r.ok)throw new Error('Voice request failed');toast('Voice request sent')}catch(e){toast(e.message,true)}};rec.start();toast('Listening…')
    }catch(e){toast(e.message,true)}
  }

  qs('#commandbarInput')?.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();sendCommand(e.currentTarget.value);e.currentTarget.value=''}})
  qs('#paletteInput')?.addEventListener('input',renderPalette)
  qs('#chatInput')?.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendChat()}})
  qs('#buildEditor')?.addEventListener('input',()=>{qs('#buildDirty').textContent='UNSAVED'})
  document.addEventListener('keydown',e=>{
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();openPalette()}
    if(e.key==='Escape')closePalette()
  });

  async function boot(){
    applySettings();
    const hash=location.hash.slice(1);openView(views[hash]?hash:'overview');
    await refreshOverview();
    renderChat();
  }
  boot();
})();
