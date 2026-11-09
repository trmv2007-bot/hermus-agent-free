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
  /* Periodic pollers (model catalog every 30s, kernel every 2.5s) report the
   * same healthy line over and over. A quiet system must not paint the feed
   * with repeats: a run of identical messages collapses into one row with a
   * ×N counter and the timestamp of the latest occurrence. Any different
   * message starts a new row, so state changes still surface immediately. */
  function addEvent(text, kind='info') {
    const host=$('#activity'); if(!host)return;
    if (host.querySelector('.empty-event')) host.innerHTML='';
    const first=host.firstElementChild;
    if (first && first.dataset.msg===text && first.dataset.kind===kind){
      const n=(Number(first.dataset.repeat)||1)+1;
      first.dataset.repeat=String(n);
      first.querySelector('.count').textContent='×'+n;
      first.querySelector('time').textContent=new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
      return;
    }
    const row=document.createElement('div'); row.className='event';
    row.dataset.msg=text; row.dataset.kind=kind; row.dataset.repeat='1';
    row.innerHTML=`<i class="${kind}"></i><span>${esc(text)}</span><b class="count" aria-label="repeated"></b><time>${new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</time>`;
    host.prepend(row); while(host.children.length>7)host.lastElementChild.remove();
  }
  function setState(label, detail='') {
    const stateEl=$('#coreState'); const detailEl=$('#coreDetail');
    if(stateEl) stateEl.textContent=label;
    if(detailEl) detailEl.textContent=detail;
    document.body.dataset.state=label.toLowerCase().replace(/\s+/g,'-');
    // The hero chip's second line ("Idle. The queue is clear.") is owned by
    // control-core's poller. While a run is live, that line must agree with
    // the run instead of contradicting the label two pixels away; otherwise
    // it mirrors this state's own detail so label+line always match.
    const now=$('#coreNow');
    if(now) now.textContent = (state.busy || state.missionLive)
      ? (state.commandInFlight ? `working on “${state.commandInFlight}”` : 'working on your request…')
      : (detail || 'awaiting your command');
  }

  /* ------------------------------------------------------------------ *
   * Conversation transcript — the answer must never be swallowed again.
   *
   * The run bus publishes the final response as 'agent_response' with the
   * full text; before round 3 that event only flipped a status chip and the
   * answer was lost. The transcript renders it, keeps the last stretch of
   * the conversation in localStorage so a refresh does not erase it, and
   * turns "no model backend" answers into a one-click fix. */
  const TRANSCRIPT_KEY='hermus_transcript_v1';
  const MODEL_HELP_RE=/ollama|no usable (hosted )?provider|no model backend|model backend|api key|provider.*not|not running at/i;
  function transcriptLoad(){
    try{ const raw=JSON.parse(localStorage.getItem(TRANSCRIPT_KEY)||'[]'); return Array.isArray(raw)?raw.slice(-20):[]; }catch{ return []; }
  }
  function transcriptSave(rows){
    try{ localStorage.setItem(TRANSCRIPT_KEY, JSON.stringify(rows.slice(-20))); }catch{/* private mode etc */}
  }
  function messageEl(role, text, error){
    const row=document.createElement('div');
    row.className='msg '+role+(error?' error':'');
    const who=role==='user'?'You':'HERMUS';
    row.innerHTML='<div class="msg-who">'+esc(who)+'</div>'
      +'<div class="msg-text">'+esc(text).replace(/\n/g,'<br>')+'</div>'
      +(MODEL_HELP_RE.test(String(text||''))
        ? '<div class="msg-actions"><button type="button" class="btn ghost" data-goto="systems">Connect a model →</button></div>'
        : '');
    return row;
  }
  function addMessage(role, text, opts={}){
    const body=$('#transcriptBody'); if(!body) return;
    if(body.querySelector('.note')) body.innerHTML='';
    body.appendChild(messageEl(role, text, !!opts.error));
    body.scrollTop=body.scrollHeight;
    const rows=transcriptLoad();
    rows.push({role:String(role),text:String(text),error:!!opts.error,at:Date.now()});
    transcriptSave(rows);
  }
  function transcriptRestore(){
    const body=$('#transcriptBody'); if(!body) return;
    const rows=transcriptLoad();
    if(!rows.length) return;
    body.innerHTML='';
    rows.forEach(r=>body.appendChild(messageEl(r.role==='user'?'user':'hermus', String(r.text||''), !!r.error)));
    body.scrollTop=body.scrollHeight;
  }
  $('#transcriptClear')?.addEventListener('click',()=>{
    transcriptSave([]);
    const body=$('#transcriptBody');
    if(body) body.innerHTML='<div class="note">Ask HERMUS anything — answers stream here and are kept in this browser.</div>';
  });
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

  /* The badge explains itself on hover/focus instead of posing a riddle:
   * which subsystems were probed, which of them reported unhealthy, and where
   * to go next. The cause list is built from the live health snapshot, so the
   * tooltip never drifts from what the gateway actually reported. */
  function badgeTitle(healthy, health){
    const entries=Object.entries(health && typeof health==='object' ? health : {});
    if(healthy){
      const okKeys=entries.filter(([,v])=>v && (v.ok===true || v.running===true || v.installed===true)).map(([k])=>k);
      return 'Healthy — subsystems reporting ready: ' + (okKeys.join(', ') || 'none named');
    }
    const badKeys=entries.filter(([,v])=>!(v && (v.ok===true || v.running===true || v.installed===true))).map(([k])=>k);
    return entries.length
      ? 'Degraded — no subsystem reports healthy. Not ready: ' + (badKeys.join(', ') || 'none') + '. Open the Systems tab → Doctor panel to diagnose.'
      : 'Degraded — the health endpoint returned no subsystems to check. Open the Systems tab → Doctor panel to diagnose.';
  }
  function syncJarvisStatus(online, healthy, health) {
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
        badge.title=badgeTitle(healthy, health);
      }
    } else {
      if(topLabel) topLabel.textContent='OFFLINE';
      if(dot) dot.className='state-dot bad';
      if(label) label.textContent='OFFLINE';
      if(badge){
        badge.textContent='OFFLINE';
        badge.classList.remove('warn');
        badge.title='Gateway unreachable — the page keeps its last snapshot and keeps retrying. Check the gateway process or network.';
      }
    }
  }

  async function refresh() {
    const started=performance.now();
    try {
      const [health, caps] = await Promise.all([api('/api/v1/system/health'), api('/api/v1/system/capabilities')]);
      state.online=true;
      const healthEntries = health && typeof health === 'object' ? health : {};
      const healthyCount = Object.values(healthEntries).filter(v=>v && (v.ok===true || v.running===true || v.installed===true)).length;
      const healthy = healthyCount > 0;
      state.lastHealthHealthy = healthy;
      syncJarvisStatus(true, healthy, healthEntries);
      if(!state.missionLive) { const mission=$('#missionStatus'); if(mission) mission.textContent = healthyCount ? `${healthyCount} systems ready` : 'READY'; }
      renderCapabilities(caps.capabilities || caps);
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
      state.commandInFlight='';
      mission.terminal=true;
      addMessage('hermus', String(data.error||data.message||'The run failed.'), {error:true});
    } else if(type==='run_finished' || type==='mission_finished' || type==='stream_end'){
      const finalStatus=String(data.status||'finished').toLowerCase();
      status=finalStatus==='finished'||finalStatus==='completed'||finalStatus==='succeeded'?'COMPLETED':'FINISHED';
      detail=state.lastResponse ? 'answered — see the conversation' : 'execution complete';
      progress=data.duration_ms ? `Finished · ${data.duration_ms}ms` : 'Verified run complete';
      state.busy=false;
      state.commandInFlight='';
      mission.terminal=true;
      if(!state.lastResponse && (type==='run_finished'||type==='mission_finished')){
        addMessage('hermus',
          'The run finished'+(data.duration_ms?` in ${data.duration_ms}ms`:'')+' but returned no response text. Open the Jobs tab and click the job row for its event log.',
          {error:false});
      }
    } else if(type==='agent_response'){
      status='WORKING'; detail='final response ready'; progress='Response received';
      // Round 3: the response text IS the product. Render it, remember it,
      // and let a run that ends without one say so honestly.
      const text=String(data.text||'').trim();
      if(text){
        state.lastResponse=text;
        addMessage('hermus', text);
        state.answeredAt=Date.now();
        const activeTab=document.querySelector('nav [role=tab].on');
        if(activeTab && activeTab.dataset.tab!=='overview' && window.toast){
          window.toast('HERMUS answered — see the conversation on Overview','info');
        }
      }
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
    input.value=''; missionReset(); state.busy=true; state.lastResponse=null;
    state.commandInFlight=command.length>48 ? command.slice(0,48)+'…' : command;
    setState('WORKING','processing your request');
    addMessage('user', command);
    addEvent(`Command · ${command}`);
    // The command bar speaks natural language, so it must start a real run:
    // POST /jobs (kind=runtime.turn) returns a run_id and streams the answer
    // on /stream/run/{run_id}. The old primary — POST /api/v1/commands — is
    // the console-command ledger: it recorded {ok:true, command_id:null} and
    // started nothing, which is why requests vanished without a word.
    try {
      const result=await api('/jobs',{method:'POST',body:JSON.stringify({kind:'runtime.turn',payload:{text:command,platform:'web',mode:'chat',stream:true,session_id:await ensureSession(),user_id:'default'}})});
      const id=result.run_id || result.job_id; state.mission=id || null;
      addEvent(`Request accepted${id ? ` · ${id}` : ''}`); setState('WORKING',id ? `run ${id}` : 'request accepted');
      if(result.run_id) connectRun(result.run_id);
      else if(id) addEvent(`Queued as ${id} — no live stream for this kind`);
      else { state.busy=false; state.missionLive=false; addMessage('hermus','The request was queued but returned no run id. Open the Jobs tab to follow it.',{error:true}); }
    } catch (e) {
      state.busy=false; state.missionLive=false; state.commandInFlight='';
      addEvent(e.message,'error'); setState('ATTENTION',e.message);
      addMessage('hermus', 'Your request could not be started: '+e.message, {error:true});
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
      if(path.endsWith('/')) { $('#modalLog').textContent='Replay surface — source: '+path+' · mode: replay. Live mission state streams on the Overview board.'; return; }
      const value=await api(path);
      if(typeof value==='string'){ $('#modalLog').textContent=value; }
      else { $('#modalLog').innerHTML=window.humanSummary ? window.humanSummary(value) : '<div class="note">done — open the Systems tab for the full probe results.</div>'; }
    } catch(e) { $('#modalLog').textContent=e.message; }
  }

  $('#paletteClose')?.addEventListener('click',()=>showPalette(false));
  $('#paletteBtn')?.addEventListener('click',()=>showPalette(true));
  $('#palette')?.addEventListener('click',e=>{
    const item=e.target.closest('[data-palette-open]');
    if(item){showPalette(false);open(item.dataset.paletteOpen);}
  });

  document.addEventListener('click', e => {
    const nav=e.target.closest('[data-open]'); if(nav)open(nav.dataset.open);
    const suggestion=e.target.closest('[data-command]'); if(suggestion)sendCommand(suggestion.dataset.command);
    // Cross-panel jumps: every dead-end we render offers the next step, so
    // "Connect a model →" actually goes somewhere.
    const goto=e.target.closest('[data-goto]');
    if(goto){ if(window.selectTab) window.selectTab(goto.dataset.goto); else { const b=document.querySelector('#tabbtn-'+goto.dataset.goto); if(b) b.click(); } }
    if(e.target.closest('[data-close]')){$('#overlay').classList.remove('open');$('#overlay').setAttribute('aria-hidden','true');}
    if(e.target.closest('#send'))sendCommand();
  });
  $('#command')?.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendCommand();}});
  window.addEventListener('keydown',e=>{if(e.key==='Escape'){if($('#palette')?.classList.contains('open')){showPalette(false);}else{$('#overlay')?.classList.remove('open');$('#overlay')?.setAttribute('aria-hidden','true');}}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('#command')?.focus();}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='p'){e.preventDefault();showPalette(true);}});
  refresh(); setInterval(refresh,10000);
  transcriptRestore();

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
  window.HermusNexus = { api, sendCommand, contextualAction, actions, state,
    // Exposed for the Node test harness; not part of the page contract.
    __test: { addMessage, transcriptLoad, transcriptRestore, renderPalette, paletteMove, paletteRunActive, messageEl, attentionKey, attentionDismissed, dismissAttention, handleRunEvent } };

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
      meta.textContent='No compatible deployments for this role yet.';
      fleet.innerHTML='<div class="empty-event">No model is reachable right now. Add an API key (Agents tab → API keys) or start a local runtime, then refresh.<br>'
        +'<button type="button" class="btn ghost" data-goto="systems">Show me how →</button></div>';
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
      if(fleet) fleet.innerHTML='<div class="model-state unavailable"><span>RUNTIME UNAVAILABLE</span><small>The model hub could not be reached. It usually comes back on its own; if not, check the gateway process or open Systems → Local engine.</small><button type="button" class="btn ghost" data-goto="systems">Open Systems →</button></div>';
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

  /* Attention dismissal is per-browser and keyed on title+detail so a real
   * new problem (different text) always resurfaces. */
  const ATTENTION_DISMISS_KEY='hermus_attention_dismissed_v1';
  function attentionKey(item){
    return String((item&&item.title)||'')+'|'+String((item&&item.detail)||'');
  }
  function attentionDismissed(){
    try{ const raw=JSON.parse(localStorage.getItem(ATTENTION_DISMISS_KEY)||'[]'); return Array.isArray(raw)?raw.slice(-50):[]; }catch{ return []; }
  }
  function dismissAttention(key){
    const rows=attentionDismissed().filter(k=>k!==key); rows.push(key);
    try{ localStorage.setItem(ATTENTION_DISMISS_KEY, JSON.stringify(rows.slice(-50))); }catch{/* ignore */}
  }

  function renderKernel(kernel){
    if(!kernel) return;
    state.kernel=kernel;
    const summary=kernel.summary || {};
    // Single-writer rule: while a user command is in flight the run stream
    // owns the hero chip. The kernel poller only writes when nothing is live,
    // otherwise the label said THINKING while the line under it said "Idle.".
    if(!state.missionLive && !state.busy) setState(String(summary.state || 'idle').replace(/_/g,' ').toUpperCase(), String(summary.detail || 'ready'));

    const active=Number(summary.active_runs || 0);
    const attentionCount=Number(summary.attention_count || 0);
    const health=$('#healthValue');
    if(health) health.textContent = active
      ? (active + (active===1 ? ' active task' : ' active tasks'))
      : (attentionCount ? (attentionCount + (attentionCount===1 ? ' attention item' : ' attention items')) : 'READY');

    const host=$('#attention');
    if(host){
      /* Attention items must be actionable: "Diagnose" jumps to the Systems
       * console, "Dismiss" hides the item for this browser (persisted). A
       * degraded gateway with an empty kernel attention list still earns a
       * pointer to the Doctor, instead of claiming "All systems ready"
       * under a DEGRADED badge. */
      const dismissed=attentionDismissed();
      const raw=Array.isArray(kernel.attention) ? kernel.attention.slice(0,8) : [];
      const rows=raw.filter(item=>!dismissed.includes(attentionKey(item)));
      host.innerHTML=rows.length
        ? rows.map(item=>{
            const sev=String(item.severity||'').toLowerCase();
            const cls=sev==='critical' ? 'bad' : (sev==='high'||sev==='medium' ? 'warn' : '');
            return '<div class="attention-item '+cls+'" data-attention-key="'+esc(attentionKey(item))+'"><i></i><span><strong>'+esc(item.title||'Attention')+'</strong><small>'+esc(item.detail||'')+'</small>'
              +'<span class="att-actions"><button type="button" class="btn ghost" data-goto="systems">Diagnose</button>'
              +'<button type="button" class="btn ghost" data-attention-dismiss="'+esc(attentionKey(item))+'">Dismiss</button></span></span></div>';
          }).join('')
        : (state.lastHealthHealthy===false
          ? '<div class="attention-item warn"><i></i><span><strong>No subsystem reports ready</strong><small>The gateway is up, but the health probe found nothing healthy. Run the Doctor to find out why.</small><span class="att-actions"><button type="button" class="btn ghost" data-goto="systems">Diagnose</button></span></span></div>'
          : '<div class="empty-event">'+esc(String((kernel.summary||{}).headline||'All systems ready'))+'</div>');
      host.querySelectorAll('button[data-attention-dismiss]').forEach(btn=>btn.addEventListener('click',()=>{
        dismissAttention(btn.dataset.attentionDismiss);
        const sel='[data-attention-key="'+(window.CSS&&CSS.escape ? CSS.escape(btn.dataset.attentionDismiss) : btn.dataset.attentionDismiss.replace(/"/g,'\\"'))+'"]';
        const item=host.querySelector(sel);
        if(item) item.remove();
        const status=$('#attentionStatus');
        if(status) status.textContent=host.querySelector('.attention-item') ? status.textContent : 'CLEAR';
      }));
    }

    const status=$('#attentionStatus');
    const visibleAttention=host ? host.querySelectorAll('.attention-item').length : attentionCount;
    if(status) status.textContent=visibleAttention ? (visibleAttention+' ACTIVE') : 'CLEAR';

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
    if(open){ paletteActive=0; renderPalette(); setTimeout(()=>$('#paletteInput')?.focus(),0); }
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
  /* A command palette is a keyboard instrument first: arrow keys move the
   * active row, Enter runs it, and the digit hints (1–9) are real shortcuts,
   * not decoration. */
  let paletteActive=0;
  function paletteItemsNow(){ return Array.from(document.querySelectorAll('#paletteList .palette-item')); }
  function paletteMarkActive(){
    const items=paletteItemsNow();
    if(paletteActive>=items.length) paletteActive=Math.max(0,items.length-1);
    items.forEach((el,i)=>el.classList.toggle('active',i===paletteActive));
    if(items[paletteActive] && items[paletteActive].scrollIntoView) items[paletteActive].scrollIntoView({block:'nearest'});
  }
  function paletteMove(delta){
    const items=paletteItemsNow(); if(!items.length) return;
    paletteActive=(paletteActive+delta+items.length)%items.length;
    paletteMarkActive();
  }
  function paletteRunActive(){
    const items=paletteItemsNow();
    const it=items[paletteActive]||items[0];
    if(it) it.click();
  }
  function renderPalette(){
    const input=$('#paletteInput'); const host=$('#paletteList'); if(!host)return;
    const q=String(input?.value||'').toLowerCase();
    const items=PALETTE_ITEMS.filter(([name,desc])=>(name+' '+desc).toLowerCase().includes(q));
    host.innerHTML=items.length?items.map(([name,desc],i)=>'<button class="palette-item'+(i===paletteActive?' active':'')+'" data-palette-open="'+esc(name)+'"><b>'+esc(name)+'</b><span>'+esc(desc)+'</span><kbd>'+String(i+1)+'</kbd></button>').join(''):'<div class="empty-event">No HERMUS surface found.</div>';
  }
  $('#paletteInput')?.addEventListener('keydown',e=>{
    if(e.key==='ArrowDown'){ e.preventDefault(); paletteMove(1); }
    else if(e.key==='ArrowUp'){ e.preventDefault(); paletteMove(-1); }
    else if(e.key==='Enter'){ e.preventDefault(); paletteRunActive(); }
    else if(/^[1-9]$/.test(e.key)){
      const it=paletteItemsNow()[Number(e.key)-1];
      if(it){ e.preventDefault(); it.click(); }
    }
  });
  $('#paletteInput')?.addEventListener('input',()=>{ paletteActive=0; renderPalette(); });

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
        $('#modalLog').innerHTML=window.humanSummary ? window.humanSummary(data) : '<div class="note">loaded — details render on the matching tab.</div>';
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
      const cat=(modelState.catalog||{});
      const models=Array.isArray(cat.models)?cat.models:[];
      const sel=(modelState.selected||{}).selections||{};
      const selKeys=Object.keys(sel);
      const rows=[];
      rows.push('<div class="kv"><span class="k">discovered models</span><span class="v">'+esc(String(cat.count||models.length))+'</span></div>');
      rows.push('<div class="kv"><span class="k">selected</span><span class="v">'+esc(selKeys.length?selKeys.map(k=>k+': '+sel[k]).join(' · '):'auto — best available')+'</span></div>');
      if(!models.length){
        rows.push('<div class="note">No deployments discovered. Add an API key (Agents tab → API keys) or start a local runtime, then refresh the Model fleet panel.</div>');
      } else {
        models.slice(0,8).forEach(m=>rows.push('<div class="kv"><span class="k">'+esc(String(m.provider||'?')+'/'+String(m.id||m.ref||'?'))+'</span><span class="v">'+esc(String(m.source||'runtime'))+'</span></div>'));
        if(models.length>8){ rows.push('<div class="note">+'+(models.length-8)+' more in the catalog.</div>'); }
      }
      $('#modalTitle').textContent='Models';
      $('#modalLog').innerHTML=rows.join('');
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
      state.busy=true; state.lastResponse=null;
      state.commandInFlight='voice request';
      addMessage('user','🎙 voice request');
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

  function humanWorldFact(v){
    if(v==null) return 'unknown';
    if(typeof v==='string') return v;
    if(Array.isArray(v)){ return v.length ? v.slice(0,4).map(x=>String((x&&(x.path||x.file||x.name))||x)).join(', ')+(v.length>4?' +'+(v.length-4)+' more':'') : 'clean'; }
    if(typeof v==='object'){ const s=Object.entries(v).slice(0,3).map(([k,val])=>k+': '+(Array.isArray(val)?val.length+' item(s)':String(val))).join(' · '); return s||'none'; }
    return String(v);
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
      ['GIT STATUS',humanWorldFact(world['workspace.git.status']?.value)],
      ['BROWSER',world['browser.state']?.value ? humanWorldFact(world['browser.state'].value):'not connected'],
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

  // Three clicks on the HERMUS mark opens the second JARVIS workspace.
  function setJarvisWorkspace(open){
    const host=$('#jarvisWorkspace');
    if(!host)return;
    host.classList.toggle('open',!!open);
    host.setAttribute('aria-hidden',open?'false':'true');
    document.body.classList.toggle('jarvis-workspace-mode',!!open);
    if(open) $('#jwCommand')?.focus();
  }
  let logoClicks=0, logoTimer=null;
  $('#hermusLogo')?.addEventListener('click',e=>{
    e.preventDefault();
    logoClicks++;
    clearTimeout(logoTimer);
    logoTimer=setTimeout(()=>{logoClicks=0;},1300);
    if(logoClicks>=3){logoClicks=0;setJarvisWorkspace(true);}
  });
  document.addEventListener('click',e=>{
    if(e.target.closest('[data-jarvis-close]'))setJarvisWorkspace(false);
    if(e.target.closest('[data-deep-workshop]')){setJarvisWorkspace(false);setWorkshop(true);}
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
      $('#modalLog').innerHTML=window.humanSummary ? window.humanSummary(mission) : '<div class="note">mission '+esc(state.mission)+' — live state is on the Missions tab.</div>';
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
