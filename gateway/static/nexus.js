(() => {
  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const token = new URLSearchParams(location.search).get('token') || localStorage.getItem('hermus_gateway_token') || '';
  if (token) localStorage.setItem('hermus_gateway_token', token);
  async function api(path, options={}) {
    const headers = new Headers(options.headers || {});
    headers.set('Accept','application/json');
    if (token) headers.set('X-Hermus-Token', token);
    if (options.body && !headers.has('Content-Type')) headers.set('Content-Type','application/json');
    const r = await fetch(path, {...options, headers});
    const text = await r.text(); let data={}; try { data=text?JSON.parse(text):{} } catch { data={message:text}; }
    if (!r.ok) throw new Error(data.message || data.detail || `HTTP ${r.status}`);
    return data;
  }
  const addEvent = (text, kind='info') => {
    const host=$('#activity'); if(!host)return;
    const row=document.createElement('div'); row.className='event';
    row.innerHTML=`<i></i><span>${esc(text)}</span><time>${new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</time>`;
    host.prepend(row); while(host.children.length>6)host.lastElementChild.remove();
    if(kind==='error') row.querySelector('i').style.background='var(--bad)';
  };
  function setState(label, detail='') { $('#coreState').textContent=label; $('#coreDetail').textContent=detail; }
  async function refresh() {
    try {
      const [health, caps] = await Promise.all([api('/api/v1/system/health'), api('/api/v1/system/capabilities')]);
      $('#stateLabel').textContent='ONLINE'; $('#stateDot').className='state-dot';
      const services = health.services || health.checks || health;
      const count = typeof services === 'object' ? Object.values(services).filter(v => v && (v.status==='ok'||v.ready===true||v.healthy===true)).length : 0;
      $('#healthValue').textContent = count ? `${count}` : 'READY';
      const entries = caps.capabilities || caps;
      const list=$('#capabilities'); list.innerHTML='';
      Object.entries(entries && typeof entries==='object'?entries:{}).slice(0,8).forEach(([name,value])=>{
        const ok=typeof value==='object' ? (value.available ?? value.ready ?? value.ok) : !!value;
        const row=document.createElement('div'); row.className='cap'; row.innerHTML=`<span>${esc(name.replaceAll('_',' '))}</span><b style="color:${ok?'var(--good)':'var(--muted)'}">${ok?'READY':'UNAVAILABLE'}</b>`; list.appendChild(row);
      });
      setState('READY','awaiting your command');
    } catch (e) {
      $('#stateLabel').textContent='OFFLINE'; $('#stateDot').className='state-dot bad'; $('#healthValue').textContent='—';
      setState('DISCONNECTED','gateway unavailable'); addEvent(e.message,'error');
    }
  }
  async function sendCommand() {
    const input=$('#command'); const value=input.value.trim(); if(!value)return;
    input.value=''; setState('WORKING','executing command'); addEvent(`Command: ${value}`);
    try {
      const result=await api('/api/v1/commands',{method:'POST',body:JSON.stringify({command:value,args:{},source:'nexus'})});
      addEvent(`Command accepted${result.command_id?` · ${result.command_id}`:''}`); setState('READY','command accepted');
    } catch(e) { addEvent(e.message,'error'); setState('ATTENTION',e.message); }
  }
  function open(name) {
    $('#modalTitle').textContent=name;
    $('#modalLog').textContent='Loading…'; $('#overlay').classList.add('open');
    const paths={
      'System health':'/api/v1/system/health',
      'Capabilities':'/api/v1/system/capabilities',
      'Dashboard state':'/api/v1/dashboard/state',
      'Console manifest':'/api/v1/console/manifest'
    };
    const path=paths[name]; if(!path){$('#modalLog').textContent='No live projection registered for this surface yet.';return;}
    api(path).then(v=>$('#modalLog').textContent=JSON.stringify(v,null,2)).catch(e=>$('#modalLog').textContent=e.message);
  }
  document.addEventListener('click', e => {
    const nav=e.target.closest('[data-open]'); if(nav) open(nav.dataset.open);
    if(e.target.closest('[data-close]')) $('#overlay').classList.remove('open');
    if(e.target.closest('#send')) sendCommand();
  });
  $('#command')?.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendCommand()}});
  window.addEventListener('keydown',e=>{if(e.key==='Escape')$('#overlay')?.classList.remove('open');if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('#command')?.focus()}});
  refresh(); setInterval(refresh,15000);
})();
