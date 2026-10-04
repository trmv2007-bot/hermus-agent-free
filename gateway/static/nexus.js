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
    if (!r.ok) throw new Error(data.message || data.detail || data.error || `HTTP ${r.status}`);
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
    $('#coreState').textContent=label; $('#coreDetail').textContent=detail;
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

  async function refresh() {
    const started=performance.now();
    try {
      const [health, caps] = await Promise.all([api('/api/v1/system/health'), api('/api/v1/system/capabilities')]);
      state.online=true; $('#stateLabel').textContent='ONLINE'; $('#stateDot').className='state-dot';
      const healthEntries = health && typeof health === 'object' ? health : {};
      const healthy = Object.values(healthEntries).filter(v=>v && (v.ok===true || v.running===true || v.installed===true)).length;
      if(!state.missionLive) { const mission=$('#missionStatus'); if(mission) mission.textContent = healthy ? `${healthy} systems ready` : 'READY'; }
      renderCapabilities(caps.capabilities || caps);
      if (!state.busy) setState('READY','awaiting your command');
      document.body.style.setProperty('--rtt', `${Math.round(performance.now()-started)}ms`);
    } catch (e) {
      state.online=false; $('#stateLabel').textContent='OFFLINE'; $('#stateDot').className='state-dot bad'; const mission=$('#missionStatus'); if(mission&&!state.missionLive) mission.textContent='OFFLINE';
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
    'Agents': ['/api/v1/agents/list'],
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

  async function open(name) {
    $('#modalTitle').textContent=name; $('#modalLog').textContent='Loading…'; $('#overlay').classList.add('open'); $('#overlay').setAttribute('aria-hidden','false');
    const path=(surfaces[name] || [])[0];
    if(!path){$('#modalLog').textContent='Contextual HERMUS surface available through command and subsystem actions.';return;}
    try {
      if(path.endsWith('/')) { $('#modalLog').textContent=JSON.stringify({source:path, mission:state.mission, mode:'replay'},null,2); return; }
      const value=await api(path); $('#modalLog').textContent=typeof value==='string'?value:JSON.stringify(value,null,2);
    } catch(e) { $('#modalLog').textContent=e.message; }
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
      if(meta) meta.textContent='Model discovery unavailable: ' + e.message;
      const pill=$('#modelPill');
      if(pill) pill.textContent='MODEL · UNAVAILABLE';
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
    if(name === 'Focus' || name === 'Learning'){
      try{
        const endpoint=name==='Focus' ? '/focus' : '/learning?limit=6';
        const data=await api(endpoint);
        $('#modalTitle').textContent=name;
        $('#modalLog').textContent=JSON.stringify(data,null,2);
        $('#overlay').classList.add('open');
        $('#overlay').setAttribute('aria-hidden','false');
      }catch(e){
        $('#modalTitle').textContent=name;
        $('#modalLog').textContent=name+' unavailable: '+e.message;
        $('#overlay').classList.add('open');
        $('#overlay').setAttribute('aria-hidden','false');
      }
      return;
    }
    if(name === 'Routines'){
      try{
        const data=await api('/routines');
        const rows=Array.isArray(data.routines)?data.routines:[];
        $('#modalTitle').textContent='Routines';
        $('#modalLog').textContent=rows.length
          ? rows.map(r=>[
              String(r.enabled?'ON ':'OFF ') + String(r.name||r.id||'routine'),
              'trigger='+String(r.event_type||''),
              'action='+String(r.action_type||''),
              'task='+String(r.task||'').slice(0,220),
              'cooldown='+String(r.cooldown_seconds||0)+'s'
            ].join('\n')).join('\n\n')
          : 'No routines configured.';
        $('#overlay').classList.add('open');
        $('#overlay').setAttribute('aria-hidden','false');
      }catch(e){
        $('#modalTitle').textContent='Routines';
        $('#modalLog').textContent='Routine discovery unavailable: '+e.message;
        $('#overlay').classList.add('open'); $('#overlay').setAttribute('aria-hidden','false');
      }
      return;
    }
    if(name === 'Models'){
      await refreshModels(true);
      $('#modalTitle').textContent='Models';
      $('#modalLog').textContent=JSON.stringify({
        count:(modelState.catalog||{}).count || 0,
        models:(modelState.catalog||{}).models || [],
        selected:(modelState.selected||{}).selections || {}
      },null,2);
      $('#overlay').classList.add('open');
      $('#overlay').setAttribute('aria-hidden','false');
      return;
    }
    return originalOpen(name);
  };

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
  $('#workshopAsk')?.addEventListener('click',()=>$('#command')?.focus());
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
      host.innerHTML = '<div class="agent-row muted-row"><span class="agent-avatar">!</span><div><strong>Agent roster unavailable</strong><small>Check HERMUS system state</small></div><i></i></div>';
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
