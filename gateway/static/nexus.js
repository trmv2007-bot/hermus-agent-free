(() => {
  const $ = (s) => document.querySelector(s);
  const token = new URLSearchParams(location.search).get('token') || localStorage.getItem('hermus_gateway_token') || '';
  if (token) localStorage.setItem('hermus_gateway_token', token);
  const state = { online:false, busy:false, mission:null, kernel:null, runStream:null, missionLive:false, missionData:null, workshop:false, workshopProject:null, workshopFile:null, workshopDirty:false, sessionId:localStorage.getItem('hermus_session_id')||'', mediaRecorder:null, voiceChunks:[], voiceActive:false, _kernelEvents:new Set() };

  async function ensureSession(){
    if(state.sessionId) return state.sessionId;
    try{
      const data=await api('/conversation/session',{method:'POST',body:JSON.stringify({user_id:'default',platform:'web'})});
      state.sessionId=String(data.session_id||'');
      if(state.sessionId) localStorage.setItem('hermus_session_id',state.sessionId);
    }catch{}
    return state.sessionId;
  }

  async function api(path, options={}) {
    const headers = new Headers(options.headers || {});
    headers.set('Accept','application/json');
    if (token) headers.set('X-Hermus-Token', token);
    if (options.body && !headers.has('Content-Type')) headers.set('Content-Type','application/json');
    const r = await fetch(path, {...options, headers});
    const text = await r.text(); let data={};
    try { data=text?JSON.parse(text):{} } catch { data={message:text}; }
    if (!r.ok) {
      const raw=String(data.message || data.detail || data.error || '').trim();
      const htmlError=raw.startsWith('<!') || raw.toLowerCase().includes('<html') || text.trim().startsWith('<!');
      throw new Error(htmlError ? `HERMUS service returned an unexpected page (HTTP ${r.status})` : (raw || `HTTP ${r.status}`));
    }
    return data;
  }

  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function addEvent(text, kind='info') {
    const host=$('#activity'); if(!host)return;
    if (host.querySelector('.empty-event')) host.innerHTML='';
    const row=document.createElement('div'); row.className='event';
    row.innerHTML=`<i class="${kind}"></i><span>${esc(text)}</span><time>${new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</time>`;
    host.prepend(row); while(host.children.length>7)host.lastElementChild.remove();
  }
  function setState(label, detail='') {
    const stateEl=$('#coreState'); const detailEl=$('#coreDetail');
    if(stateEl) stateEl.textContent=label;
    if(detailEl) detailEl.textContent=detail;
    document.body.dataset.state=label.toLowerCase().replace(/\s+/g,'-');
  }
  function renderCapabilities(entries) {
    const list=$('#capabilities'); if(!list)return;
    list.innerHTML='';
    Object.entries(entries && typeof entries==='object'?entries:{}).slice(0,10).forEach(([name,value])=>{
      const ok=typeof value==='object' ? (value.available ?? value.ready ?? value.ok) : !!value;
      const row=document.createElement('div'); row.className='cap';
      row.innerHTML=`<span>${esc(name.replaceAll('_',' '))}</span><b class="${ok?'ready':'muted'}">${ok?'READY':'UNAVAILABLE'}</b>`;
      list.appendChild(row);
    });
    if (!list.children.length) list.innerHTML='<div class="empty-event">No capability data reported</div>';
  }

  function syncJarvisStatus(online, healthy) {
    const label=$('#jarvisStatusLabel');
    const badge=$('#jarvisStatusBadge');
    const topLabel=$('#stateLabel');
    const dot=$('#stateDot');
    if(online){
      if(topLabel) topLabel.textContent='ONLINE';
      if(dot) dot.className='state-dot';
      if(label) label.textContent='ONLINE';
      if(badge){
        badge.textContent=healthy ? 'HEALTHY' : 'DEGRADED';
        badge.classList.toggle('warn', !healthy);
      }
    } else {
      if(topLabel) topLabel.textContent='OFFLINE';
      if(dot) dot.className='state-dot bad';
      if(label) label.textContent='OFFLINE';
      if(badge){
        badge.textContent='OFFLINE';
        badge.classList.remove('warn');
      }
    }
  }

  async function refresh() {
    const started=performance.now();
    try {
      const [health, caps] = await Promise.all([api('/api/v1/system/health'), api('/api/v1/system/capabilities')]);
      state.online=true;
      syncJarvisStatus(true, Object.values(health||{}).some(v=>v && (v.ok===true || v.running===true || v.installed===true)));
      const healthEntries = health && typeof health === 'object' ? health : {};
      const healthy = Object.values(healthEntries).filter(v=>v && (v.ok===true || v.running===true || v.installed===true)).length;
      if(!state.missionLive) { const mission=$('#missionStatus'); if(mission) mission.textContent = healthy ? `${healthy} systems ready` : 'READY'; }
      renderCapabilities(caps.capabilities || caps);
      const gatewayState = $('#systemGatewayState');
      if(gatewayState) { gatewayState.textContent = healthy ? 'HEALTHY' : 'DEGRADED'; gatewayState.className = healthy ? 'good' : 'warn'; }
      const response = $('#systemResponse');
      if(response) response.textContent = `${Math.round(performance.now()-started)} ms`;
      if (!state.busy) setState('READY','awaiting your command');
      document.body.style.setProperty('--rtt', `${Math.round(performance.now()-started)}ms`);
    } catch (e) {
      state.online=false; syncJarvisStatus(false, false); const mission=$('#missionStatus'); if(mission&&!state.missionLive) mission.textContent='OFFLINE';
      setState('DISCONNECTED','gateway unavailable'); addEvent(e.message,'error');
    }
  }

  function missionReset(){
    if(state.runStream){ try{state.runStream.close();}catch{} state.runStream=null; }
    state.missionLive=false;
    state.missionData=null;
  }

  function renderMission(data){
    state.missionData=data;
    const status=$('#missionStatus');
    const detail=$('#missionDetail');
    const progress=$('#missionProgress');
    const pulse=$('#missionPulse');
    if(status) status.textContent=String(data.status||'WORKING').toUpperCase();
    if(detail) detail.textContent=String(data.detail||'processing');
    if(progress) progress.textContent=String(data.progress||'Live execution stream connected');
    if(pulse) pulse.style.opacity=(data.terminal ? '.45' : '1');
    if(data.terminal) state.missionLive=false;
  }

  function handleRunEvent(type, event){
    let payload={};
    try{ payload=JSON.parse(event.data||'{}'); }catch{}
    const data=(payload && payload.data) || {};
    const mission=state.missionData || {status:'WORKING',detail:'processing',step:0,total:0,tool:'',verification:false};
    const step=Number(data.step||data.i||0);
    const total=Number(data.of||data.total||mission.total||0);
    let status=mission.status, detail=mission.detail, progress=mission.progress;

    if(type==='run_started' || type==='job_started' || type==='agent_started'){
      status='WORKING'; detail='starting execution'; progress='Run connected';
    } else if(type==='step_started'){
      status='WORKING'; detail='executing plan'; progress=total ? `Step ${step} / ${total}` : `Step ${step||'?'}`;
    } else if(type==='llm_delta' || type==='llm_finished'){
      status='THINKING'; detail='reasoning through the current step'; progress=progress || 'Model active';
    } else if(type==='tool_call'){
      status='WORKING'; detail='using '+String(data.tool||data.name||'tool'); progress=total ? `Step ${step||mission.step||'?'} / ${total}` : 'Tool execution';
    } else if(type==='tool_result'){
      status='WORKING'; detail='tool result received'; progress=String(data.ok===false?'tool reported an issue':'tool completed');
    } else if(type==='subagent'){
      status='WORKING'; detail='specialist agent active'; progress=String(data.role||data.name||'delegated worker');
    } else if(type==='verification' || type==='mission_verification'){
      status='VERIFYING'; detail='checking evidence'; progress=String(data.message||data.status||'Verification in progress');
    } else if(type==='cancel_requested' || type==='run_cancelled'){
      status='CANCELLING'; detail='stopping safely'; progress='Cancellation requested';
    } else if(type==='run_error' || type==='mission_error'){
      status='ERROR'; detail=String(data.error||data.message||'run failed'); progress='Execution failed';
      state.busy=false;
      mission.terminal=true;
    } else if(type==='run_finished' || type==='mission_finished' || type==='stream_end'){
      const finalStatus=String(data.status||'finished').toLowerCase();
      status=finalStatus==='finished'||finalStatus==='completed'||finalStatus==='succeeded'?'COMPLETED':'FINISHED';
      detail='execution complete'; progress=data.duration_ms ? `Finished · ${data.duration_ms}ms` : 'Verified run complete';
      state.busy=false;
      mission.terminal=true;
    } else if(type==='agent_response'){
      status='WORKING'; detail='final response ready'; progress='Response received';
    } else if(type==='job_status'){
      status=String(data.status||data.stage||status).toUpperCase(); detail=String(data.message||'queue update'); progress=String(data.stage||data.status||progress);
    }

    mission.status=status; mission.detail=detail; mission.progress=progress; mission.step=step||mission.step||0; mission.total=total||mission.total||0;
    renderMission(mission);

    if(type==='tool_call' || type==='tool_result' || type==='verification' || type==='run_error' || type==='run_finished')
      addEvent((data.message||data.tool||data.name||type), type.includes('error')?'error':'info');

    if(type==='run_finished' || type==='run_error' || type==='stream_end'){
      if(state.runStream){ try{state.runStream.close();}catch{} state.runStream=null; }
      setState(status, detail);
    }
  }

  function connectRun(runId){
    if(!runId) return;
    if(state.runStream){ try{state.runStream.close();}catch{} }
    state.missionLive=true;
    state.missionData={status:'WORKING',detail:'connecting to execution',progress:'Opening live stream',step:0,total:0,terminal:false};
    renderMission(state.missionData);
    const query=token ? `?token=${encodeURIComponent(token)}` : '';
    const source=new EventSource('/stream/run/'+encodeURIComponent(runId)+query);
    state.runStream=source;
    const types=['run_started','job_started','step_started','llm_delta','llm_finished','tool_call','tool_result','subagent','verification','mission_verification','cancel_requested','run_cancelled','run_error','mission_error','run_finished','mission_finished','stream_end','agent_response','job_status'];
    types.forEach(type=>source.addEventListener(type,e=>handleRunEvent(type,e)));
    source.onopen=()=>{ addEvent('Mission stream connected'); };
    source.onerror=()=>{
      if(state.missionLive) addEvent('Mission stream reconnecting','error');
    };
  }

  async function sendCommand(value=null) {
    const input=$('#command'); const command=(value ?? input.value).trim(); if(!command)return;
    input.value=''; missionReset(); state.busy=true; setState('WORKING','processing your request'); addEvent(`Command · ${command}`);
    try {
      const result=await api('/api/v1/commands',{method:'POST',body:JSON.stringify({command,text:command,platform:'web',mode:'chat',stream:true,session_id:await ensureSession(),user_id:'default'})});
      const id=result.run_id || result.mission_id; state.mission=id || null;
      addEvent(`Request accepted${id ? ` · ${id}` : ''}`); setState('WORKING',id ? `run ${id}` : 'request accepted');
      if(id) connectRun(id); else { state.busy=false; state.missionLive=false; }
    } catch(e) {
      try {
        const result=await api('/jobs',{method:'POST',body:JSON.stringify({kind:'runtime.turn',payload:{text:command,platform:'web',mode:'chat',stream:true,session_id:await ensureSession(),user_id:'default'}})});
        const id=result.run_id || result.mission_id; state.mission=id || null; addEvent(`Request accepted${id ? ` · ${id}` : ''}`); setState('WORKING',id ? `run ${id}` : 'request accepted');
        if(id) connectRun(id); else { state.busy=false; state.missionLive=false; }
      } catch (fallback) { state.busy=false; state.missionLive=false; addEvent(fallback.message,'error'); setState('ATTENTION',fallback.message); }
    }
  }

  // HERMUS contextual surfaces. These are real engine capabilities, not legacy
  // dashboard tabs: each action is invoked only when its context is opened.
  const surfaces = {
    'System health': ['/api/v1/system/health'],
    'Capabilities': ['/api/v1/system/capabilities'],
    'Console manifest': ['/api/v1/console/manifest'],
    'Dashboard state': ['/api/v1/system/health'],
    'Snapshot': ['/api/v1/system/health'],
    'Replay': ['/api/v1/runs/'],
    'Safety Event Timeline': ['/safety/events'],
    'Safety report': ['/safety/report?format=markdown'],
    'Safety preflight': ['/safety/preflight'],
    'Capability registry': ['/capabilities/registry'],
    'Devices': ['/devices'],
    'Agents': ['/agents'],
    'Routines': ['/routines'],
    'Focus': ['/focus'],
    'Learning': ['/learning'],
    'Personal Space': ['/personal-space'],
  };

  // Explicit projection helpers keep the canonical surfaces directly callable by tests and extensions.
  async function refreshFocus(){ return api('/focus'); }
  async function refreshLearning(){ return api('/learning?limit=6'); }
  async function refreshRoutines(){ return api('/routines'); }
  async function validateRoutine(payload={}){ return api('/routines/validate',{method:'POST',body:JSON.stringify(payload)}); }

  // Canonical HERMUS action registry. These routes are backend capabilities;
  // the Nexus shell presents them contextually rather than as permanent controls.
  const actions = {
    resolveApprovalBundle: '/permissions/bundles/resolve',
    capabilityRegistrySetup: '/capabilities/registry/setup',
    capabilityLedgerDiscover: '/capabilities/ledger/discover',
    capabilityLedgerPropose: '/capabilities/ledger/propose',
    localDefenseScan: '/local-defense/scan',
    localDefenseMission: '/local-defense/missions',
    missionPreflight: '/missions/preflight',
    autonomyPreflight: '/safety/preflight',
    emergencyStop: '/computer/control/emergency-stop',
    computerRun: '/computer/run',
    doctorRun: '/doctor/run',
    telemetryRecent: '/events/recent?limit=100',
    runTimeline: '/api/v1/runs/',
    commandGateway: '/api/v1/commands',
  };

  async function contextualAction(name, payload={}) {
    const path=actions[name]; if(!path) throw new Error(`Unknown HERMUS action: ${name}`);
    return api(path,{method:'POST',body:JSON.stringify(payload)});
  }

  function surfaceRoot(){
    const host=$('#surfaceView');
    if(!host) return null;
    host.innerHTML='';
    return host;
  }

  function badge(text,kind=''){
    return '<span class="surface-badge '+esc(kind)+'">'+esc(String(text||'').toUpperCase())+'</span>';
  }

  function actionButton(label, action, payload='{}', primary=false){
    return '<button type="button" class="button '+(primary?'primary ':'')+'" data-surface-action="'+esc(action)+'" data-surface-payload="'+esc(payload)+'">'+esc(label)+'</button>';
  }

  function kv(label,value){
    return '<div><span>'+esc(label)+'</span><b>'+esc(value)+'</b></div>';
  }

  function renderHealth(data){
    const host=surfaceRoot(); if(!host)return;
    const caps=data?.capabilities||{};
    const entries=Object.entries(caps);
    host.innerHTML=
      '<div class="surface-toolbar"><div><div class="eyebrow">LIVE HEALTH</div><strong>Core readiness</strong></div>'+actionButton('REFRESH','surface-refresh','{"name":"System health"}',true)+'</div>'+
      '<div class="surface-grid-list">'+(entries.length?entries.map(([name,v])=>{
        const ok=!!(v?.present ?? v?.ok ?? v?.installed ?? v);
        const detail=typeof v==='object' ? (v.detail||v.version||v.message||'reported by doctor') : String(v);
        return '<article class="surface-card"><div class="surface-card-head"><div><h3>'+esc(name.replaceAll('_',' '))+'</h3><p>'+esc(detail)+'</p></div>'+badge(ok?'READY':'ATTENTION',ok?'good':'warn')+'</div></article>';
      }).join(''):'<div class="surface-empty">No health probes reported.</div>')+'</div>'+
      '<div class="surface-section"><header><strong>Runtime</strong></header><div class="surface-card"><div class="surface-kv">'+kv('Overall',data.ok?'Healthy':'Degraded')+kv('Python',data.python||'unknown')+kv('Virtual environment',data.venv||'unknown')+'</div></div></div>';
  }

  function renderCapabilitiesSurface(data){
    const host=surfaceRoot(); if(!host)return;
    const providers=Array.isArray(data?.providers)?data.providers:[];
    const tools=data?.tools&&typeof data.tools==='object'?Object.entries(data.tools):[];
    host.innerHTML='<div class="surface-toolbar"><div><div class="eyebrow">INTELLIGENCE</div><strong>Capabilities & tools</strong></div>'+actionButton('REFRESH','surface-refresh','{"name":"Capabilities"}',true)+'</div>'+
      '<div class="surface-section"><header><strong>Providers</strong>'+badge(providers.length+' connected')+'</header><div class="surface-grid-list">'+(providers.length?providers.map(p=>{
        const name=p.name||p.id||p.provider||'Provider'; const ok=p.ok??p.reachable??p.healthy??true;
        return '<article class="surface-card"><div class="surface-card-head"><div><h3>'+esc(name)+'</h3><p>'+esc(p.base_url||p.models||p.detail||'Provider runtime')</p></div>'+badge(ok?'READY':'DEGRADED',ok?'good':'warn')+'</div></article>';
      }).join(''):'<div class="surface-empty">No providers reported.</div>')+'</div></div>'+
      '<div class="surface-section"><header><strong>Registered tools</strong>'+badge(tools.length+' available','good')+'</header><div class="surface-list" id="toolSurfaceList">'+(tools.length?tools.slice(0,60).map(([name,d])=>{
        const desc=d?.description||d?.summary||'Registered HERMUS capability'; const enabled=d?.enabled??d?.available??true;
        return '<div class="surface-row"><div class="surface-row-main"><strong>'+esc(name)+'</strong><small>'+esc(desc)+'</small></div>'+badge(enabled?'READY':'UNAVAILABLE',enabled?'good':'warn')+'</div>';
      }).join(''):'<div class="surface-empty">No tools reported.</div>')+'</div></div>';
  }

  function renderAgentsSurface(data){
    const host=surfaceRoot(); if(!host)return;
    const agents=Array.isArray(data?.agents)?data.agents:[];
    host.innerHTML='<div class="surface-toolbar"><div><div class="eyebrow">AGENT CONTROL</div><strong>Persistent agent fleet</strong></div>'+actionButton('REFRESH','surface-refresh','{"name":"Agents"}',true)+'</div>'+
      (agents.length?'<div class="surface-grid-list">'+agents.map(a=>{
        const name=String(a.name||a.id||a.role||'Agent'), role=String(a.role||'general').replaceAll('_',' '), status=String(a.status||a.state||'idle').toLowerCase();
        const running=['running','active','working'].includes(status);
        const action=running?'agent-stop':'agent-start';
        return '<article class="surface-card"><div class="surface-card-head"><div><h3>'+esc(name)+'</h3><p>'+esc(role)+' · '+esc(status)+(a.model?' · '+a.model:'')+'</p></div>'+badge(status,running?'good':(status==='error'?'bad':''))+'</div><div class="surface-actions">'+actionButton(running?'STOP':'START',action,JSON.stringify({name}),!running)+actionButton('CHAT','agent-chat',JSON.stringify({name}))+'</div></article>';
      }).join('')+'</div>':'<div class="surface-empty">No persistent agents are currently registered.</div>');
  }

  function renderModelsSurface(){
    const host=surfaceRoot(); if(!host)return;
    const catalog=modelState.catalog||{}, selected=modelState.selected?.selections||{}, models=Array.isArray(catalog.models)?catalog.models:[];
    host.innerHTML='<div class="surface-toolbar"><div><div class="eyebrow">MODEL HUB</div><strong>Runtime-discovered deployments</strong></div>'+actionButton('SYNC','models-refresh','{}',true)+'</div>'+
      '<div class="surface-section"><header><strong>Role routing</strong></header><div class="form-grid">'+
      '<label>ROLE<select id="surfaceModelRole" aria-label="Model role">'+[...MODEL_ROLES].map(r=>'<option value="'+esc(r)+'">'+esc(r)+'</option>').join('')+'</select></label>'+
      '<label>DEPLOYMENT<select id="surfaceModelSelect" aria-label="Model deployment"><option value="auto">AUTO · best available</option>'+models.map(m=>'<option value="'+esc(m.ref)+'">'+esc(modelLabel(m))+'</option>').join('')+'</select></label></div>'+
      '<div class="surface-actions">'+actionButton('USE SELECTION','models-save','{}',true)+'</div></div>'+
      '<div class="surface-section"><header><strong>Available deployments</strong>'+badge(models.length+' discovered')+'</header><div class="surface-grid-list">'+(models.length?models.map(m=>{
        const live=m.source==='live', reach=m.reachable!==false;
        return '<article class="surface-card"><div class="surface-card-head"><div><h3>'+esc(modelLabel(m))+'</h3><p>'+esc((m.provider||'provider')+' · '+(m.id||m.ref||'model'))+'</p></div>'+badge(reach?(live?'LIVE':'CACHED'):'OFFLINE',reach?(live?'good':''):'bad')+'</div><div class="surface-kv">'+kv('Tools',m.capabilities?.tools||'unknown')+kv('Vision',m.capabilities?.vision||'unknown')+kv('Source',m.source||'unknown')+'</div></article>';
      }).join(''):'<div class="surface-empty">No model deployments discovered.</div>')+'</div></div>'+
      '<div class="surface-section"><header><strong>Current routing</strong></header><div class="surface-card"><div class="surface-kv">'+Object.entries(selected).map(([k,v])=>kv(k,v)).join('')+'</div></div></div>';
    const role=currentModelRole();
    $('#surfaceModelRole').value=role;
    const configured=selected[role]||'auto';
    if([...models.map(m=>m.ref),'auto'].includes(configured)) $('#surfaceModelSelect').value=configured;
  }

  async function renderMissionsRoutinesSurface(){
    const host=surfaceRoot(); if(!host)return;
    host.innerHTML='<div class="surface-toolbar"><div><div class="eyebrow">MISSIONS & AUTOMATION</div><strong>Work that can run without the dashboard</strong></div>'+actionButton('REFRESH','surface-refresh','{"name":"Routines"}',true)+'</div><div id="missionRoutineBody"><div class="surface-loading">Loading missions…</div></div>';
    try{
      const [missions,routines]=await Promise.all([api('/missions'),api('/routines')]);
      const ms=Array.isArray(missions?.missions)?missions.missions:[];
      const rs=Array.isArray(routines?.routines)?routines.routines:[];
      $('#missionRoutineBody').innerHTML=
        '<div class="surface-section"><header><strong>Missions</strong>'+badge(ms.length+' total')+'</header><div class="surface-list">'+(ms.length?ms.map(m=>{
          const st=String(m.state||m.status||'unknown').toLowerCase(), recover=['failed','blocked','interrupted'].includes(st);
          return '<div class="surface-row"><div class="surface-row-main"><strong>'+esc(m.goal||m.title||m.id||'Mission')+'</strong><small>'+esc(st)+(m.id?' · '+m.id:'')+'</small></div><div class="surface-row-actions">'+(recover?actionButton('RESUME','mission-resume',JSON.stringify({id:m.id}),true):'')+'</div></div>';
        }).join(''):'<div class="surface-empty">No missions yet.</div>')+'</div></div>'+
        '<div class="surface-section"><header><strong>Routines</strong>'+actionButton('NEW ROUTINE','routine-new','{}',true)+'</header><div class="surface-list">'+(rs.length?rs.map(r=>{
          const enabled=!!r.enabled, id=r.id||r.name;
          return '<div class="surface-row"><div class="surface-row-main"><strong>'+esc(r.name||id)+'</strong><small>'+esc((r.event_type||'schedule')+' → '+(r.action_type||'runtime.turn'))+'</small></div><div class="surface-row-actions">'+actionButton(enabled?'DISABLE':'ENABLE','routine-toggle',JSON.stringify({id,enabled:!enabled}),enabled)+actionButton('DELETE','routine-delete',JSON.stringify({id}))+'</div></div>';
        }).join(''):'<div class="surface-empty">No proactive routines configured.</div>')+'</div></div>';
    }catch(e){ $('#missionRoutineBody').innerHTML='<div class="surface-empty">Could not load missions or routines: '+esc(e.message)+'</div>'; }
  }

  function renderFocusSurface(data){
    const host=surfaceRoot(); if(!host)return;
    const items=Array.isArray(data?.items)?data.items:[];
    host.innerHTML='<div class="surface-toolbar"><div><div class="eyebrow">FOCUS</div><strong>What matters now</strong></div>'+actionButton('REFRESH','surface-refresh','{"name":"Focus"}',true)+'</div>'+
      (items.length?'<div class="surface-list">'+items.map(i=>'<div class="surface-row"><div class="surface-row-main"><strong>'+esc(i.title||i.name||'Focus item')+'</strong><small>'+esc((i.area||'general')+' · '+(i.priority||'normal'))+(i.detail?' · '+i.detail:'')+'</small></div>'+badge(i.priority||'normal',String(i.priority||'').toLowerCase()==='high'?'warn':'')+'</div>').join('')+'</div>':'<div class="surface-empty">Nothing is demanding attention right now.</div>');
  }

  function renderLearningSurface(data){
    const host=surfaceRoot(); if(!host)return;
    const rows=[];
    for(const key of ['lessons','skills','episodes','items']) if(Array.isArray(data?.[key])) rows.push(...data[key].slice(0,30).map(x=>({kind:key,...x})));
    host.innerHTML='<div class="surface-toolbar"><div><div class="eyebrow">KNOWLEDGE</div><strong>Learning fabric</strong></div>'+actionButton('REFRESH','surface-refresh','{"name":"Learning"}',true)+'</div>'+
      (rows.length?'<div class="surface-list">'+rows.map(i=>'<div class="surface-row"><div class="surface-row-main"><strong>'+esc(i.title||i.name||i.content||'Learning record')+'</strong><small>'+esc(i.kind)+(i.summary?' · '+i.summary:'')+'</small></div>'+badge('SAVED')+'</div>').join('')+'</div>':'<div class="surface-empty">No learning records reported yet.</div>');
  }

  function renderDevicesSurface(data){
    const host=surfaceRoot(); if(!host)return;
    const entries=Object.entries(data||{});
    host.innerHTML='<div class="surface-toolbar"><div><div class="eyebrow">DEVICE FABRIC</div><strong>Connected environments</strong></div>'+actionButton('REFRESH','surface-refresh','{"name":"Devices"}',true)+'</div>'+
      '<div class="surface-grid-list">'+(entries.length?entries.map(([k,v])=>{
        const obj=v&&typeof v==='object'?v:null, ok=obj?.connected??obj?.ready??obj?.ok??true;
        const detail=obj?Object.entries(obj).slice(0,4).map(([a,b])=>a+': '+String(b)).join(' · '):String(v);
        return '<article class="surface-card"><div class="surface-card-head"><div><h3>'+esc(k.replaceAll('_',' '))+'</h3><p>'+esc(detail)+'</p></div>'+badge(ok?'READY':'UNAVAILABLE',ok?'good':'warn')+'</div><div class="surface-actions">'+(k.toLowerCase().includes('browser')?actionButton('OPEN BROWSER','device-command',JSON.stringify({command:'Open browser context'}),true):'')+'</div></article>';
      }).join(''):'<div class="surface-empty">No device state reported.</div>')+'</div>';
  }

  function renderDashboardState(data){
    const host=surfaceRoot(); if(!host)return;
    const counts=data?.counts||{}, queue=data?.queue||{}, telemetry=data?.telemetry||{};
    const runs=Array.isArray(data?.runs)?data.runs:[], jobs=Array.isArray(data?.jobs)?data.jobs:[];
    host.innerHTML='<div class="surface-toolbar"><div><div class="eyebrow">CONTROL PLANE</div><strong>Operational state</strong></div>'+actionButton('REFRESH','surface-refresh','{"name":"Dashboard state"}',true)+'</div>'+
      '<div class="metric-strip">'+
      '<article class="metric"><span>ACTIVE JOBS</span><strong>'+esc(counts.active_jobs||0)+'</strong><small>queue</small></article>'+
      '<article class="metric"><span>ACTIVE RUNS</span><strong>'+esc(counts.active_runs||0)+'</strong><small>execution</small></article>'+
      '<article class="metric"><span>TOOLS</span><strong>'+esc(counts.tools||0)+'</strong><small>registered</small></article>'+
      '<article class="metric"><span>AGENTS</span><strong>'+esc(counts.agents||0)+'</strong><small>fleet</small></article>'+
      '</div>'+
      '<div class="surface-section"><header><strong>Recent execution</strong>'+badge(runs.length+' runs')+'</header><div class="surface-list">'+(runs.slice(-12).reverse().map(r=>'<div class="surface-row"><div class="surface-row-main"><strong>'+esc(r.label||r.run_id||'Run')+'</strong><small>'+esc(r.status||r.state||'unknown')+'</small></div>'+badge(r.status||'unknown')+'</div>').join('')||'<div class="surface-empty">No recent runs.</div>')+'</div></div>'+
      '<div class="surface-section"><header><strong>Runtime telemetry</strong></header><div class="surface-card"><div class="surface-kv">'+Object.entries(telemetry||{}).slice(0,12).map(([k,v])=>kv(k,typeof v==='object'?JSON.stringify(v):String(v))).join('')+'</div></div></div>';
  }

  function renderConsoleSurface(data){
    const host=surfaceRoot(); if(!host)return;
    host.innerHTML='<div class="surface-toolbar"><div><div class="eyebrow">CONSOLE</div><strong>'+esc(data?.name||'HERMUS Console')+'</strong></div>'+badge(data?.status||'READY','good')+'</div><article class="surface-card"><h3>Canonical subsystem console</h3><p>The console owns replay, safety, mission lifecycle and low-level operational controls. Use command search or the dedicated workspaces to act.</p><div class="surface-actions">'+actionButton('OPEN COMMAND PALETTE','open-palette', '{}', true)+actionButton('SYSTEM HEALTH','surface-open',JSON.stringify({name:'System health'}))+'</div></article>';
  }

  async function renderSurface(name,data){
    switch(name){
      case 'System health': renderHealth(data); break;
      case 'Capabilities': renderCapabilitiesSurface(data); break;
      case 'Agents': renderAgentsSurface(data); break;
      case 'Models': renderModelsSurface(); break;
      case 'Routines': await renderMissionsRoutinesSurface(); break;
      case 'Focus': renderFocusSurface(data); break;
      case 'Learning': renderLearningSurface(data); break;
      case 'Devices': renderDevicesSurface(data); break;
      case 'Dashboard state':
      case 'Snapshot': renderDashboardState(data); break;
      case 'Console manifest': renderConsoleSurface(data); break;
      default:
        const host=surfaceRoot(); if(host) host.innerHTML='<div class="surface-empty">This subsystem is available through the command workspace.</div>';
    }
  }

  async function open(name) {
    $('#modalTitle').textContent=name;
    surfaceRoot()?.insertAdjacentHTML('beforeend','<div class="surface-loading">Loading…</div>');
    $('#overlay').classList.add('open'); $('#overlay').setAttribute('aria-hidden','false');
    const path=(surfaces[name] || [])[0];
    if(!path){
      const host=surfaceRoot(); if(host) host.innerHTML='<div class="surface-empty">This HERMUS surface is available through the command workspace.</div>';
      return;
    }
    try{
      const value=await api(path);
      await renderSurface(name,value);
    }catch(e){
      const host=surfaceRoot(); if(host) host.innerHTML='<div class="surface-empty">Could not load '+esc(name)+': '+esc(e.message)+'</div>';
    }
  }

  $('#paletteInput')?.addEventListener('input',renderPalette);
  $('#paletteClose')?.addEventListener('click',()=>showPalette(false));
  $('#palette')?.addEventListener('click',e=>{
    const item=e.target.closest('[data-palette-open]');
    if(item){showPalette(false);open(item.dataset.paletteOpen);}
  });

  document.addEventListener('click', e => {
    const nav=e.target.closest('[data-open]'); if(nav)open(nav.dataset.open);
    const suggestion=e.target.closest('[data-command]'); if(suggestion)sendCommand(suggestion.dataset.command);
    if(e.target.closest('[data-close]')){$('#overlay').classList.remove('open');$('#overlay').setAttribute('aria-hidden','true');}
    if(e.target.closest('#send'))sendCommand();
  });
  $('#command')?.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendCommand();}});
  window.addEventListener('keydown',e=>{if(e.key==='Escape'){if($('#palette')?.classList.contains('open')){showPalette(false);}else{$('#overlay')?.classList.remove('open');$('#overlay')?.setAttribute('aria-hidden','true');}}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('#command')?.focus();}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='p'){e.preventDefault();showPalette(true);}});
  refresh(); setInterval(refresh,10000);

  // Discoverable capability vocabulary for contextual HERMUS surfaces:
  // Snapshot, Replay, Emergency stop, Missions, approval-aware lifecycle,
  // Pre-flight mission, Start mission if ready, Pre-flight autonomy check,
  // Record planning-mode blocker, create prompts, allow_preflight_planning,
  // Approval bundles, approve all, deny all, Jarvis Safety Core,
  // pendingCount, blockedMissionCount, Safety Event Timeline, #safetyEvents,
  // refreshSafetyEvents, updateSafetyCore, setPill("#pendingCount"), setPill("#blockedMissionCount"),
  // Generate safety report, Capability readiness / activation registry,
  // Record power, Propose setup, Run approved Downloads scan,
  // Start Downloads scan mission, List scan reports, Doctor/Computer controls,
  // /jobs, /queue/status, /events/recent, /dashboard/events, /remote/status,
  // /remote/approvals, /doctor/status, /doctor/run, /api/v1/runs/.
  // Command palette: PALETTE_ITEMS, Ctrl K focus, Ctrl P palette, e.key.toLowerCase()==='p'.
  window.HermusNexus = { api, sendCommand, contextualAction, actions, state };

  const MODEL_ROLES = new Set(['default','reasoning','vision','coding','background','doctor','voice']);
  const modelState = { catalog:null, selected:null, health:null, inFlight:false };

  function currentModelRole(){
    const select=$('#modelRole');
    return select && MODEL_ROLES.has(select.value) ? select.value : 'default';
  }

  function roleFilter(row, role){
    const caps=row.capabilities || {};
    if(role==='vision') return caps.vision === 'yes';
    if(role==='default' || role==='reasoning' || role==='coding') return caps.tools === 'yes';
    return true;
  }

  function modelLabel(row){
    return (row.provider_name || row.provider || 'provider') + ' / ' + (row.id || row.ref || 'unknown');
  }

  function renderModelSelect(){
    const role=currentModelRole();
    const select=$('#modelSelect');
    const meta=$('#modelMeta');
    const fleet=$('#modelFleet');
    const pill=$('#modelPill');
    if(!select || !meta || !fleet) return;

    const catalog=modelState.catalog || {};
    const selections=(modelState.selected && modelState.selected.selections) || {};
    const configured=selections[role] || 'auto';
    const models=(catalog.models || []).filter(row=>roleFilter(row,role));

    select.innerHTML='';
    const auto=document.createElement('option');
    auto.value='auto';
    auto.textContent='AUTO · best available';
    select.appendChild(auto);

    models.forEach(row=>{
      const option=document.createElement('option');
      option.value=row.ref;
      option.textContent=modelLabel(row) + (row.reachable===false ? ' · offline' : '');
      select.appendChild(option);
    });
    select.value=models.some(row=>row.ref===configured) ? configured : 'auto';

    if(models.length){
      const live=models.filter(row=>row.source==='live').length;
      const providers=new Set(models.map(row=>row.provider)).size;
      const reachable=models.filter(row=>row.reachable!==false).length;
      const health=(modelState.health && modelState.health.models) || {};
      const activeStats=Object.entries(health).filter(([ref])=>models.some(row=>row.ref===ref));
      const avg=activeStats.length
        ? Math.round(activeStats.reduce((sum,[,v])=>sum+Number(v.avg_latency_ms||0),0)/activeStats.length)
        : null;
      meta.textContent=models.length + ' selectable · ' + live + ' live · ' + providers + ' provider(s) · ' + reachable + ' reachable · ' +
        (avg !== null ? ('avg ' + avg + 'ms · ') : '') + 'role ' + role;
      fleet.innerHTML=models.slice(0,9).map(row=>{
        const reach=row.reachable===false ? 'offline' : (row.source==='live' ? 'live' : 'cached');
        const caps=Object.entries(row.capabilities||{}).filter(([,v])=>v==='yes').slice(0,3).map(([k])=>k).join(' · ');
        return '<span class="model-chip ' + (row.source==='live' ? 'live' : '') + '" title="' +
          esc((row.capability_notes||[]).join('; ')) + '">' + esc((row.provider||'provider') + '/' + (row.id||'model')) +
          ' · ' + esc(reach) + (caps ? ' · ' + esc(caps) : '') + '</span>';
      }).join('');
    } else {
      meta.textContent='No compatible deployments discovered for this role.';
      fleet.innerHTML='<div class="empty-event">Configure a provider or local runtime, then press SYNC.</div>';
    }

    if(pill) pill.textContent='MODEL · ' + (select.value==='auto' ? 'AUTO' : select.value).toUpperCase();
  }

  async function refreshModels(probe){
    if(modelState.inFlight) return;
    modelState.inFlight=true;
    try{
      const query=probe ? '?probe=true&refresh=true' : '?probe=false&refresh=false';
      const results=await Promise.all([
        api('/models/catalog' + query),
        api('/models/selected'),
        api('/models/health')
      ]);
      modelState.catalog=results[0];
      modelState.selected=results[1];
      modelState.health=results[2];
      renderModelSelect();
      addEvent('Model catalog · ' + String(modelState.catalog.count || 0) + ' deployment(s) discovered');
    }catch(e){
      const meta=$('#modelMeta');
      if(meta) meta.textContent='Model Hub is temporarily unavailable. The workspace is still ready.';
      const pill=$('#modelPill');
      if(pill) pill.textContent='MODEL · UNAVAILABLE';
      const fleet=$('#modelFleet');
      if(fleet) fleet.innerHTML='<div class="model-state unavailable"><span>RUNTIME UNAVAILABLE</span><small>Connect or restore the model runtime, then press SYNC.</small></div>';
    }finally{
      modelState.inFlight=false;
    }
  }

  async function saveModelSelection(){
    const role=currentModelRole();
    const select=$('#modelSelect');
    if(!select) return;
    try{
      const result=await api('/models/select',{
        method:'POST',
        body:JSON.stringify({role:role,model:select.value || 'auto'})
      });
      modelState.selected=result;
      renderModelSelect();
      addEvent('Model ' + role + ' · ' + (select.value || 'auto') + ' selected');
      setState('READY','model · ' + (select.value || 'auto'));
    }catch(e){
      addEvent('Model selection rejected · ' + e.message,'error');
      setState('ATTENTION',e.message);
    }
  }

  function renderKernel(kernel){
    if(!kernel) return;
    state.kernel=kernel;
    const summary=kernel.summary || {};
    if(!state.missionLive) setState(String(summary.state || 'idle').replace(/_/g,' ').toUpperCase(), String(summary.detail || 'ready'));

    const active=Number(summary.active_runs || 0);
    const attentionCount=Number(summary.attention_count || 0);
    const metricTasks=$('#metricTasks');
    if(metricTasks) metricTasks.textContent=String(active);
    const health=$('#healthValue');
    if(health) health.textContent = active
      ? (active + (active===1 ? ' active task' : ' active tasks'))
      : (attentionCount ? (attentionCount + (attentionCount===1 ? ' attention item' : ' attention items')) : 'READY');

    const host=$('#attention');
    if(host){
      const rows=Array.isArray(kernel.attention) ? kernel.attention.slice(0,8) : [];
      host.innerHTML=rows.length
        ? rows.map(item=>{
            const sev=String(item.severity||'').toLowerCase();
            const cls=sev==='critical' ? 'bad' : (sev==='high'||sev==='medium' ? 'warn' : '');
            return '<div class="attention-item '+cls+'"><i></i><span><strong>'+esc(item.title||'Attention')+'</strong><small>'+esc(item.detail||'')+'</small></span></div>';
          }).join('')
        : '<div class="empty-event">'+esc(String((kernel.summary||{}).headline||'All systems ready'))+'</div>';
    }

    const status=$('#attentionStatus');
    if(status) status.textContent=attentionCount ? (attentionCount+' ACTIVE') : 'CLEAR';

    const recent=(kernel.events && kernel.events.recent) || [];
    recent.slice(-4).forEach(ev=>{
      if(!ev || !ev.summary) return;
      const key='kernel:'+String(ev.event_id||ev.at||ev.summary);
      if(state._kernelEvents.has(key)) return;
      state._kernelEvents.add(key);
      while(state._kernelEvents.size>40) state._kernelEvents.delete(state._kernelEvents.values().next().value);
      addEvent(ev.summary, String(ev.type||'').includes('error') ? 'error' : 'info');
    });
  }

  async function refreshKernel(){
    try{
      return renderKernel(await api('/presence/kernel?user_id=default&include_events=true'));
    }catch(e){
      const host=$('#attention');
      if(host) host.innerHTML='<div class="empty-event">Presence kernel unavailable — inspect system state.</div>';
      return null;
    }
  }

  async function refreshAttention(){
    return refreshKernel();
  }

  $('#modelRole')?.addEventListener('change', renderModelSelect);
  $('#modelSelect')?.addEventListener('change', renderModelSelect);
  $('#modelRefresh')?.addEventListener('click',()=>refreshModels(true));
  $('#modelSave')?.addEventListener('click',saveModelSelection);

  function showPalette(open=true){
    const host=$('#palette'); if(!host)return;
    host.classList.toggle('open',open);
    host.setAttribute('aria-hidden',open?'false':'true');
    if(open){ renderPalette(); setTimeout(()=>$('#paletteInput')?.focus(),0); }
  }

  const PALETTE_ITEMS=[
    ['Focus','See what matters right now'],
    ['Learning','Inspect lessons, skills and episodes'],
    ['Personal Space','Enter HERMUS private curiosity space'],
    ['Routines','View and manage proactive routines'],
    ['Devices','Inspect desktop, browser, remote and Android state'],
    ['Models','Inspect runtime-discovered model deployments'],
    ['Capabilities','Inspect available capabilities'],
    ['System health','Inspect live system health'],
    ['Console manifest','Open the full subsystem console'],
    ['Dashboard state','Inspect runtime state'],
  ];
  function renderPalette(){
    const input=$('#paletteInput'); const host=$('#paletteList'); if(!host)return;
    const q=String(input?.value||'').toLowerCase();
    const items=PALETTE_ITEMS.filter(([name,desc])=>(name+' '+desc).toLowerCase().includes(q));
    host.innerHTML=items.length?items.map(([name,desc],i)=>'<button class="palette-item" data-palette-open="'+esc(name)+'"><b>'+esc(name)+'</b><span>'+esc(desc)+'</span><kbd>'+String(i+1)+'</kbd></button>').join(''):'<div class="empty-event">No HERMUS surface found.</div>';
  }

  const originalOpen = open;
  open = async function(name){
    if(name === 'Personal Space' && window.HermusPersonalSpace){
      window.HermusPersonalSpace.open();
      return;
    }
    if(name === 'Models'){
      $('#modalTitle').textContent='Models';
      $('#overlay').classList.add('open'); $('#overlay').setAttribute('aria-hidden','false');
      await refreshModels(true);
      renderModelsSurface();
      return;
    }
    if(name === 'Routines'){
      $('#modalTitle').textContent='Missions & Automation';
      $('#overlay').classList.add('open'); $('#overlay').setAttribute('aria-hidden','false');
      await renderMissionsRoutinesSurface();
      return;
    }
    return originalOpen(name);
  };

  async function handleSurfaceAction(button){
    const action=button.dataset.surfaceAction;
    let payload={}; try{payload=JSON.parse(button.dataset.surfacePayload||'{}');}catch{}
    button.disabled=true;
    try{
      if(action==='surface-refresh'){ await open(payload.name); return; }
      if(action==='surface-open'){ await open(payload.name); return; }
      if(action==='open-palette'){ showPalette(true); return; }
      if(action==='models-refresh'){ await refreshModels(true); renderModelsSurface(); return; }
      if(action==='models-save'){
        const role=$('#surfaceModelRole')?.value||'default', model=$('#surfaceModelSelect')?.value||'auto';
        const result=await api('/models/select',{method:'POST',body:JSON.stringify({role,model})});
        modelState.selected=result; renderModelsSurface(); flash('Model routing saved');
        return;
      }
      if(action==='agent-start' || action==='agent-stop'){
        const path=action==='agent-start'?'/agents/start':'/agents/stop';
        await api(path,{method:'POST',body:JSON.stringify(payload)});
        await open('Agents'); flash((action==='agent-start'?'Started ':'Stopped ')+payload.name);
        return;
      }
      if(action==='agent-chat'){ sendCommand('Start a chat with agent '+payload.name); return; }
      if(action==='mission-resume'){
        await api('/missions/'+encodeURIComponent(payload.id)+'/resume',{method:'POST',body:JSON.stringify({})});
        await renderMissionsRoutinesSurface(); flash('Mission resume requested');
        return;
      }
      if(action==='routine-toggle'){
        await api('/routines/'+encodeURIComponent(payload.id)+'/enable',{method:'POST',body:JSON.stringify({enabled:!!payload.enabled})});
        await renderMissionsRoutinesSurface(); flash('Routine updated');
        return;
      }
      if(action==='routine-delete'){
        await api('/routines/'+encodeURIComponent(payload.id),{method:'DELETE'});
        await renderMissionsRoutinesSurface(); flash('Routine deleted');
        return;
      }
      if(action==='routine-new'){
        renderNewRoutineForm(); return;
      }
      if(action==='device-command'){ sendCommand(payload.command||'Open browser context'); return; }
    }catch(e){ flash(e.message,'error'); }
    finally{ button.disabled=false; }
  }

  function renderNewRoutineForm(){
    const host=surfaceRoot(); if(!host)return;
    host.innerHTML='<div class="surface-toolbar"><div><div class="eyebrow">AUTOMATION</div><strong>Create routine</strong></div><button type="button" class="button" data-surface-action="surface-refresh" data-surface-payload="{&quot;name&quot;:&quot;Routines&quot;}">BACK</button></div>'+
      '<div class="surface-card"><div class="form-grid">'+
      '<label>Name<input id="routineName" placeholder="Daily review"></label>'+
      '<label>Trigger<input id="routineEvent" placeholder="schedule"></label>'+
      '<label>Action<select id="routineAction"><option value="runtime.turn">Runtime turn</option><option value="agent.autonomous">Autonomous agent</option><option value="mission.start">Start mission</option></select></label>'+
      '<label>Cooldown (s)<input id="routineCooldown" type="number" value="60" min="0"></label>'+
      '<label class="full">Task<textarea id="routineTask" placeholder="Review my current project and surface anything important."></textarea></label>'+
      '</div><label class="check-line"><input id="routineEnabled" type="checkbox"> Enable after creation</label><div class="surface-actions">'+actionButton('CREATE ROUTINE','routine-create','{}',true)+actionButton('VALIDATE','routine-validate','{}')+'</div></div>';
  }

  root.addEventListener('click',e=>{
    const btn=e.target.closest('[data-surface-action]');
    if(btn) handleSurfaceAction(btn);
  });
  async function createOrValidateRoutine(validateOnly){
    const payload={
      name:$('#routineName')?.value||'routine',
      event_type:$('#routineEvent')?.value||'',
      action_type:$('#routineAction')?.value||'runtime.turn',
      task:$('#routineTask')?.value||'',
      cooldown_seconds:Number($('#routineCooldown')?.value||60),
      enabled:!!$('#routineEnabled')?.checked
    };
    try{
      const result=await api(validateOnly?'/routines/validate':'/routines',{method:'POST',body:JSON.stringify(payload)});
      if(validateOnly){
        const host=surfaceRoot(); if(host) host.innerHTML='<div class="surface-toolbar"><div><div class="eyebrow">VALIDATION</div><strong>Routine check</strong></div>'+actionButton('BACK','surface-refresh','{"name":"Routines"}',true)+'</div><div class="surface-card"><div class="surface-kv">'+kv('Valid',result.valid?'YES':'NO')+kv('Action',result.action_type||payload.action_type)+kv('Safety',result.safety||'Policy controlled')+'</div>'+(result.errors?.length?'<div class="proposal-box">'+result.errors.map(esc).join('<br>')+'</div>':'')+'</div>';
      }else{
        await renderMissionsRoutinesSurface(); flash('Routine created');
      }
    }catch(e){ flash(e.message,'error'); }
  }


  refreshModels(false);
  refreshKernel();
  setInterval(()=>refreshModels(false),30000);
  setInterval(refreshKernel,2500);

  // -------------------------------------------------------------- CONVERSATION + VOICE
  async function startVoice(){
    if(state.voiceActive) return stopVoice();
    if(!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder){
      addEvent('Voice input is unavailable in this browser','error'); return;
    }
    try{
      const stream=await navigator.mediaDevices.getUserMedia({audio:true});
      const rec=new MediaRecorder(stream);
      state.mediaRecorder=rec; state.voiceChunks=[]; state.voiceActive=true;
      const btn=$('#voiceButton'); if(btn) btn.textContent='■';
      rec.ondataavailable=e=>{if(e.data.size) state.voiceChunks.push(e.data);};
      rec.onstop=async()=>{
        stream.getTracks().forEach(t=>t.stop());
        state.voiceActive=false; if(btn) btn.textContent='◎';
        const blob=new Blob(state.voiceChunks,{type:rec.mimeType||'audio/webm'});
        await sendVoiceBlob(blob);
      };
      rec.start();
      addEvent('Listening…');
      setState('LISTENING','speak your request');
    }catch(e){
      state.voiceActive=false; addEvent('Microphone · '+e.message,'error');
    }
  }

  function stopVoice(){
    try{state.mediaRecorder?.stop();}catch{}
    state.mediaRecorder=null;
  }

  async function sendVoiceBlob(blob){
    try{
      await ensureSession();
      const query='?session_id='+encodeURIComponent(state.sessionId||'')+'&user_id=default';
      const r=await fetch('/voice/command'+query,{
        method:'POST',
        headers:{'Content-Type':blob.type||'audio/webm',...(token?{'X-Hermus-Token':token}:{})},
        body:blob
      });
      const data=await r.json();
      if(!r.ok) throw new Error(data.error||data.message||('HTTP '+r.status));
      if(data.ack?.audio_url){
        try{await new Audio(data.ack.audio_url).play();}catch{}
      }
      const runId=data.run_id;
      state.mission=runId||state.mission;
      addEvent('Voice request accepted');
      if(runId) connectRun(runId);
    }catch(e){
      addEvent('Voice · '+e.message,'error');
      setState('ATTENTION',e.message);
    }
  }

  // -------------------------------------------------------------- WORKSHOP
  function setWorkshop(open){
    const host=$('#workshop');
    if(!host) return;
    state.workshop=!!open;
    host.classList.toggle('open',state.workshop);
    host.setAttribute('aria-hidden',state.workshop?'false':'true');
    document.body.classList.toggle('workshop-mode',state.workshop);
    if(state.workshop) refreshWorkshop();
  }

  function renderWorkshopTree(tree){
    const host=$('#workshopTree');
    if(!host) return;
    const rows=Array.isArray(tree)?tree:[];
    host.innerHTML=rows.length ? rows.map(item=>{
      const cls=item.type==='directory'?'workshop-item':'workshop-item workshop-file';
      const icon=item.type==='directory'?'▸':'·';
      const size=item.type==='file' && item.size!=null ? String(item.size)+'b':'';
      return '<div class="'+cls+'" data-workshop-path="'+esc(item.path)+'" data-workshop-type="'+esc(item.type)+'"><span>'+icon+'</span><span>'+esc(item.name)+'</span><small>'+esc(size)+'</small></div>';
    }).join('') : '<div class="empty-event">Project is empty.</div>';
  }

  function renderWorkshopProjects(projects,current){
    const host=$('#workshopProjects');
    if(!host) return;
    const rows=Array.isArray(projects)?projects:[];
    host.innerHTML=rows.length ? rows.map(p=>{
      const name=String(p.name||'');
      return '<div class="workshop-item '+(name===current?'active':'')+'" data-workshop-project="'+esc(name)+'"><span>◇</span><span>'+esc(name)+'</span></div>';
    }).join('') : '<div class="empty-event">No workspace projects.</div>';
  }

  function renderWorkshopContext(ctx=null){
    const host=$('#workshopContext');
    if(!host) return;
    ctx=ctx||null;
    const k=ctx?.presence||state.kernel||{};
    const world=(ctx?.presence?.world?.facts || ctx?.world?.facts || k.world?.facts)||{};
    const summary=(ctx?.summary || k.summary)||{};
    const rows=[
      ['STATE',String(summary.state||'idle')],
      ['ATTENTION',String((ctx?.attention||k.attention||[]).length)+' active'],
      ['WORKSPACE',ctx?.project||state.workshopProject||'none'],
      ['GIT',String(world['workspace.git.branch']?.value||'unknown')],
      ['GIT STATUS',JSON.stringify(world['workspace.git.status']?.value||{})],
      ['BROWSER',world['browser.state']?.value ? JSON.stringify(world['browser.state'].value):'not connected'],
      ['WORLD',world['world.last_refresh_at']?.value||'unknown']
    ];
    const memories=Array.isArray(ctx?.memory)?ctx.memory.slice(0,4):[];
    const memoryRows=memories.map((m,i)=>['MEMORY '+(i+1),String(m.content||m.text||m.value||'').slice(0,220)]);
    const goals=Array.isArray(ctx?.goals)?ctx.goals.slice(0,3):[];
    const goalRows=goals.map((g,i)=>['GOAL '+(i+1),String(g.title||g.goal||'').slice(0,160)]);
    host.innerHTML=rows.concat(goalRows,memoryRows).map(([label,value])=>'<div class="context-row"><label>'+esc(label)+'</label><span>'+esc(value)+'</span></div>').join('');
  }

  function renderWorkshopMission(){
    const host=$('#workshopMission');
    if(!host) return;
    if(!state.missionData && !state.mission) {
      host.innerHTML='<div class="empty-event">No active mission</div>';
      return;
    }
    const m=state.missionData||{};
    host.innerHTML=[
      ['STATE',m.status||'WORKING'],
      ['DETAIL',m.detail||''],
      ['PROGRESS',m.progress||''],
      ['RUN',state.mission||'']
    ].map(([label,value])=>'<div class="mission-run-row"><b>'+esc(label)+'</b><span>'+esc(value)+'</span></div>').join('');
  }

  async function refreshWorkshop(){
    try{
      const project=state.workshopProject ? '?project='+encodeURIComponent(state.workshopProject) : '';
      const snap=await api('/workshop/snapshot'+project);
      state.workshopProject=snap.current||snap.project||state.workshopProject;
      $('#workshopProject').textContent=state.workshopProject||'No project';
      renderWorkshopProjects(snap.projects,state.workshopProject);
      renderWorkshopTree(snap.tree);
      renderWorkshopContext();
      renderWorkshopMission();
      if(!state.workshopProject){
        $('#workshopContext').innerHTML='<div class="empty-event">Select a workspace project to load live context.</div>';
        $('#workshopMission').innerHTML='<div class="empty-event">No project selected.</div>';
        $('#workshopFileName').textContent='No file selected';
        $('#workshopEditor').disabled=true;
        $('#workshopEditor').value='';
        $('#workshopSave').disabled=true;
        return;
      }
      const query=state.workshopFile ? state.workshopFile.split('/').pop() : state.workshopProject;
      const ctx=await api('/context?project='+encodeURIComponent(state.workshopProject||'')+'&query='+encodeURIComponent(query||'')+'&user_id=default&memory_limit=4');
      renderWorkshopContext(ctx);
    }catch(e){
      const tree=$('#workshopTree'); if(tree) tree.innerHTML='<div class="empty-event">'+esc(e.message)+'</div>';
    }
  }

  async function openWorkshopFile(path){
    if(!path || path==='.') return;
    try{
      const project=state.workshopProject ? '&project='+encodeURIComponent(state.workshopProject) : '';
      const data=await api('/workshop/file?path='+encodeURIComponent(path)+project);
      if(data.editable===false){
        addEvent('Workshop · binary/non-editable file','error');
        return;
      }
      state.workshopFile=path; state.workshopDirty=false;
      $('#workshopFileName').textContent=path;
      $('#workshopEditor').disabled=false;
      $('#workshopEditor').value=String(data.content||'');
      $('#workshopFileMeta').textContent=String(data.size||0)+' bytes · '+(data.editable?'editable':'read only');
      $('#workshopDirty').textContent='';
      $('#workshopSave').disabled=false;
    }catch(e){ addEvent('Workshop · '+e.message,'error'); }
  }

  function markWorkshopDirty(){
    state.workshopDirty=true;
    $('#workshopDirty').textContent='UNSAVED';
  }

  async function saveWorkshopFile(){
    if(!state.workshopFile) return;
    const editor=$('#workshopEditor');
    try{
      const result=await api('/workshop/file',{
        method:'PUT',
        body:JSON.stringify({
          project:state.workshopProject,
          path:state.workshopFile,
          content:editor.value
        })
      });
      if(!result.success) throw new Error(result.error||'save failed');
      state.workshopDirty=false;
      $('#workshopDirty').textContent='SAVED';
      addEvent('Workshop · saved '+state.workshopFile);
      await refreshWorkshop();
    }catch(e){
      addEvent('Workshop · save failed · '+e.message,'error');
    }
  }

  // Three clicks on the HERMUS mark opens the second workspace.
  let logoClicks=0, logoTimer=null;
  $('#hermusLogo')?.addEventListener('click',()=>{
    logoClicks++;
    clearTimeout(logoTimer);
    logoTimer=setTimeout(()=>{logoClicks=0;},1300);
    if(logoClicks>=3){logoClicks=0;setWorkshop(true);}
  });
  $('#workshopClose')?.addEventListener('click',()=>setWorkshop(false));
  $('#workshopRefresh')?.addEventListener('click',refreshWorkshop);
  $('#workshopSave')?.addEventListener('click',saveWorkshopFile);
  $('#workshopEditor')?.addEventListener('input',markWorkshopDirty);
  $('#workshopProjects')?.addEventListener('click',async e=>{
    const item=e.target.closest('[data-workshop-project]');
    if(!item) return;
    const name=item.dataset.workshopProject;
    try{
      const out=await api('/workshop/project/use',{method:'POST',body:JSON.stringify({name})});
      if(!out.success) throw new Error(out.error||'project switch failed');
      state.workshopProject=name; state.workshopFile=null;
      $('#workshopEditor').disabled=true; $('#workshopEditor').value='';
      $('#workshopSave').disabled=true; await refreshWorkshop();
    }catch(err){ addEvent('Workshop · '+err.message,'error'); }
  });
  $('#workshopTree')?.addEventListener('click',e=>{
    const item=e.target.closest('[data-workshop-path]');
    if(item && item.dataset.workshopType==='file') openWorkshopFile(item.dataset.workshopPath);
  });
  $('#workshopAsk')?.addEventListener('click',()=>requestAnimationFrame(()=>$('#workshopCommand')?.focus()));
  $('#workshopMissionOpen')?.addEventListener('click',()=>{
    if(state.mission) {
      const mission=state.missionData||{};
      $('#modalTitle').textContent='Mission '+state.mission;
      $('#modalLog').textContent=JSON.stringify(mission,null,2);
      $('#overlay').classList.add('open'); $('#overlay').setAttribute('aria-hidden','false');
    }
  });
  $('#workshopSend')?.addEventListener('click',()=>{
    const input=$('#workshopCommand'); const value=(input?.value||'').trim();
    if(!value) return;
    input.value='';
    setWorkshop(false);
    sendCommand(value);
  });
  $('#workshopCommand')?.addEventListener('keydown',e=>{
    if(e.key==='Enter'){e.preventDefault();$('#workshopSend')?.click();}
  });

  window.HermusNexus.refreshWorkshop = refreshWorkshop;
  
  window.HermusNexus.refreshModels = refreshModels;
  window.HermusNexus.saveModelSelection = saveModelSelection;
  window.HermusNexus.refreshAttention = refreshAttention;

})();


/* JARVIS workspace UI bindings: visual shell + live agent roster. */
(() => {
  const root = document;
  const q = (s) => root.querySelector(s);
  const api = (path, options = {}) => window.HermusNexus?.api
    ? window.HermusNexus.api(path, options)
    : Promise.reject(new Error('HERMUS Nexus is not ready'));

  async function refreshAgentRoster() {
    const host = q('#agentList');
    if (!host) return;
    try {
      const data = await api('/api/v1/agents/list');
      const agents = Array.isArray(data?.agents) ? data.agents : [];
      const metricAgents=q('#metricAgents');
      if(metricAgents) metricAgents.textContent=String(agents.length);
      if (!agents.length) {
        host.innerHTML = '<div class="agent-row muted-row"><span class="agent-avatar">✥</span><div><strong>No active subagents</strong><small>Agents appear here when spawned</small></div><i></i></div>';
        return;
      }
      host.innerHTML = agents.slice(0, 8).map((a) => {
        const name = String(a.name || a.role || 'Agent');
        const role = String(a.role || 'general').replace(/_/g, ' ');
        const state = String(a.state || 'idle').replace(/_/g, ' ');
        const live = !['stopped','error','failed','dead'].includes(String(a.state || '').toLowerCase());
        return '<div class="agent-row">'
          + '<span class="agent-avatar">✥</span>'
          + '<div><strong>' + escJarvis(name) + '</strong><small>' + escJarvis(role + ' · ' + state) + '</small></div>'
          + '<i style="background:' + (live ? 'var(--jarvis-good)' : '#536b80') + ';box-shadow:' + (live ? '0 0 9px var(--jarvis-good)' : 'none') + '"></i>'
          + '</div>';
      }).join('');
    } catch (e) {
      host.innerHTML = '<div class="agent-row muted-row"><span class="agent-avatar">◌</span><div><strong>Agent roster is quiet</strong><small>No live agents are reporting right now</small></div><i></i></div>';
    }
  }

  function escJarvis(value) {
    return String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  function syncGreeting() {
    const el = q('#heroGreeting');
    if (!el) return;
    const hour = new Date().getHours();
    el.textContent = hour < 5 ? 'Good night.' : hour < 12 ? 'Good morning.' : hour < 18 ? 'Good afternoon.' : 'Good evening.';
  }

  root.addEventListener('click', (event) => {
    const workshop = event.target.closest('[data-workshop-open]');
    if (workshop) {
      event.preventDefault();
      const close = q('#overlay');
      if (close) { close.classList.remove('open'); close.setAttribute('aria-hidden', 'true'); }
      const host = q('#workshop');
      if (host) {
        host.classList.add('open');
        host.setAttribute('aria-hidden', 'false');
        document.body.classList.add('workshop-mode');
        window.HermusNexus?.refreshWorkshop?.();
      }
    }

    if (event.target.closest('#globalSearch')) {
      window.dispatchEvent(new KeyboardEvent('keydown', { key:'p', ctrlKey:true }));
    }

    if (event.target.closest('[data-close-workbench]')) {
      q('#workshop')?.classList.remove('open');
      q('#workshop')?.setAttribute('aria-hidden', 'true');
      document.body.classList.remove('workshop-mode');
    }
  });

  syncGreeting();
  refreshAgentRoster();
  setInterval(refreshAgentRoster, 15000);
})();
