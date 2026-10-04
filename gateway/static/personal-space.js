(() => {
  const $ = (s) => document.querySelector(s);
  const token = new URLSearchParams(location.search).get('token') || localStorage.getItem('hermus_gateway_token') || '';

  async function api(path, options={}) {
    const headers = new Headers(options.headers || {});
    headers.set('Accept','application/json');
    if(token) headers.set('X-Hermus-Token', token);
    if(options.body && !headers.has('Content-Type')) headers.set('Content-Type','application/json');
    const r=await fetch(path,{...options,headers});
    const text=await r.text(); let data={};
    try{ data=text?JSON.parse(text):{} }catch{ data={message:text}; }
    if(!r.ok) throw new Error(data.message||data.detail||data.error||('HTTP '+r.status));
    return data;
  }

  const esc=(v)=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let snapshot=null;

  function render(data){
    snapshot=data;
    const status=$('#personalSpaceStatus');
    const headline=$('#personalSpaceHeadline');
    const detail=$('#personalSpaceDetail');
    const proposal=$('#personalSpaceProposal');
    if(status) status.textContent=String(data.status||'quiet').replace(/_/g,' ').toUpperCase();
    if(headline){
      headline.textContent=data.status==='proposal_ready'
        ? 'HERMUS found something worth showing you.'
        : data.status==='curious'
          ? 'HERMUS is exploring a thought.'
          : data.status==='waiting'
            ? 'HERMUS is giving you space.'
            : data.status==='daily_cap'
              ? 'HERMUS has finished today’s curiosity budget.'
              : data.status==='resting'
                ? 'HERMUS is keeping quiet.'
                : 'HERMUS has its own time.';
    }
    if(detail){
      const idle=Math.floor(Number(data.idle_for_seconds||0)/60);
      detail.textContent=data.status==='proposal_ready'
        ? String(data.pending_proposals||1)+' proposal pending review'
        : data.status==='curious'
          ? 'Thinking privately; no action is being taken.'
          : data.status==='daily_cap'
            ? 'No more background curiosity cycles today.'
            : idle+' min since the last user activity';
    }
    if(proposal){
      const p=(data.proposals||[]).find(x=>x.status==='pending');
      proposal.innerHTML=p
        ? '<strong>'+esc(p.title)+'</strong><small>'+esc(p.summary||'')+'</small>'
        : '<span>No proposal waiting for you.</span>';
    }
  }

  async function refresh(){
    try{ render(await api('/personal-space?limit=6')); }
    catch(e){
      const detail=$('#personalSpaceDetail'); if(detail) detail.textContent='Personal Space unavailable: '+e.message;
    }
  }

  function openSpace(){
    if(!snapshot) return refresh();
    $('#modalTitle').textContent='Personal Space';
    const p=(snapshot.proposals||[]).find(x=>x.status==='pending');
    const lines=[
      'STATUS: '+String(snapshot.status||'').toUpperCase(),
      'IDLE: '+Math.floor(Number(snapshot.idle_for_seconds||0)/60)+' min',
      'TODAY: '+String(snapshot.daily_cycles||0)+' / '+String(snapshot.daily_cap||0)+' cycles',
      '',
      p ? 'PROPOSAL' : 'NO PENDING PROPOSAL',
      p ? [
        'Title: '+p.title,
        'Category: '+p.category,
        'Confidence: '+p.confidence,
        '',
        p.summary||'',
        '',
        'Why: '+(p.why||''),
        '',
        'Next: '+(p.next_action||'')
      ].join('\n') : 'HERMUS is quietly waiting or exploring.'
    ];
    $('#modalLog').textContent=lines.join('\n');
    const modal=$('.modal');
    let actions=$('#personalSpaceActions');
    if(!actions){
      actions=document.createElement('div'); actions.id='personalSpaceActions'; actions.className='personal-space-actions';
      $('#modalLog').after(actions);
    }
    actions.innerHTML='';
    const think=document.createElement('button'); think.textContent='THINK NOW'; think.type='button'; think.className='personal-space-action';
    think.onclick=async()=>{ think.disabled=true; try{ await api('/personal-space/run',{method:'POST'}); await refresh(); openSpace(); }catch(e){ $('#modalLog').textContent='Personal Space error: '+e.message; } finally{ think.disabled=false; } };
    actions.appendChild(think);
    if(p){
      const approve=document.createElement('button'); approve.textContent='APPROVE'; approve.type='button'; approve.className='personal-space-action primary';
      approve.onclick=async()=>{ approve.disabled=true; try{ await api('/personal-space/proposals/'+encodeURIComponent(p.id)+'/approve',{method:'POST'}); await refresh(); openSpace(); }catch(e){ $('#modalLog').textContent='Approval failed: '+e.message; } finally{ approve.disabled=false; } };
      const dismiss=document.createElement('button'); dismiss.textContent='DISMISS'; dismiss.type='button'; dismiss.className='personal-space-action';
      dismiss.onclick=async()=>{ dismiss.disabled=true; try{ await api('/personal-space/proposals/'+encodeURIComponent(p.id)+'/dismiss',{method:'POST'}); await refresh(); openSpace(); }catch(e){ $('#modalLog').textContent='Dismiss failed: '+e.message; } finally{ dismiss.disabled=false; } };
      actions.appendChild(approve); actions.appendChild(dismiss);
    }
    $('#overlay').classList.add('open'); $('#overlay').setAttribute('aria-hidden','false');
  }

  document.addEventListener('click',e=>{
    if(e.target.closest('#personalSpaceOpen') || e.target.closest('#personalSpaceOpenDock')) openSpace();
  });
  refresh();
  setInterval(refresh,15000);

  window.HermusPersonalSpace={refresh,open:openSpace,api,get snapshot(){return snapshot;}};
})();
