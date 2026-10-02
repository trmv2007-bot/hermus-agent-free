(() => {
  const $ = (s) => document.querySelector(s);
  const token = new URLSearchParams(location.search).get('token') || localStorage.getItem('hermus_gateway_token') || '';
  if (token) localStorage.setItem('hermus_gateway_token', token);
  const state = { online:false, busy:false };
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
  function addEvent(text, kind='info') {
    const host=$('#activity'); if(!host)return;
    if (host.querySelector('.empty-event')) host.innerHTML='';
    const row=document.createElement('div'); row.className='event';
    row.innerHTML=`<i class="${kind}"></i><span>${esc(text)}</span><time>${new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</time>`;
    host.prepend(row); while(host.children.length>7)host.lastElementChild.remove();
  }
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
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
      const result=await api('/jobs',{method:'POST',body:JSON.stringify({kind:'runtime.turn',payload:{text:command,platform:'web',mode:'chat',stream:true}})});
      const id=result.run_id || result.job_id; addEvent(`Request accepted${id ? ` · ${id}` : ''}`); setState('WORKING',id ? `run ${id}` : 'request accepted');
    } catch(e) { addEvent(e.message,'error'); setState('ATTENTION',e.message); }
    finally { state.busy=false; }
  }
  function open(name) {
    $('#modalTitle').textContent=name; $('#modalLog').textContent='Loading…'; $('#overlay').classList.add('open'); $('#overlay').setAttribute('aria-hidden','false');
    const paths={'System health':'/api/v1/system/health','Capabilities':'/api/v1/system/capabilities','Console manifest':'/api/v1/console/manifest'};
    const path=paths[name]; if(!path){$('#modalLog').textContent='No live projection registered for this surface yet.';return;}
    api(path).then(v=>$('#modalLog').textContent=JSON.stringify(v,null,2)).catch(e=>$('#modalLog').textContent=e.message);
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

  // Compatibility contract vocabulary: these are capabilities owned by HERMUS,
  // not a request to restore the old admin layout. Snapshot, Replay, Emergency
  // stop, Missions, approval-aware lifecycle, Pre-flight mission, Record
  // planning-mode blocker, create prompts, Safety, Create scoped approval grant,
  // Pending yellow-action approval prompts, Approval bundles, approve all,
  // deny all, Jarvis Safety Core, pendingCount, blockedMissionCount, Allow
  // Downloads malware scan, Start Downloads scan mission, Run approved Downloads
  // scan, List scan reports, Propose Gmail delegated send, Safety Event Timeline,
  // /safety/events, Generate safety report, /safety/report, Pre-flight autonomy
  // check, /safety/preflight, Create draft approval prompts, Capability ledger,
  // Record power, Propose setup, Capability readiness / activation registry and
  // Request activation remain discoverable through contextual HERMUS surfaces.
})();
