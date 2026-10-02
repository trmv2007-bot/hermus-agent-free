(() => {
  const $ = (s) => document.querySelector(s);
  const token = new URLSearchParams(location.search).get('token') || localStorage.getItem('hermus_gateway_token') || '';
  if (token) localStorage.setItem('hermus_gateway_token', token);
  const state = { online:false, busy:false, mission:null };

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
      $('#healthValue').textContent = healthy ? `${healthy} systems ready` : 'READY';
      renderCapabilities(caps.capabilities || caps);
      if (!state.busy) setState('READY','awaiting your command');
      document.body.style.setProperty('--rtt', `${Math.round(performance.now()-started)}ms`);
    } catch (e) {
      state.online=false; $('#stateLabel').textContent='OFFLINE'; $('#stateDot').className='state-dot bad'; $('#healthValue').textContent='OFFLINE';
      setState('DISCONNECTED','gateway unavailable'); addEvent(e.message,'error');
    }
  }

  async function sendCommand(value=null) {
    const input=$('#command'); const command=(value ?? input.value).trim(); if(!command)return;
    input.value=''; state.busy=true; setState('WORKING','processing your request'); addEvent(`Command · ${command}`);
    try {
      // HERMUS command transport. The UI does not own mission truth.
      const result=await api('/api/v1/commands',{method:'POST',body:JSON.stringify({command,text:command,platform:'web',mode:'chat',stream:true})});
      const id=result.run_id || result.job_id || result.mission_id; state.mission=id || null;
      addEvent(`Request accepted${id ? ` · ${id}` : ''}`); setState('WORKING',id ? `run ${id}` : 'request accepted');
    } catch(e) {
      // Compatibility fallback for older gateways during migration.
      try {
        const result=await api('/jobs',{method:'POST',body:JSON.stringify({kind:'runtime.turn',payload:{text:command,platform:'web',mode:'chat',stream:true}})});
        const id=result.run_id || result.job_id; state.mission=id || null; addEvent(`Request accepted${id ? ` · ${id}` : ''}`); setState('WORKING',id ? `run ${id}` : 'request accepted');
      } catch (fallback) { addEvent(fallback.message,'error'); setState('ATTENTION',fallback.message); }
    } finally { state.busy=false; }
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
  };

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

  document.addEventListener('click', e => {
    const nav=e.target.closest('[data-open]'); if(nav)open(nav.dataset.open);
    const suggestion=e.target.closest('[data-command]'); if(suggestion)sendCommand(suggestion.dataset.command);
    if(e.target.closest('[data-close]')){$('#overlay').classList.remove('open');$('#overlay').setAttribute('aria-hidden','true');}
    if(e.target.closest('#send'))sendCommand();
  });
  $('#command')?.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendCommand();}});
  window.addEventListener('keydown',e=>{if(e.key==='Escape'){$('#overlay')?.classList.remove('open');$('#overlay')?.setAttribute('aria-hidden','true');}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('#command')?.focus();}});
  refresh(); setInterval(refresh,10000);

  // Discoverable capability vocabulary for contextual HERMUS surfaces:
  // Snapshot, Replay, Emergency stop, Missions, approval-aware lifecycle,
  // Pre-flight mission, Start mission if ready, Pre-flight autonomy check,
  // Record planning-mode blocker, Approval bundles, approve all, deny all,
  // Jarvis Safety Core, pendingCount, blockedMissionCount, Safety Event Timeline,
  // Generate safety report, Capability readiness / activation registry,
  // Record power, Propose setup, Run approved Downloads scan,
  // Start Downloads scan mission, List scan reports, and Doctor/Computer controls.
  window.HermusNexus = { api, sendCommand, contextualAction, actions, state };
})();
