(() => {
  const qs = (s, r = document) => r.querySelector(s);
  const qsa = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
  const token = new URLSearchParams(location.search).get('token')
    || localStorage.getItem('hermus_gateway_token') || '';
  if (token) localStorage.setItem('hermus_gateway_token', token);

  let savedSettings = {};
  try { savedSettings = JSON.parse(localStorage.getItem('hermus_dashboard_settings') || '{}') || {}; } catch {}

  const state = {
    view: 'overview',
    settingsTab: 'general',
    online: false,
    modelCatalog: [],
    selectedModels: {},
    agents: [],
    projects: [],
    project: null,
    file: null,
    settings: savedSettings,
    settingsData: {},
    messages: [{who:'jarvis', text:'JARVIS is ready. Tell HERMUS what you want to accomplish.', time:'Now'}],
    activeRun: null,
    voice: null,
    paletteFocus: null,
    runPollers: new Map()
  };

  const views = {
    overview:['Overview','Your operational center. See what matters, what is running, and where to act.'],
    chat:['Chat','Talk to HERMUS directly and keep every request tied to a tracked run.'],
    build:['Workshop','Build, inspect and edit workspace files with HERMUS context beside you.'],
    agents:['Agents','Manage persistent specialists and delegate work deliberately.'],
    models:['Models','Route roles to runtime-discovered deployments and inspect capabilities.'],
    missions:['Missions','Start recoverable work, review preflight and manage proactive routines.'],
    computer:['Computer','Operate the desktop safely with planning, execution and emergency controls.'],
    tools:['Tools','Inspect the capability fabric and open the real workspace for a task.'],
    knowledge:['Knowledge','Review retained learning and save durable project knowledge.'],
    personal:['Personal Space','Bounded curiosity turns useful discoveries into proposals for you.'],
    browser:['Browser','Bring public web context into HERMUS through the browser boundary.'],
    terminal:['Terminal','Send terminal work through the agent runtime and follow execution.'],
    image:['Image Lab','Submit image work through the HERMUS runtime and track it as a run.'],
    video:['Video Studio','Describe motion work and follow the generated task.'],
    space:['Space','A spatial navigator for the real HERMUS workspaces.'],
    settings:['Control Center','Inspect and operate real HERMUS configuration and runtime state.']
  };

  const settingTabs = [
    ['general','General'],['chat','Chat'],['models','Models'],['providers','Providers & Keys'],['agents','Agents'],
    ['safety','Safety & Approvals'],['voice','Voice'],['computer','Computer'],['memory','Memory'],
    ['workspace','Workspace'],['integrations','Integrations'],['runtime','Runtime & Doctor'],['updates','Updates']
  ];

  const setupSteps = [
    {id:'welcome',label:'Welcome'},
    {id:'check',label:'System check'},
    {id:'provider',label:'AI provider'},
    {id:'models',label:'Model routing'},
    {id:'workspace',label:'Workspace'},
    {id:'capabilities',label:'Capabilities'},
    {id:'finish',label:'Finish'}
  ];
  const wizard = {step:0, data:{}, focus:null};

  function openWizard(start=0) {
    wizard.step=Math.max(0,Math.min(setupSteps.length-1,start));
    wizard.focus=document.activeElement;
    const el=qs('#setupWizard'); if(!el)return;
    el.classList.add('open'); el.setAttribute('aria-hidden','false');
    renderWizard();
    setTimeout(()=>qs('[data-wizard-next]')?.focus(),0);
  }
  function closeWizard(completed=false) {
    const el=qs('#setupWizard'); if(!el)return;
    if(completed) localStorage.setItem('hermus_setup_complete','1');
    el.classList.remove('open'); el.setAttribute('aria-hidden','true');
    try{wizard.focus?.focus()}catch{}
  }
  function wizardStatuses(check) {
    const rows=[
      ['Gateway',!failed(check.ready),'HERMUS gateway readiness'],
      ['Core health',!failed(check.health),'capability health'],
      ['AI providers',!failed(check.providers)&&Array.isArray(check.providers?.providers)&&check.providers.providers.some(p=>p.usable||p.available||p.configured),'at least one usable provider'],
      ['Models',!failed(check.models)&&Array.isArray(check.models?.models)&&check.models.models.length>0,'runtime-discovered models'],
      ['Workspace',!failed(check.workspace)&&Array.isArray(check.workspace?.projects)&&check.workspace.projects.length>0,'at least one project'],
      ['Voice',!failed(check.voice),'speech backend status'],
      ['Computer',!failed(check.computer),'desktop-control subsystem status']
    ];
    return rows;
  }
  async function wizardCheck() {
    const endpoints=[
      ['ready','/readyz'],['health','/api/v1/system/health'],['providers','/providers/available'],
      ['models','/models/catalog?probe=true&refresh=true'],['workspace','/workspace'],['voice','/speech/status'],['computer','/computer/status']
    ];
    const vals=await Promise.all(endpoints.map(async([k,u])=>[k,await safe(u)]));
    wizard.data.check=Object.fromEntries(vals);
  }
  function wizardProgress() {
    const host=qs('#wizardProgress');if(!host)return;
    host.innerHTML=setupSteps.map((s,i)=>'<div class="wizard-step '+(i<wizard.step?'done ':'')+(i===wizard.step?'active':'')+'"><span>'+(
      i<wizard.step?'✓':String(i+1)
    )+'</span><small>'+esc(s.label)+'</small></div>').join('');
  }
  function wizardStatusList(rows) {
    return '<div class="wizard-status-list">'+rows.map(r=>'<div class="wizard-status"><div><strong>'+esc(r[0])+'</strong><small>'+esc(r[2])+'</small></div>'+badge(r[1]?'READY':'NEEDS ATTENTION',r[1]?'good':'warn')+'</div>').join('')+'</div>';
  }
  function renderWizard() {
    const body=qs('#wizardBody'),next=qs('[data-wizard-next]'),back=qs('[data-wizard-back]'),later=qs('[data-wizard-later]');
    if(!body)return;
    wizardProgress();
    back.disabled=wizard.step===0;
    const s=setupSteps[wizard.step];
    let html='';
    if(s.id==='welcome'){
      html='<div class="wizard-hero"><div class="wizard-orb"><i></i></div><div><div class="eyebrow">ONE GUIDED SETUP</div><h3>We configure the pieces. You stay in control.</h3><p>HERMUS will inspect what is already working, avoid asking for values it can discover itself, and only stop when a choice or credential actually needs you.</p></div></div>'+
      '<div class="wizard-choice-grid"><div class="wizard-choice active"><strong>Safe automatic setup</strong><span>Discover existing providers, models and local resources. Apply only low-risk configuration.</span></div><div class="wizard-choice"><strong>Manual control</strong><span>Use the Control Center directly when you already know how you want HERMUS configured.</span></div></div>';
      next.textContent='CHECK MY SYSTEM';
    } else if(s.id==='check'){
      const check=wizard.data.check||{};
      const rows=wizardStatuses(check);
      html='<div class="wizard-section-head"><div><div class="eyebrow">STEP 2</div><h3>What is already ready?</h3><p>HERMUS checks the live runtime before making setup decisions.</p></div><button class="btn" type="button" data-wizard-recheck>RECHECK</button></div>'+
      (rows.length?wizardStatusList(rows):'<div class="empty">Checking…</div>')+
      '<div class="wizard-note">Nothing is installed or changed by this check.</div>';
      next.textContent=(rows.filter(r=>!r[1]&&['Gateway','Core health'].includes(r[0])).length)?'FIX CORE FIRST':'CONFIGURE AI';
    } else if(s.id==='provider'){
      const p=wizard.data.check?.providers;
      const providers=Array.isArray(p?.providers)?p.providers:[];
      html='<div class="wizard-section-head"><div><div class="eyebrow">STEP 3</div><h3>Choose where HERMUS gets intelligence.</h3><p>Use an existing local provider, connect an API, or discover free options.</p></div><button class="btn primary" type="button" data-wizard-free>DISCOVER FREE</button></div>'+
      '<div class="wizard-provider-list">'+(providers.length?providers.map(x=>{
        const ready=!!(x.usable??x.available??x.configured??x.ok);
        return '<div class="wizard-provider"><div><strong>'+esc(x.name||x.id||x.provider||'Provider')+'</strong><small>'+esc(x.base_url||x.reason||x.detail||'Provider resolver')+'</small></div>'+badge(ready?'USABLE':'SETUP',ready?'good':'warn')+'</div>';
      }).join(''):'<div class="empty">No provider information returned yet.</div>')+'</div>'+
      '<div class="wizard-form-grid"><label class="field">PROVIDER<input id="wizardKeyProvider" placeholder="ollama"></label><label class="field">API KEY<input id="wizardKeyValue" type="password" placeholder="Only needed for API providers"></label><label class="field">BASE URL<input id="wizardKeyBaseUrl" placeholder="http://127.0.0.1:11434"></label><label class="field">DEFAULT MODEL<input id="wizardKeyModel" placeholder="optional"></label></div>'+
      '<div class="actions"><button class="btn primary" type="button" data-wizard-add-provider>USE / ADD PROVIDER</button></div><div id="wizardProviderResult" class="result">HERMUS will not display stored credentials.</div>';
      next.textContent='MODEL ROUTING';
    } else if(s.id==='models'){
      const cat=wizard.data.models||wizard.data.check?.models;
      const models=Array.isArray(cat?.models)?cat.models:[];
      const selected=state.selectedModels||{};
      html='<div class="wizard-section-head"><div><div class="eyebrow">STEP 4</div><h3>Pick the brain for each kind of work.</h3><p>Auto is safe when you are unsure; explicit choices are persisted per role.</p></div><button class="btn" type="button" data-wizard-model-refresh>SYNC MODELS</button></div>'+
      '<div class="wizard-model-grid">'+['default','reasoning','coding','vision','background'].map(role=>{
        const cur=selected[role]||'auto';
        return '<label class="field"><span>'+esc(role.toUpperCase())+'</span><select data-wizard-role="'+esc(role)+'"><option value="auto">AUTO · best available</option>'+
          models.map(m=>'<option value="'+esc(m.ref)+'" '+(m.ref===cur?'selected':'')+'>'+esc(m.name||m.id||m.ref)+'</option>').join('')+'</select></label>';
      }).join('')+'</div>'+
      '<div class="wizard-note">'+(models.length?models.length+' live deployment(s) discovered.':'No live deployments yet. You can keep AUTO and configure a provider later.')+'</div>';
      next.textContent='WORKSPACE';
    } else if(s.id==='workspace'){
      const ws=wizard.data.check?.workspace||{};
      const projects=Array.isArray(ws.projects)?ws.projects:[];
      html='<div class="wizard-section-head"><div><div class="eyebrow">STEP 5</div><h3>Give HERMUS a place to work.</h3><p>Projects are shared context for Workshop, memory and agent tasks.</p></div></div>'+
      '<div class="wizard-current-workspace">'+
      '<div><strong>Current</strong><span>'+esc(ws.current||'No project selected')+'</span></div><div><strong>Root</strong><span>'+esc(ws.base_dir||'Not reported')+'</span></div><div><strong>Projects</strong><span>'+esc(projects.length)+'</span></div></div>'+
      '<div class="wizard-form-grid"><label class="field">NEW PROJECT<input id="wizardProjectName" placeholder="my-project"></label><label class="field full">DESCRIPTION<textarea id="wizardProjectDescription" placeholder="What should this workspace contain?"></textarea></label></div>'+
      '<div class="actions"><button class="btn primary" type="button" data-wizard-create-project>CREATE PROJECT</button><button class="btn" type="button" data-view="build">OPEN WORKSHOP</button></div><div id="wizardWorkspaceResult" class="result">Existing projects are preserved.</div>';
      next.textContent='CAPABILITIES';
    } else if(s.id==='capabilities'){
      const check=wizard.data.check||{};
      const voice=check.voice,computer=check.computer;
      html='<div class="wizard-section-head"><div><div class="eyebrow">STEP 6</div><h3>Optional capabilities.</h3><p>These are discovered and tested, not silently enabled.</p></div></div>'+
      '<div class="wizard-cap-grid">'+
      '<div class="wizard-cap"><div><strong>VOICE</strong><small>'+esc(valueSummary(voice?.status||voice?.backend||'unknown'))+'</small></div><button class="btn" type="button" data-wizard-voice-test>TEST</button></div>'+
      '<div class="wizard-cap"><div><strong>COMPUTER CONTROL</strong><small>'+esc(computer?.halted?'HALTED':computer?.active?'AVAILABLE':'UNAVAILABLE')+'</small></div><button class="btn" type="button" data-wizard-computer>OPEN</button></div>'+
      '<div class="wizard-cap"><div><strong>SAFETY</strong><small>Risky actions remain behind the permission system.</small></div><button class="btn" type="button" data-view="settings" data-settings-tab-target="safety">REVIEW</button></div>'+
      '<div class="wizard-cap"><div><strong>INTEGRATIONS</strong><small>MCP and plugins are controlled from the runtime.</small></div><button class="btn" type="button" data-view="settings" data-settings-tab-target="integrations">REVIEW</button></div>'+
      '</div>';
      next.textContent='FINISH';
    } else if(s.id==='finish'){
      const check=wizard.data.check||{};
      const rows=wizardStatuses(check);
      const good=rows.filter(r=>r[1]).length;
      html='<div class="wizard-finish"><div class="wizard-finish-mark">✓</div><div><div class="eyebrow">SETUP REVIEW</div><h3>HERMUS is ready to take over the rest.</h3><p>'+good+' of '+rows.length+' major subsystems are currently ready. Anything still missing is visible in Control Center, where HERMUS can keep helping you configure it.</p></div></div>'+
      '<div class="wizard-next-grid"><button class="btn primary" type="button" data-view="chat">START A TASK</button><button class="btn" type="button" data-view="settings">OPEN CONTROL CENTER</button><button class="btn" type="button" data-view="models">OPEN MODEL HUB</button><button class="btn" type="button" data-view="build">OPEN WORKSHOP</button></div>';
      next.textContent='DONE';
    }
    body.innerHTML=html;
  }
  async function wizardNext() {
    const id=setupSteps[wizard.step].id;
    if(id==='welcome'){
      await wizardCheck(); wizard.step=1; renderWizard(); return;
    }
    if(id==='check'){
      if(failed(wizard.data.check?.ready)&&failed(wizard.data.check?.health)){toast('HERMUS core is not reachable yet',true);return;}
      wizard.step=2;renderWizard();return;
    }
    if(id==='provider'){
      wizard.step=3;renderWizard();return;
    }
    if(id==='models'){
      const selects=qsa('[data-wizard-role]');
      for(const s of selects){
        try{await api('/models/select',{method:'POST',body:JSON.stringify({role:s.dataset.wizardRole,model:s.value})});state.selectedModels[s.dataset.wizardRole]=s.value;}catch(e){toast('Model '+s.dataset.wizardRole+' · '+e.message,true);}
      }
      wizard.data.models=await safe('/models/catalog'); wizard.step=4; renderWizard(); return;
    }
    if(id==='workspace'){wizard.step=5;renderWizard();return;}
    if(id==='capabilities'){await wizardCheck();wizard.step=6;renderWizard();return;}
    if(id==='finish'){closeWizard(true);toast('HERMUS setup saved');return;}
  }
  async function wizardBack(){if(wizard.step>0){wizard.step--;renderWizard();}}


  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set('Accept','application/json');
    if (token) headers.set('X-Hermus-Token', token);
    if (options.body && !headers.has('Content-Type')) headers.set('Content-Type','application/json');
    const r = await fetch(path, {...options, headers});
    const raw = await r.text();
    let data = {};
    try { data = raw ? JSON.parse(raw) : {}; } catch { data = {message: raw}; }
    if (!r.ok) throw new Error(String(data.message || data.detail || data.error || ('HTTP ' + r.status)));
    return data;
  }
  const safe = async (path, options) => {
    try { return await api(path, options); }
    catch (e) { return {__error: e.message}; }
  };
  const failed = v => !!(v && v.__error);
  const errText = v => failed(v) ? String(v.__error || 'Unavailable') : '';

  function toast(message, bad = false) {
    let n = qs('#toast');
    if (!n) {
      n = document.createElement('div');
      n.id = 'toast'; n.className = 'toast';
      document.body.appendChild(n);
    }
    n.textContent = message;
    n.classList.toggle('bad', bad);
    n.classList.add('show');
    clearTimeout(n._t);
    n._t = setTimeout(() => n.classList.remove('show'), 2600);
  }

  function setOnline(ok, label) {
    state.online = ok;
    const dot = qs('#topStatusDot'), title = qs('#topStatusLabel'), sub = qs('#topStatusSub');
    dot?.classList.toggle('off', !ok);
    if (title) title.textContent = label || (ok ? 'ONLINE' : 'OFFLINE');
    if (sub) sub.textContent = ok ? 'HERMUS CORE' : 'GATEWAY UNAVAILABLE';
  }

  function badge(text, kind = '') {
    return '<span class="badge ' + esc(kind) + '">' + esc(String(text).toUpperCase()) + '</span>';
  }
  function stat(label, value, detail) {
    return '<article class="stat"><span>'+esc(label)+'</span><b>'+esc(value)+'</b><small>'+esc(detail)+'</small></article>';
  }
  function valueSummary(v) {
    if (v === null || v === undefined || v === '') return '—';
    if (typeof v === 'boolean') return v ? 'Yes' : 'No';
    if (Array.isArray(v)) return v.length + ' items';
    if (typeof v === 'object') {
      if (v.status !== undefined) return String(v.status);
      if (v.state !== undefined) return String(v.state);
      if (v.ok !== undefined) return v.ok ? 'Ready' : 'Not ready';
      if (v.running !== undefined) return v.running ? 'Running' : 'Stopped';
      return Object.keys(v).length + ' fields';
    }
    return String(v);
  }
  function errorCard(message) {
    return '<div class="state-card error-state"><strong>Unavailable</strong><p>'+esc(message || 'The runtime did not return this section.')+'</p></div>';
  }

  function openView(key, opts = {}) {
    if (!views[key]) key = 'overview';
    state.view = key;
    if (opts.settingsTab) state.settingsTab = opts.settingsTab;
    if (opts.history !== false) history.pushState(null, '', '#'+key);
    qsa('.view').forEach(v => v.classList.toggle('active', v.id === 'view-'+key));
    qsa('.nav-item[data-view]').forEach(b => {
      const active = b.dataset.view === key;
      b.classList.toggle('active', active);
      if (active) b.setAttribute('aria-current','page'); else b.removeAttribute('aria-current');
    });
    if (opts.render !== false) loadView(key);
    window.scrollTo({top:0,behavior:'auto'});
  }

  function openSettingsTab(tab) {
    if (!settingTabs.some(x => x[0] === tab)) tab = 'general';
    state.settingsTab = tab;
    qsa('[data-settings-tab]').forEach(b => {
      const active = b.dataset.settingsTab === tab;
      b.classList.toggle('active', active);
      b.setAttribute('aria-selected', active ? 'true' : 'false');
      b.setAttribute('tabindex', active ? '0' : '-1');
    });
    renderSettingsTab(tab);
    qs('[data-settings-tab="'+tab+'"]')?.focus({preventScroll:true});
  }

  function toolProgress(tool, args = {}) {
    const name = String(tool || 'tool').trim() || 'tool';
    const a = args && typeof args === 'object' ? args : {};
    const key = name.toLowerCase();
    const pick = (...keys) => {
      for (const k of keys) {
        const v = a[k];
        if (v !== undefined && v !== null && String(v).trim()) return String(v);
      }
      return '';
    };
    let preview = pick('command','cmd','query','url','path','file','file_path','name','goal','task','text');
    if (!preview && key.includes('terminal')) preview = pick('input','script');
    preview = preview.replace(/(?:api[_-]?key|token|password|secret|credential)\s*[:=]\s*[^\s,;]+/ig, '$1=••••');
    preview = preview.replace(/\s+/g, ' ').trim();
    if (preview.length > 140) preview = preview.slice(0, 140) + '…';

    if (key.includes('terminal') || key === 'shell' || key.includes('execute')) {
      return preview ? 'Running terminal · ' + preview : 'Running terminal';
    }
    if (key.includes('search') || key.includes('web')) {
      return preview ? 'Searching the web · ' + preview : 'Searching the web';
    }
    if (key.includes('read') || key.includes('file')) {
      return preview ? 'Reading workspace · ' + preview : 'Reading workspace';
    }
    if (key.includes('write') || key.includes('edit') || key.includes('patch')) {
      return preview ? 'Editing workspace · ' + preview : 'Editing workspace';
    }
    if (key.includes('git')) {
      return preview ? 'Working with Git · ' + preview : 'Working with Git';
    }
    if (key.includes('computer') || key.includes('browser')) {
      return preview ? 'Operating computer · ' + preview : 'Operating computer';
    }
    if (key.includes('delegate') || key.includes('agent')) {
      return preview ? 'Delegating work · ' + preview : 'Delegating work';
    }
    return preview ? 'Running ' + name + ' · ' + preview : 'Running ' + name;
  }

  function summarizeRunActivity(events) {
    const items = [];
    const openTools = new Map();
    let model = '';
    let connected = false;
    let current = 'Thinking…';
    let terminalSeen = false;
    let streamedText = '';

    const add = (item) => {
      const last = items[items.length - 1];
      if (last && last.key === item.key && last.state === item.state && last.detail === item.detail) return;
      items.push(item);
    };

    (Array.isArray(events) ? events : []).forEach((e, index) => {
      const type = String(e?.type || '').toLowerCase();
      const d = e?.data && typeof e.data === 'object' ? e.data : {};
      const step = d.step ? 'Step ' + d.step : '';

      if (type === 'turn_started') {
        model = String(d.model || '');
        if (model) current = 'Connecting to ' + model;
        add({key:'turn',icon:'◌',detail:model ? 'Model · '+model : 'Connecting to model',state:'done',meta:''});
        return;
      }
      if (type === 'mission_runtime_started') {
        current = 'Planning…';
        add({key:'mission-'+index,icon:'◆',detail:'Planning the task',state:'done',meta:step});
        return;
      }
      if (type === 'step_started') {
        current = 'Thinking…' + (d.of ? ' · step '+d.step+'/'+d.of : '');
        add({key:'step-'+String(d.step||index),icon:'◌',detail:current,state:'done',meta:''});
        return;
      }
      if (type === 'llm_delta') {
        connected = true;
        terminalSeen = true;
        streamedText += String(d.text || '');
        current = 'Typing… · model connected';
        return;
      }
      if (type === 'tool_call') {
        connected = true;
        terminalSeen = true;
        const tool = String(d.tool || 'tool');
        const detail = toolProgress(tool, d.args || {});
        const key = 'tool-'+String(d.step||0)+'-'+tool+'-'+index;
        const toolItem = {key,icon:'●',detail,state:'running',meta:step,tool};
        items.push(toolItem);
        openTools.set(String(tool)+'|'+String(d.step||0), toolItem);
        current = detail;
        return;
      }
      if (type === 'tool_result') {
        connected = true;
        terminalSeen = true;
        const tool = String(d.tool || 'tool');
        const sig = String(tool)+'|'+String(d.step||0);
        const item = openTools.get(sig);
        if (item) {
          item.state = d.error ? 'error' : 'done';
          item.icon = d.error ? '!' : '✓';
          item.meta = d.error ? ((d.ms ? d.ms+'ms' : '') || 'failed') : (d.ms ? d.ms+'ms' : 'done');
          openTools.delete(sig);
        } else {
          add({key:'result-'+index,icon:d.error?'!':'✓',detail:(d.error?'Tool failed · ':'Completed ')+tool,state:d.error?'error':'done',meta:d.ms ? d.ms+'ms' : ''});
        }
        current = d.error ? 'Tool failed · continuing…' : 'Thinking…';
        return;
      }
      if (type === 'tools_expanded') {
        add({key:'expand-'+index,icon:'+',detail:'Expanding available tools',state:'done',meta:d.tools_available ? d.tools_available+' tools' : ''});
        current = 'Thinking…';
        return;
      }
      if (type === 'verification') {
        add({key:'verify-'+index,icon:'✓',detail:'Checking the result',state:d.verified === false ? 'error' : 'done',meta:d.verified === false ? 'needs attention' : 'verified'});
        current = 'Verifying…';
        return;
      }
      if (type === 'approval_required') {
        add({key:'approval-'+index,icon:'!',detail:'Waiting for your approval',state:'running',meta:String(d.tool || 'sensitive action')});
        current = 'Waiting for approval…';
        return;
      }
      if (type === 'skill_harvest_started') {
        add({key:'skill-'+index,icon:'✦',detail:'Learning from the completed work',state:'done',meta:''});
        current = 'Finishing…';
        return;
      }
      if (type === 'skill_created') {
        add({key:'skill-created-'+index,icon:'✦',detail:'Reusable skill created',state:'done',meta:String(d.name || '')});
        return;
      }
      if (type === 'steer_applied') {
        add({key:'steer-'+index,icon:'↳',detail:'Applying your latest instruction',state:'done',meta:d.count ? String(d.count)+' update(s)' : ''});
        current = 'Thinking…';
        return;
      }
      if (type === 'model_capability_warning') {
        const recommended = String(d.recommended_model || '');
        add({key:'capability-'+index,icon:'!',detail:'Model capability check',state:'error',meta:recommended ? 'Try '+recommended : 'compatibility warning'});
        current = 'Adjusting model/tool plan…';
        return;
      }
      if (type === 'run_error' || type === 'mission_error') {
        add({key:'error-'+index,icon:'!',detail:'Execution failed',state:'error',meta:String(d.error || d.message || 'runtime error').slice(0,160)});
        current = 'Model unavailable';
        return;
      }
      if (type === 'run_finished' || type === 'mission_finished') {
        current = 'Finishing…';
      }
    });

    if (!connected && terminalSeen) connected = true;
    return {items:items.slice(-10), model, connected, current, streamedText};
  }

  function splitTableRow(line) {
    const raw=String(line||'').trim().replace(/^\|/,'').replace(/\|$/,'');
    const cells=[]; let buf=''; let escaped=false;
    for(const ch of raw){
      if(ch==='|'&&!escaped){cells.push(buf.trim().replace(/\\\|/g,'|'));buf='';}
      else buf+=ch;
      escaped=(ch==='\\'&&!escaped);
      if(ch!=='\\')escaped=false;
    }
    cells.push(buf.trim().replace(/\\\|/g,'|'));
    return cells;
  }

  function inlineMarkdown(value) {
    let s=esc(value);
    const stash=[];
    const hold=html=>{const key='@@MD'+stash.length+'@@';stash.push(html);return key;};
    s=s.replace(/\`([^\n\`]+)\`/g,(_,v)=>hold('<code>'+v+'</code>'));
    s=s.replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>');
    s=s.replace(/__([^_\n]+)__/g,'<strong>$1</strong>');
    s=s.replace(/\*([^*\n]+)\*/g,'<em>$1</em>');
    s=s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,(_,label,url)=>hold('<a href="'+url.replace(/"/g,'&quot;')+'" target="_blank" rel="noreferrer noopener">'+label+'</a>'));
    return s.replace(/@@MD(\d+)@@/g,(_,i)=>stash[Number(i)]||'');
  }

  function renderMarkdown(markdown) {
    const fence=String.fromCharCode(96).repeat(3);
    const source=String(markdown??'').replace(/\r\n?/g,'\n');
    if(!source.trim())return '';
    const lines=source.split('\n'),out=[];let i=0,inCode=false,codeLang='',code=[];
    const flushCode=()=>{
      if(!inCode)return;
      out.push('<pre class="md-code"><code data-lang="'+esc(codeLang)+'">'+esc(code.join('\n'))+'</code></pre>');
      inCode=false;codeLang='';code=[];
    };
    const isTableSep=line=>{
      const cells=splitTableRow(line);
      return cells.length>=2&&cells.every(x=>/^:?-{3,}:?$/.test(x.replace(/\s/g,'')));
    };
    while(i<lines.length){
      const line=lines[i];
      if(inCode){
        if(line.trim().startsWith(fence)){flushCode();i++;continue;}
        code.push(line);i++;continue;
      }
      if(line.trim().startsWith(fence)){
        inCode=true;codeLang=line.trim().slice(fence.length).trim();i++;continue;
      }
      if(!line.trim()){i++;continue;}
      const heading=line.match(/^\s*(#{1,6})\s+(.+?)\s*#*\s*$/);
      if(heading){const n=heading[1].length;out.push('<h'+n+'>'+inlineMarkdown(heading[2])+'</h'+n+'>');i++;continue;}
      if(/^\s*(?:---+|\*\s*\*\s*\*|___+)\s*$/.test(line)){out.push('<hr>');i++;continue;}
      if(/^\s*>/.test(line)){
        const rows=[];while(i<lines.length&&/^\s*>/.test(lines[i])){rows.push(lines[i].replace(/^\s*>\s?/,'').trim());i++;}
        out.push('<blockquote>'+rows.map(inlineMarkdown).join('<br>')+'</blockquote>');continue;
      }
      if(i+1<lines.length&&line.includes('|')&&isTableSep(lines[i+1])){
        const headers=splitTableRow(line);i+=2;const rows=[];
        while(i<lines.length&&lines[i].trim()&&lines[i].includes('|')){rows.push(splitTableRow(lines[i]));i++;}
        out.push('<div class="md-table-wrap"><table><thead><tr>'+
          headers.map(h=>'<th>'+inlineMarkdown(h)+'</th>').join('')+
          '</tr></thead><tbody>'+
          rows.map(r=>'<tr>'+headers.map((_,idx)=>'<td>'+inlineMarkdown(r[idx]??'')+'</td>').join('')+'</tr>').join('')+
          '</tbody></table></div>');continue;
      }
      const ul=line.match(/^\s*[-*+]\s+(.+)$/);
      if(ul){
        const rows=[];while(i<lines.length){const m=lines[i].match(/^\s*[-*+]\s+(.+)$/);if(!m)break;rows.push('<li>'+inlineMarkdown(m[1])+'</li>');i++;}
        out.push('<ul>'+rows.join('')+'</ul>');continue;
      }
      const ol=line.match(/^\s*\d+[.)]\s+(.+)$/);
      if(ol){
        const rows=[];while(i<lines.length){const m=lines[i].match(/^\s*\d+[.)]\s+(.+)$/);if(!m)break;rows.push('<li>'+inlineMarkdown(m[1])+'</li>');i++;}
        out.push('<ol>'+rows.join('')+'</ol>');continue;
      }
      const para=[];while(i<lines.length&&lines[i].trim()){
        const next=lines[i];
        if(para.length&&(/^\s*#{1,6}\s+/.test(next)||/^\s*(?:[-*+]\s+|\d+[.)]\s+|>)/.test(next)))break;
        if(para.length&&i+1<lines.length&&next.includes('|')&&isTableSep(lines[i+1]))break;
        para.push(next);i++;
      }
      out.push('<p>'+para.map(inlineMarkdown).join('<br>')+'</p>');
    }
    flushCode();
    return out.join('');
  }

  function chatStreamUrl(runId) {
    const suffix=token?'&token='+encodeURIComponent(token):'';
    return '/stream/run/'+encodeURIComponent(runId)+'?after=0'+suffix;
  }

  async function sendCommand(command) {
    const text = String(command || '').trim();
    if (!text) return;

    state.messages.push({who:'user',text,time:'Now'});
    const statusMessage = {
      who:'jarvis',
      text:'Connecting to model…',
      time:'Now',
      pending:true,
      status:'connecting'
    };
    state.messages.push(statusMessage);
    renderChat();

    try {
      const session = localStorage.getItem('hermus_session_id') || '';
      const result = await api('/jobs', {
        method:'POST',
        body:JSON.stringify({
          kind:'runtime.turn',
          payload:{text,platform:'web',mode:'chat',stream:true,model:(state.settings.chat_model||undefined),session_id:session || undefined,user_id:'default'}
        })
      });
      if (result.session_id) localStorage.setItem('hermus_session_id', String(result.session_id));

      const run = result.run_id || result.mission_id;
      if (run) {
        statusMessage.runId = String(run);
        statusMessage.text = 'Thinking…';
        statusMessage.status = 'thinking';
        renderChat();
        refreshOverview();
        trackRun(String(run));
        return run;
      }

      const answer = String(result.response || result.final_answer || result.answer || '').trim();
      if (answer) {
        statusMessage.text = answer.slice(0, 12000);
        statusMessage.pending = false;
        statusMessage.status = 'done';
      } else {
        statusMessage.text = 'HERMUS is working…';
        statusMessage.pending = false;
        statusMessage.status = 'working';
      }
      renderChat();
      refreshOverview();
      return run;
    } catch (e) {
      statusMessage.text = 'Model unavailable · '+e.message;
      statusMessage.pending = false;
      statusMessage.status = 'error';
      renderChat();
      toast(e.message,true);
    }
  }

  function trackRun(runId) {
    if(!runId||state.runPollers.has(runId))return;
    state.activeRun=runId;
    let finalMessageShown=false;
    let eventLog=[];
    const seen=new Set();
    let source=null;
    let fallbackTimer=null;
    let fallbackTries=0;

    const pendingMessage=()=>state.messages.find(m=>m.runId===runId&&m.pending);

    const sync=()=>{
      const pending=pendingMessage();
      if(!pending||finalMessageShown)return;
      const summary=summarizeRunActivity(eventLog);
      pending.activity=summary.items;
      pending.model=summary.model||pending.model||'';
      pending.streamingText=summary.streamedText||'';
      pending.text=summary.current||'Thinking…';
      pending.status=summary.current==='Model unavailable'?'error':(summary.connected?'connected':'thinking');
      renderChat();
    };

    const showAnswer=(value,status='done')=>{
      const answer=String(value||'').trim();
      if(!answer||finalMessageShown)return;
      finalMessageShown=true;
      const clipped=answer.slice(0,12000);
      const pending=pendingMessage();
      if(pending){
        pending.text=clipped;
        pending.pending=false;
        pending.status=status;
        pending.streamingText='';
      }else if(!state.messages.some(m=>m.runId===runId&&m.text===clipped)){
        state.messages.push({who:'jarvis',text:clipped,time:'Now',runId});
      }
      renderChat();
    };

    const consume=e=>{
      if(!e)return;
      const eventId=e.id===undefined||e.id===null?'local-'+eventLog.length:String(e.id);
      if(seen.has(eventId))return;
      seen.add(eventId);
      eventLog.push(e);
      const type=String(e.type||'').toLowerCase(),d=e.data||{};
      if(type==='agent_response')showAnswer(d.text||d.response||d.summary||'');
      else if(type==='run_error'||type==='mission_error')showAnswer('Model unavailable · '+String(d.error||d.message||'runtime error'),'error');
      else if(!finalMessageShown)sync();
      if(type==='run_finished'||type==='mission_finished'||type==='__closed__'){
        if(!finalMessageShown){
          if(['error','failed'].includes(String(d.status||'').toLowerCase()))showAnswer('Model unavailable · HERMUS could not complete this request','error');
          else showAnswer('HERMUS completed the request but returned no response','error');
        }
        stop();
      }
    };

    const startPolling=()=>{
      if(fallbackTimer||finalMessageShown)return;
      fallbackTimer=setInterval(async()=>{
        fallbackTries++;
        const d=await safe('/runs/'+encodeURIComponent(runId)+'?limit=500');
        if(failed(d)){if(fallbackTries>10)stop();return;}
        for(const e of (Array.isArray(d.events)?d.events:[]))consume(e);
        if(d.result&&!finalMessageShown)showAnswer(d.result.response||d.result.final_answer||d.result.answer||'');
      },700);
    };

    const stop=()=>{
      if(fallbackTimer){clearInterval(fallbackTimer);fallbackTimer=null;}
      if(source){try{source.close()}catch{}source=null;}
      const x=state.runPollers.get(runId);
      if(x)state.runPollers.delete(runId);
      if(state.activeRun===runId)state.activeRun=null;
      refreshOverview();
    };

    try{
      source=new EventSource(chatStreamUrl(runId));
      state.runPollers.set(runId,{source,stop});
      const streamEvents=[
        'run_started','turn_started','mission_runtime_started','step_started','step_observed',
        'llm_delta','llm_finished','tool_call','tool_result','tools_expanded','memory','skill',
        'skill_harvest_started','skill_created','subagent','approval_required','verification',
        'steer','steer_applied','steer_consumed','model_capability_warning','job_status',
        'runtime_issue','agent_response','run_error','mission_error','run_finished','mission_finished','log',
        'cancel_requested'
      ];
      for(const name of streamEvents){
        source.addEventListener(name,message=>{try{consume(JSON.parse(message.data));}catch{}});
      }
      source.onerror=()=>{startPolling();};
      setTimeout(()=>{if(!eventLog.length)startPolling();},1200);
    }catch{
      state.runPollers.set(runId,{source:null,stop});
      startPolling();
    }
  }


  function renderChat() {
    const host=qs('#chatMessages');if(!host)return;
    host.innerHTML=state.messages.slice(-30).map(m=>{
      const classes=['msg'];
      if(m.who==='user')classes.push('user');
      if(m.pending)classes.push('pending');
      if(m.status)classes.push('status-'+String(m.status).replace(/[^a-z0-9_-]/gi,''));
      const activity=Array.isArray(m.activity)&&m.activity.length
        ? '<div class="live-activity" aria-label="Live agent activity">'+m.activity.map(a=>
            '<div class="activity-item '+esc(a.state||'done')+'"><span class="activity-icon">'+esc(a.icon||'·')+
            '</span><span class="activity-detail">'+esc(a.detail||'Working')+'</span>'+
            (a.meta?'<span class="activity-meta">'+esc(a.meta)+'</span>':'')+'</div>'
          ).join('')+'</div>'
        : '';
      const liveLabel=m.pending
        ? '<div class="live-status" role="status" aria-live="polite"><span class="live-pulse"></span>'+esc(m.text)+'</div>'
        : '';
      const stream=m.pending&&m.streamingText
        ? '<div class="msg-stream" aria-label="Live assistant response">'+renderMarkdown(m.streamingText)+'<span class="stream-cursor" aria-hidden="true">▌</span></div>'
        : '';
      const body=m.pending?'':'<div class="msg-text">'+renderMarkdown(m.text)+'</div>';
      return '<div class="'+classes.join(' ')+'"><span class="msg-meta">'+
        (m.who==='user'?'YOU':'JARVIS')+' · '+esc(m.time)+'</span>'+liveLabel+stream+body+activity+'</div>';
    }).join('');
    host.scrollTop=host.scrollHeight;
  }

  async function refreshOverview() {
    const [health,ready,presence,agents,catalog,selected] = await Promise.all([
      safe('/api/v1/system/health'),
      safe('/readyz'),
      safe('/presence/kernel?user_id=default&include_events=true'),
      safe('/agents'),
      safe('/models/catalog'),
      safe('/models/selected')
    ]);
    const connected = !failed(ready) || !failed(health);
    setOnline(connected, failed(ready) ? (failed(health) ? 'OFFLINE' : 'DEGRADED') : 'ONLINE');
    let quality = 'OFFLINE';
    if (!failed(health)) {
      const good = Object.values(health).filter(v => v && (v.ok === true || v.running === true || v.installed === true)).length;
      quality = good ? 'HEALTHY' : 'DEGRADED';
    }
    const healthNode = qs('#overviewHealth');
    if (healthNode) { healthNode.textContent=quality; healthNode.classList.toggle('warn',quality!=='HEALTHY'); }
    const a = Array.isArray(agents?.agents) ? agents.agents : [];
    state.agents = a;
    qs('#statAgents').textContent = String(a.length);
    qs('#statTasks').textContent = String(Number(presence?.summary?.active_runs || 0));
    state.modelCatalog = Array.isArray(catalog?.models) ? catalog.models : [];
    state.selectedModels = selected?.selections || {};
    qs('#statModels').textContent = String(state.modelCatalog.length);
    qs('#statAttention').textContent = String(Number(presence?.summary?.attention_count || 0));
    const attention = Array.isArray(presence?.attention) ? presence.attention : [];
    const ah = qs('#overviewAttention');
    if (ah) ah.innerHTML = attention.length ? attention.slice(0,4).map(i =>
      '<div class="row"><div class="row-main"><strong>'+esc(i.title||'Attention')+'</strong><small>'+esc(i.detail||'Needs review')+'</small></div>'+
      badge(i.severity||'normal',String(i.severity||'').toLowerCase()==='critical'?'bad':'warn')+'</div>'
    ).join('') : '<div class="empty">All systems are calm. Nothing needs your attention.</div>';
    const active = qs('#overviewAgents');
    if (active) active.innerHTML = a.slice(0,5).map(x => {
      const s=String(x.state||x.status||'idle').toLowerCase(), running=['running','working','active'].includes(s);
      return '<div class="row"><div class="row-main"><strong>'+esc(x.name||x.role||'Agent')+'</strong><small>'+esc(String(x.role||'general').replace(/_/g,' '))+' · '+esc(s)+'</small></div>'+
      badge(s,running?'good':s==='error'?'bad':'')+'</div>';
    }).join('') || '<div class="empty">No persistent agents are registered.</div>';
    const runs = Array.isArray(presence?.events?.recent) ? presence.events.recent : [];
    const rh = qs('#overviewActivity');
    if (rh) rh.innerHTML = runs.slice(-6).reverse().map(e =>
      '<div class="row"><div class="row-main"><strong>'+esc(e.summary||e.type||'Event')+'</strong><small>'+esc(e.at||'recent')+'</small></div>'+
      badge(String(e.type||'event').replace(/_/g,' '))+'</div>'
    ).join('') || '<div class="empty">No recent execution events.</div>';
  }

  async function loadAgents() {
    const data = await safe('/agents');
    const list = Array.isArray(data?.agents) ? data.agents : [];
    state.agents=list;
    const host=qs('#agentsHost'); if(!host)return;
    const form = '<div class="card form-card"><div class="card-head"><div><strong>REGISTER SPECIALIST</strong><small>Create a persistent agent backed by the canonical agent manager.</small></div></div><div class="card-body"><div class="form-grid"><label class="field">NAME<input id="agentName" placeholder="Researcher"></label><label class="field">ROLE<input id="agentRole" placeholder="research"></label><label class="field">MODEL<input id="agentModel" placeholder="auto / deployment ref"></label><label class="field full">PERSONA<textarea id="agentPersona" placeholder="What should this specialist optimize for?"></textarea></label></div><div class="actions"><button class="btn primary" type="button" data-agent-create>CREATE AGENT</button><button class="btn" type="button" data-view="settings" data-settings-tab-target="models">MODEL ROUTING</button></div><div id="agentCreateResult" class="result">Not submitted.</div></div></div>';
    if (failed(data)) { host.innerHTML=form+errorCard(errText(data)); return; }
    const cards = list.length ? '<div class="agent-grid">'+list.map(a => {
      const name=String(a.name||a.id||a.role||'Agent'), status=String(a.status||a.state||'idle').toLowerCase();
      const running=['running','active','working'].includes(status);
      const payload=encodeURIComponent(name);
      return '<article class="agent-card"><div class="agent-top"><div><div class="agent-name">'+esc(name)+'</div><div class="meta">'+esc(String(a.role||'general').replace(/_/g,' '))+(a.model?' · '+esc(a.model):'')+'</div></div>'+
      badge(status,running?'good':status==='error'?'bad':'')+'</div><div class="actions"><button class="btn '+(running?'':'primary')+'" data-agent-action="'+(running?'stop':'start')+'" data-agent="'+payload+'">'+(running?'STOP':'START')+'</button><button class="btn" data-agent-action="chat" data-agent="'+payload+'">CHAT</button></div></article>';
    }).join('')+'</div>' : '<div class="state-card"><strong>No persistent agents yet.</strong><p>Create a specialist above instead of using a fake proposal flow.</p></div>';
    host.innerHTML=form+cards;
  }

  async function createAgent() {
    const name=qs('#agentName')?.value.trim();
    if(!name){toast('Agent name is required',true);return;}
    try {
      const out=await api('/agents/create',{method:'POST',body:JSON.stringify({
        name,role:qs('#agentRole')?.value.trim()||'generic',model:qs('#agentModel')?.value.trim()||null,persona:qs('#agentPersona')?.value.trim()||null
      })});
      qs('#agentCreateResult').textContent=out.success===false?'Create failed · '+(out.error||'unknown'):'Agent registered successfully.';
      toast('Agent created');
      await loadAgents(); refreshOverview();
    } catch(e){qs('#agentCreateResult').textContent='Create failed · '+e.message;toast(e.message,true);}
  }

  async function loadModels({refresh=false,probe=false}={}) {
    const [cat, sel] = await Promise.all([
      safe('/models/catalog?probe='+String(!!probe)+'&refresh='+String(!!refresh)),
      safe('/models/selected')
    ]);
    state.modelCatalog = Array.isArray(cat?.models) ? cat.models : [];
    state.selectedModels = sel?.selections || {};

    const role = qs('#modelRoleInput');
    const deployment = qs('#modelDeploymentInput');
    if (role && deployment) {
      const current = state.selectedModels[role.value] || 'auto';
      const search=(qs('#modelSearch')?.value||'').trim().toLowerCase();
      const visibleModels=state.modelCatalog.filter(m=>{
        if(!search) return true;
        return [m.name,m.id,m.ref,m.provider,m.provider_name].filter(Boolean).join(' ').toLowerCase().includes(search);
      });
      deployment.innerHTML =
        '<option value="auto">AUTO · best available</option>' +
        visibleModels.map((m) =>
          '<option value="' + esc(m.ref) + '"' +
          (m.ref === current ? ' selected' : '') + '>' +
          esc(m.name || m.id || m.ref) +
          '</option>'
        ).join('');
      deployment.value = [...deployment.options].some((o) => o.value === current) ? current : 'auto';
    }

    const selected = deployment?.value || 'auto';
    const cap = selected !== 'auto'
      ? await safe('/models/capabilities?model=' + encodeURIComponent(selected))
      : null;
    const capNote = qs('#modelCapabilityNote');
    if (capNote) {
      capNote.textContent = cap && !failed(cap)
        ? 'Capabilities · tools ' + valueSummary(cap.tools) +
          ' · vision ' + valueSummary(cap.vision) +
          ' · computer ' + valueSummary(cap.computer_control || cap.computer)
        : cap && failed(cap)
          ? 'Capability probe unavailable · routing can still use live catalog data.'
          : 'Model capabilities update when a deployment is selected.';
    }

    const host = qs('#modelsHost');
    if (!host) return;
    if (failed(cat)) {
      host.innerHTML = errorCard(errText(cat));
      return;
    }

    const renderModelCards = () => {
      const query=(qs('#modelSearch')?.value||'').trim().toLowerCase();
      const filtered=state.modelCatalog.filter(m=>{
        if(!query) return true;
        const hay=[
          m.name,m.id,m.ref,m.provider,m.provider_name,m.owned_by,
          m.capability_notes?.join?.(' '),
          m.capabilities ? Object.entries(m.capabilities).map(([k,v])=>k+' '+v).join(' ') : ''
        ].filter(Boolean).join(' ').toLowerCase();
        return hay.includes(query);
      });
      const cards=filtered.map((m)=>{
        const live=m.source==='live';
        const reachable=m.reachable!==false;
        const status=!reachable?'OFFLINE':live?'LIVE':'CACHED';
        const statusKind=!reachable?'bad':live?'good':'';
        const selectedForRole=(state.selectedModels[qs('#modelRoleInput')?.value||'default']||'auto')===m.ref;
        return '<article class="model-card">'+
          '<div class="model-top"><div>'+
          '<div class="model-name">'+esc(m.name||m.id||m.ref)+'</div>'+
          '<div class="meta">'+esc(m.provider||'provider')+' · '+esc(m.ref||m.id||'model')+
          '</div></div>'+badge(status,statusKind)+'</div>'+
          '<div class="meta" style="margin-top:10px">Tools: '+esc(m.capabilities?.tools||'unknown')+
          ' · Vision: '+esc(m.capabilities?.vision||'unknown')+
          ' · Context: '+esc(m.context_length||m.context||'unknown')+
          '</div><div class="actions" style="margin-top:12px">'+
          '<button class="btn '+(selectedForRole?'':'primary')+'" type="button" data-model-pick="'+esc(m.ref)+'">'+
          (selectedForRole?'SELECTED FOR ROLE':'SELECT FOR ROLE')+'</button></div></article>';
      }).join('');
      const count=filtered.length;
      qs('#modelResultCount').textContent=query?(count+' matching deployment'+(count===1?'':'s')):state.modelCatalog.length+' deployment'+(state.modelCatalog.length===1?'':'s');
      qs('#modelCardsHost').innerHTML=cards||'<div class="state-card"><strong>No matching deployments.</strong><p>Try a provider, model name, or capability.</p></div>';
    };

    host.innerHTML='<div class="card" style="margin-bottom:12px"><div class="card-body"><div class="toolbar">'+
      '<label class="field grow">SEARCH MODELS<input id="modelSearch" type="search" placeholder="Search model name, provider, capability…" aria-label="Search models"></label>'+
      '<span id="modelResultCount" class="compact-result"></span></div></div></div>'+
      '<div id="modelCardsHost"></div>';
    qs('#modelSearch').addEventListener('input',renderModelCards);
    renderModelCards();
  }
  async function saveModel() {
    const role=qs('#modelRoleInput')?.value||'default', model=qs('#modelDeploymentInput')?.value||'auto';
    try {
      await api('/models/select',{method:'POST',body:JSON.stringify({role,model})});
      state.selectedModels[role]=model; toast('Model routing saved'); await loadModels();
    } catch(e){toast(e.message,true);}
  }

  async function loadTools() {
    const [toolsData,providerData]=await Promise.all([safe('/tools'),safe('/providers/available')]);
    const raw=Array.isArray(toolsData?.tools)?toolsData.tools:(Array.isArray(toolsData)?toolsData:[]);
    const providers=Array.isArray(providerData?.providers)?providerData.providers:[];
    const host=qs('#toolsHost');if(!host)return;
    const providerHtml=providers.map(p=>{
      const ready=!!(p.usable??p.available??p.configured??p.ok);
      return '<article class="tool-card"><div class="tool-top"><div><div class="tool-name">'+esc(p.name||p.id||p.provider||'Provider')+'</div><div class="meta">'+esc(p.base_url||p.reason||p.detail||'Provider resolver')+'</div></div>'+
      badge(ready?'READY':'SETUP',ready?'good':'warn')+'</div></article>';
    }).join('');
    const toolHtml=raw.slice(0,120).map(d=>{
      const name=String(d.name||d.id||d.tool||'tool');
      return '<article class="tool-card"><div class="tool-top"><div><div class="tool-name">'+esc(name.replace(/_/g,' '))+'</div><div class="meta">'+esc(d.description||d.summary||'Registered HERMUS capability')+'</div></div>'+
      badge(d.enabled===false?'UNAVAILABLE':'READY',d.enabled===false?'warn':'good')+'</div><div class="actions" style="margin-top:9px"><button class="btn" type="button" data-tool-use="'+esc(name)+'">USE IN CHAT</button></div></article>';
    }).join('');
    host.innerHTML='<div class="grid cols-2"><div class="card"><div class="card-head"><div><strong>PROVIDER FABRIC</strong><small>Resolved providers, not hard-coded assumptions</small></div><button class="btn" type="button" data-view="settings" data-settings-tab-target="providers">CONFIGURE</button></div><div class="card-body list">'+(providerHtml||'<div class="empty">No provider state reported.</div>')+'</div></div>'+
    '<div class="card"><div class="card-head"><div><strong>QUICK WORKSPACES</strong><small>Direct routes for the capabilities people use most</small></div></div><div class="card-body actions"><button class="btn primary" type="button" data-view="browser">BROWSER</button><button class="btn" type="button" data-view="terminal">TERMINAL</button><button class="btn" type="button" data-view="image">IMAGE LAB</button><button class="btn" type="button" data-view="video">VIDEO STUDIO</button><button class="btn" type="button" data-view="computer">COMPUTER</button></div></div></div>'+
    '<div class="card" style="margin-top:12px"><div class="card-head"><div><strong>REGISTERED CAPABILITIES</strong><small>'+raw.length+' tools discovered from the canonical registry</small></div></div><div class="card-body"><div class="tool-grid">'+(toolHtml||'<div class="empty">No registered tools reported.</div>')+'</div></div></div>';
  }

  async function loadMissions() {
    const [m,r]=await Promise.all([safe('/missions'),safe('/routines')]);
    const missions=Array.isArray(m?.missions)?m.missions:[], routines=Array.isArray(r?.routines)?r.routines:[];
    const host=qs('#missionsHost');if(!host)return;
    const missionForm='<div class="card form-card"><div class="card-head"><div><strong>START A MISSION</strong><small>Create a recoverable mission and let the backend perform preflight before execution.</small></div></div><div class="card-body"><div class="form-grid"><label class="field full">GOAL<textarea id="missionGoal" placeholder="Research competitors, compare them and produce a recommendation."></textarea></label><label class="field">DOMAIN<input id="missionDomain" placeholder="research"></label><label class="field">BUDGET STEPS<input id="missionBudget" type="number" min="1" placeholder="auto"></label><label class="field"><span>PREFLIGHT</span><select id="missionPreflight"><option value="true">Required</option><option value="false">Skip</option></select></label><label class="field"><span>ALLOW PREFLIGHT PLANNING</span><select id="missionPlanning"><option value="false">No</option><option value="true">Yes</option></select></label></div><div class="actions"><button class="btn primary" type="button" data-mission-create>START MISSION</button><button class="btn" type="button" data-view="chat">START AS CHAT</button></div><div id="missionCreateResult" class="result">Not submitted.</div></div></div>';
    if(failed(m)){host.innerHTML=missionForm+errorCard(errText(m));return;}
    const missionHtml='<div class="card"><div class="card-head"><div><strong>MISSIONS</strong><small>Recoverable execution history</small></div><span class="badge">'+missions.length+' TOTAL</span></div><div class="card-body list">'+(missions.length?missions.map(x=>{
      const st=String(x.state||x.status||'unknown').toLowerCase(), recover=['failed','blocked','interrupted'].includes(st);
      return '<div class="row"><div class="row-main"><strong>'+esc(x.goal||x.title||x.id||'Mission')+'</strong><small>'+esc(st)+(x.id?' · '+esc(x.id):'')+'</small></div><div class="row-actions">'+
      (recover?'<button class="btn primary" type="button" data-mission-resume="'+esc(x.id)+'">RESUME</button>':'')+
      (x.id?'<button class="btn" type="button" data-mission-open="'+esc(x.id)+'">INSPECT</button>':'')+
      (!recover?badge(st):'')+'</div></div>';
    }).join(''):'<div class="empty">No missions yet.</div>')+'</div></div>';
    const routineHtml='<div class="card"><div class="card-head"><div><strong>ROUTINES</strong><small>Proactive automation</small></div><button class="btn primary" type="button" data-routine-new>NEW</button></div><div class="card-body list">'+(routines.length?routines.map(x=>{
      const enabled=!!x.enabled,id=x.id||x.name;
      return '<div class="row"><div class="row-main"><strong>'+esc(x.name||id)+'</strong><small>'+esc((x.event_type||'schedule')+' → '+(x.action_type||'runtime.turn'))+'</small></div><div class="row-actions"><button class="btn" type="button" data-routine-toggle data-id="'+esc(id)+'" data-enabled="'+(!enabled)+'">'+(enabled?'DISABLE':'ENABLE')+'</button><button class="btn danger" type="button" data-routine-delete data-id="'+esc(id)+'">DELETE</button></div></div>';
    }).join(''):'<div class="empty">No proactive routines configured.</div>')+'</div></div>';
    host.innerHTML=missionForm+'<div class="grid cols-2">'+missionHtml+routineHtml+'</div><div id="routineFormHost"></div><div id="missionDetailHost"></div>';
  }

  async function createMission() {
    const goal=qs('#missionGoal')?.value.trim();
    if(!goal){toast('Mission goal is required',true);return;}
    const budget=qs('#missionBudget')?.value;
    try{
      const out=await api('/missions',{method:'POST',body:JSON.stringify({
        goal,domain:qs('#missionDomain')?.value.trim()||null,
        budget_steps:budget?Number(budget):undefined,
        preflight:qs('#missionPreflight')?.value==='true',
        allow_preflight_planning:qs('#missionPlanning')?.value==='true'
      })});
      const id=out.id||out.mission_id;
      qs('#missionCreateResult').textContent='Mission started'+(id?' · '+id:'')+'.';
      toast('Mission created'); loadMissions();
    }catch(e){qs('#missionCreateResult').textContent='Start failed · '+e.message;toast(e.message,true);}
  }

  function routineForm() {
    const host=qs('#routineFormHost');if(!host)return;
    host.innerHTML='<div class="card form-card" style="margin-top:12px"><div class="card-head"><div><strong>CREATE ROUTINE</strong><small>Validate first, then save. The existing automation owner remains authoritative.</small></div></div><div class="card-body"><div class="form-grid"><label class="field">NAME<input id="routineName" placeholder="Daily review"></label><label class="field">TRIGGER<input id="routineEvent" placeholder="schedule"></label><label class="field">ACTION<select id="routineAction"><option value="runtime.turn">Runtime turn</option><option value="agent.autonomous">Autonomous agent</option><option value="mission.start">Start mission</option></select></label><label class="field">COOLDOWN<input id="routineCooldown" type="number" min="0" value="60"></label><label class="field full">TASK<textarea id="routineTask" placeholder="Review my current project and surface anything important."></textarea></label></div><div class="actions" style="margin-top:10px"><button class="btn primary" type="button" data-routine-save>CREATE</button><button class="btn" type="button" data-routine-validate>VALIDATE</button></div><div id="routineResult" class="result">Not submitted.</div></div></div>';
    host.scrollIntoView({behavior:'smooth',block:'nearest'});
  }

  async function submitRoutine(validateOnly) {
    const payload={
      name:qs('#routineName')?.value||'routine',
      event_type:qs('#routineEvent')?.value||'',
      action_type:qs('#routineAction')?.value||'runtime.turn',
      task:qs('#routineTask')?.value||'',
      cooldown_seconds:Number(qs('#routineCooldown')?.value||60),
      enabled:false
    };
    try{
      const out=await api(validateOnly?'/routines/validate':'/routines',{method:'POST',body:JSON.stringify(payload)});
      const box=qs('#routineResult');
      if(box) box.textContent=validateOnly?(out.valid?'VALID · ready to create':'NOT VALID · '+((out.errors||[]).join(' · ')||'review the fields')):'Routine created successfully.';
      if(!validateOnly) loadMissions();
    }catch(e){const box=qs('#routineResult');if(box)box.textContent='Error · '+e.message;toast(e.message,true);}
  }

  async function loadKnowledge() {
    const [data,emb]=await Promise.all([safe('/learning?limit=30'),safe('/embeddings/status')]);
    const rows=[]; for(const k of ['lessons','skills','episodes','items']) if(Array.isArray(data?.[k])) rows.push(...data[k].map(x=>({kind:k,...x})));
    const host=qs('#knowledgeHost');if(!host)return;
    host.innerHTML='<div class="card"><div class="card-head"><div><strong>SAVE KNOWLEDGE</strong><small>Write durable context through the canonical memory facade.</small></div></div><div class="card-body"><div class="compose"><textarea id="knowledgeNote" placeholder="Write a project decision, discovery or reusable lesson…"></textarea><button class="btn primary" type="button" data-knowledge-save>SAVE</button></div><div id="knowledgeResult" class="result">No note saved.</div></div></div>'+
      '<div class="grid cols-2" style="margin-top:12px"><div class="card"><div class="card-head"><div><strong>LEARNING FABRIC</strong><small>What HERMUS has retained</small></div><span class="badge">'+rows.length+' RECORDS</span></div><div class="card-body list">'+(failed(data)?'<div class="empty">'+esc(errText(data))+'</div>':(rows.map(x=>'<div class="row"><div class="row-main"><strong>'+esc(x.title||x.name||x.content||'Learning record')+'</strong><small>'+esc(x.kind)+(x.summary?' · '+esc(x.summary):'')+'</small></div>'+badge('SAVED','good')+'</div>').join('')||'<div class="empty">No learning records yet.</div>'))+'</div></div>'+
      '<div class="card"><div class="card-head"><div><strong>EMBEDDING ENGINE</strong><small>Semantic memory backend</small></div></div><div class="card-body">'+(failed(emb)?errorCard(errText(emb)):'<div class="stat-strip" style="margin:0">'+stat('BACKEND',valueSummary(emb.backend||emb.provider||emb.name),'configured store')+stat('READY',valueSummary(emb.ready??emb.available??true),'semantic search')+'</div>')+'</div></div></div>';
  }

  async function loadPersonal() {
    const data=await safe('/personal-space?limit=8');
    const proposal=Array.isArray(data?.proposals)?data.proposals.find(x=>x.status==='pending')||data.proposals[0]:null;
    const host=qs('#personalHost');if(!host)return;
    if(failed(data)){host.innerHTML=errorCard(errText(data));return;}
    host.innerHTML='<div class="grid cols-2"><div class="card"><div class="card-head"><div><strong>HERMUS PRIVATE SPACE</strong><small>Bounded curiosity · approval required</small></div>'+
      badge(data?.status||'READY',(data?.status||'').includes('proposal')?'warn':'good')+'</div><div class="card-body"><div class="stat-strip" style="margin:0 0 12px">'+
      stat('IDLE',Math.round(Number(data?.idle_for_seconds||0)/60)+'m','time available')+
      stat('TODAY',String(data?.daily_cycles??0),'/ '+String(data?.daily_cap??3)+' cycles')+'</div><div class="actions"><button class="btn primary" type="button" data-personal-run>THINK NOW</button><button class="btn" type="button" data-personal-refresh>REFRESH</button></div></div></div>'+
      '<div class="card"><div class="card-head"><div><strong>PROPOSAL</strong><small>Nothing becomes an action without you.</small></div></div><div class="card-body">'+
      (proposal?'<h3 style="margin:0 0 5px;font-size:12px">'+esc(proposal.title||'Discovery')+'</h3><p class="muted" style="font-size:9px;line-height:1.5">'+esc(proposal.summary||proposal.why||'A useful discovery is waiting.')+'</p><div class="actions" style="margin-top:12px"><button class="btn primary" type="button" data-proposal="approve" data-proposal-id="'+esc(proposal.id)+'">APPROVE</button><button class="btn danger" type="button" data-proposal="dismiss" data-proposal-id="'+esc(proposal.id)+'">DISMISS</button></div>':'<div class="empty">No proposal waiting for approval.</div>')+'</div></div></div>';
  }

  async function loadBrowser() {
    const host=qs('#browserHost');if(!host)return;
    if(!host.dataset.ready){
      host.innerHTML='<div class="grid cols-2"><div class="card"><div class="card-head"><div><strong>PUBLIC WEB FETCH</strong><small>Constrained by HERMUS navigator URL validation.</small></div></div><div class="card-body"><div class="toolbar"><label class="field grow">URL<input id="browserUrl" placeholder="https://example.com" aria-label="Browser URL"></label><button class="btn primary" type="button" data-browser-open>FETCH</button></div><div id="browserResult" class="result">No page loaded.</div></div></div><div class="card"><div class="card-head"><div><strong>NEXT ACTION</strong><small>Turn live web context into useful work.</small></div></div><div class="card-body actions"><button class="btn" type="button" data-browser-analyze>ANALYZE PAGE</button><button class="btn" type="button" data-browser-save>SAVE FINDING</button></div><div id="browserMeta" class="card-body compact-result">No page context.</div></div></div>';
      host.dataset.ready='1';
    }
  }
  async function browserOpen() {
    const url=(qs('#browserUrl')?.value||'').trim();if(!url)return;
    const box=qs('#browserResult'),meta=qs('#browserMeta');
    box.textContent='Fetching page…';
    try{
      const d=await api('/navigator/fetch',{method:'POST',body:JSON.stringify({url})});
      const content=String(d.content||d.text||'').trim();
      box.innerHTML='<strong>'+esc(d.title||d.url||'Page loaded')+'</strong><p style="margin:7px 0 0">'+esc(content.slice(0,5000)||'No readable content returned.')+'</p>';
      meta.textContent=(d.url||url)+' · '+String(d.content_length??content.length)+' characters';
      state.browserPage=d;
      toast('Browser context loaded');
    }catch(e){box.textContent='Browser error · '+e.message;toast(e.message,true);}
  }

  function toolWorkspace(type) {
    const map={
      terminal:['TERMINAL TASK','Route a command through HERMUS permission and execution boundaries.','terminalInput','Run a command…','RUN'],
      image:['IMAGE REQUEST','Send a visual request into the HERMUS runtime.','imageInput','Describe the image…','GENERATE'],
      video:['VIDEO REQUEST','Send a shot or sequence into the HERMUS media workflow.','videoInput','Describe the shot…','GENERATE']
    };
    const m=map[type],host=qs('#'+type+'Host');if(!host||!m)return;
    if(host.dataset.ready)return;
    host.innerHTML='<div class="card"><div class="card-head"><div><strong>'+m[0]+'</strong><small>'+m[1]+'</small></div><button class="btn" type="button" data-view="chat">OPEN RUN LOG</button></div><div class="card-body"><div class="toolbar"><label class="field grow">'+(type==='terminal'?'TASK':'PROMPT')+'<input id="'+m[2]+'" placeholder="'+m[3]+'"></label><button class="btn primary" type="button" data-tool-run="'+type+'">'+m[4]+'</button></div><div id="'+type+'Result" class="result">'+(type==='terminal'?'Ready.':'Nothing submitted yet.')+'</div></div></div>';
    host.dataset.ready='1';
  }
  async function runToolWorkspace(type) {
    const id={terminal:'terminalInput',image:'imageInput',video:'videoInput'}[type];
    const value=(qs('#'+id)?.value||'').trim();if(!value)return;
    const box=qs('#'+type+'Result');box.textContent='Submitting…';
    const command=type==='terminal'?value:(type==='image'?'Create an image: ':'Create a video: ')+value;
    const run=await sendCommand(command);
    box.textContent=run?'Accepted · run '+String(run).slice(0,10)+' is being tracked in Chat.':'Queued · open Chat for the execution history.';
  }

  async function loadBuild() {
    const host=qs('#buildHost');if(!host)return;
    if(!host.dataset.ready){
      host.innerHTML='<div class="workspace-shell"><div class="workspace-top"><div><div class="eyebrow">WORKSHOP</div><h3 id="buildProjectTitle">Workspace</h3></div><div class="actions"><button class="btn" type="button" data-build-refresh>REFRESH</button><button class="btn primary" type="button" data-build-new>NEW PROJECT</button><button class="btn" type="button" data-build-ask>ASK HERMUS</button></div></div><div class="workspace-grid"><aside class="pane"><div class="pane-head"><span>PROJECTS</span></div><div id="buildProjects" class="pane-list"></div></aside><section class="pane editor"><div class="pane-head"><span id="buildFileName">No file selected</span><button class="btn" id="buildSave" type="button" disabled>SAVE</button></div><textarea id="buildEditor" spellcheck="false" disabled placeholder="Select a text file…"></textarea><div class="editor-foot"><span id="buildMeta">Policy-bound workspace files</span><span id="buildDirty"></span></div></section><aside class="pane"><div class="pane-head"><span>HERMUS CONTEXT</span></div><div id="buildContext" class="context"></div></aside></div></div><div id="projectCreateHost"></div>';
      host.dataset.ready='1';
    }
    await refreshBuild();
  }
  function projectForm() {
    const host=qs('#projectCreateHost');if(!host)return;
    host.innerHTML='<div class="card form-card" style="margin-top:12px"><div class="card-head"><div><strong>NEW WORKSPACE PROJECT</strong><small>Creates the project through the canonical Workspace owner.</small></div></div><div class="card-body"><div class="form-grid"><label class="field">NAME<input id="projectName" placeholder="my-project"></label><label class="field full">DESCRIPTION<textarea id="projectDescription" placeholder="What is this workspace for?"></textarea></label></div><div class="actions"><button class="btn primary" type="button" data-project-create>CREATE PROJECT</button><button class="btn" type="button" data-project-form-close>CLOSE</button></div><div id="projectCreateResult" class="result">Not submitted.</div></div></div>';
    host.scrollIntoView({behavior:'smooth',block:'nearest'});
  }
  async function createProject() {
    const name=qs('#projectName')?.value.trim();
    if(!name){toast('Project name is required',true);return;}
    try{
      const out=await api('/workspace/create',{method:'POST',body:JSON.stringify({name,description:qs('#projectDescription')?.value.trim()||''})});
      qs('#projectCreateResult').textContent=out.success===false?'Create failed · '+(out.error||'unknown'):'Project created successfully.';
      toast('Workspace created'); await refreshBuild();
    }catch(e){qs('#projectCreateResult').textContent='Create failed · '+e.message;toast(e.message,true);}
  }
  async function refreshBuild() {
    const ws=await safe('/workspace'), hinted=state.project||ws?.current||null;
    const query=hinted?'?project='+encodeURIComponent(hinted):'';
    const snap=await safe('/workshop/snapshot'+query);
    state.projects=Array.isArray(snap?.projects)?snap.projects:(Array.isArray(ws?.projects)?ws.projects:[]);
    state.project=snap?.current||snap?.project||hinted||null;
    qs('#buildProjectTitle').textContent=state.project||'No project selected';
    qs('#buildProjects').innerHTML=state.projects.length?state.projects.map(p=>'<button type="button" class="tree-item '+(p.name===state.project?'active':'')+'" data-project="'+esc(p.name)+'">◇ '+esc(p.name)+'</button>').join(''):'<div class="empty">No workspace projects are available.</div>';
    if(!snap||failed(snap)){
      qs('#buildContext').innerHTML='<div class="empty">'+esc(errText(snap)||'Select a workspace project to load its files.')+'</div>'; return;
    }
    const ctx=await safe('/context?project='+encodeURIComponent(state.project||'')+'&user_id=default&memory_limit=4');
    qs('#buildContext').innerHTML=[
      ['STATE',ctx?.summary?.state||'idle'],['ATTENTION',Array.isArray(ctx?.attention)?ctx.attention.length+' active':'0 active'],
      ['PROJECT',state.project||'none'],['GOALS',Array.isArray(ctx?.goals)?ctx.goals.map(g=>g.title||g.goal).slice(0,3).join(' · ')||'none':'none']
    ].map(x=>'<div class="context-row"><b>'+esc(x[0])+'</b><span>'+esc(x[1])+'</span></div>').join('');
    const files=Array.isArray(snap.tree)?snap.tree:[];
    if(state.project) qs('#buildProjects').insertAdjacentHTML('beforeend','<div class="eyebrow" style="padding:14px 7px 6px">FILES</div><div id="buildFiles"></div>');
    const fh=qs('#buildFiles');if(fh)fh.innerHTML=files.map(f=>f.type==='file'?'<button type="button" class="tree-item" data-file="'+esc(f.path)+'">· '+esc(f.name)+'</button>':'<div class="tree-item">▸ '+esc(f.name)+'</div>').join('')||'<div class="empty">Project is empty.</div>';
  }
  async function selectProject(name) {
    try{
      const out=await api('/workshop/project/use',{method:'POST',body:JSON.stringify({name})});
      if(!out.success)throw new Error(out.error||'project switch failed');
      state.project=name;state.file=null;qs('#buildEditor').disabled=true;qs('#buildSave').disabled=true;
      await refreshBuild();toast('Workspace · '+name);
    }catch(e){toast(e.message,true);}
  }
  async function openFile(path) {
    if(!path||!state.project)return;
    try{
      const d=await api('/workshop/file?path='+encodeURIComponent(path)+'&project='+encodeURIComponent(state.project));
      if(d.editable===false){toast('File is read-only',true);return;}
      state.file=path;qs('#buildFileName').textContent=path;qs('#buildEditor').disabled=false;qs('#buildSave').disabled=false;
      qs('#buildEditor').value=String(d.content||'');qs('#buildMeta').textContent=String(d.size??0)+' bytes · editable';qs('#buildDirty').textContent='';
    }catch(e){toast(e.message,true);}
  }
  async function saveFile() {
    if(!state.file||!state.project)return;
    try{
      await api('/workshop/file',{method:'PUT',body:JSON.stringify({project:state.project,path:state.file,content:qs('#buildEditor').value})});
      qs('#buildDirty').textContent='SAVED';toast('File saved');
    }catch(e){toast(e.message,true);}
  }

  async function loadComputer() {
    const data=await safe('/computer/status'),host=qs('#computerHost');if(!host)return;
    if(failed(data)){host.innerHTML=errorCard(errText(data));return;}
    const c=data.control||{}, stats=data.task_stats||{};
    const current=data.current_task;
    const tasks=Array.isArray(data.tasks)?data.tasks:[];
    host.innerHTML='<div class="grid cols-4">'+
      stat('CONTROL',data.halted?'HALTED':(data.active?'ENABLED':'DISABLED'),'computer action gate')+
      stat('RUNNING',String(stats.running||0),'active desktop tasks')+
      stat('SUCCESS',String(stats.success||0),'completed tasks')+
      stat('SKILLS',String(data.skills?.stats?.count||0),'learned procedures')+
      '</div>'+
      '<div class="grid cols-2"><div class="card form-card"><div class="card-head"><div><strong>START COMPUTER TASK</strong><small>Dry-run is the safe default; real execution still passes through permissions.</small></div></div><div class="card-body"><label class="field">TASK<textarea id="computerTask" placeholder="Open the browser and inspect the current project page."></textarea></label><label class="check-row"><input id="computerDryRun" type="checkbox" checked><span>Plan / dry-run only</span></label><div class="actions"><button class="btn primary" type="button" data-computer-run>START</button><button class="btn danger" type="button" data-computer-stop>EMERGENCY STOP</button><button class="btn" type="button" data-computer-release>RELEASE STOP</button></div><div id="computerResult" class="result">Ready.</div></div></div>'+
      '<div class="card"><div class="card-head"><div><strong>CURRENT STATE</strong><small>Live world and task control</small></div><button class="btn" type="button" data-computer-refresh>REFRESH</button></div><div class="card-body">'+
      '<div class="context">'+[
        ['TASK',current?.task||'none'],['STATUS',current?.status||'idle'],['WINDOW',current?.world?.active_window||current?.world?.active_application||'unknown'],
        ['CONFIDENCE',current?.confidence!=null?Math.round(Number(current.confidence)*100)+'%':'—'],['CONTROL',c.state||c.status||'available']
      ].map(x=>'<div class="context-row"><b>'+esc(x[0])+'</b><span>'+esc(x[1])+'</span></div>').join('')+'</div></div></div>'+
      '<div class="card" style="margin-top:12px"><div class="card-head"><div><strong>TASK HISTORY</strong><small>Pause, resume or inspect persisted work.</small></div></div><div class="card-body list">'+(tasks.length?tasks.slice(0,12).map(t=>{
        const s=String(t.status||'created').toLowerCase(), tid=t.task_id||t.id||'';
        return '<div class="row"><div class="row-main"><strong>'+esc(t.task||tid)+'</strong><small>'+esc(s)+' · '+esc(String(t.progress??0))+'% · '+esc(tid)+'</small></div><div class="row-actions">'+
        (['running','paused','interrupted'].includes(s)?'<button class="btn" type="button" data-computer-pause="'+esc(tid)+'">PAUSE</button>':'')+
        (['paused','interrupted'].includes(s)?'<button class="btn primary" type="button" data-computer-resume="'+esc(tid)+'">RESUME</button>':'')+
        (['running','paused','interrupted'].includes(s)?'<button class="btn danger" type="button" data-computer-cancel="'+esc(tid)+'">CANCEL</button>':'')+
        '</div></div>';
      }).join(''):'<div class="empty">No computer tasks yet.</div>')+'</div></div>';
  }
  async function computerRun() {
    const task=qs('#computerTask')?.value.trim();if(!task)return;
    try{
      const dry=qs('#computerDryRun')?.checked!==false;
      const d=await api('/computer/run',{method:'POST',body:JSON.stringify({task,dry_run:dry})});
      qs('#computerResult').textContent='Started · '+(d.task_id||'background task')+(dry?' · dry-run':' · LIVE CONTROL');
      toast('Computer task started');loadComputer();
    }catch(e){qs('#computerResult').textContent='Start failed · '+e.message;toast(e.message,true);}
  }

  function renderSettingsTab(tab) {
    const host=qs('#settingsHost');if(!host)return;
    qsa('[data-settings-tab]').forEach(b=>{
      const active=b.dataset.settingsTab===tab;b.classList.toggle('active',active);b.setAttribute('aria-selected',active?'true':'false');
    });
    const d=state.settingsData;
    const link = (view, label) => '<button class="btn" type="button" data-view="'+view+'">'+label+'</button>';
    let html='';
    if(tab==='chat'){
      const models=Array.isArray(state.modelCatalog)?state.modelCatalog:[];
      const current=state.settings.chat_model||'auto';
      const font=Number(state.settings.chat_font_size||8);
      const modelOptions='<option value="auto">AUTO · use role routing</option>'+models.map(m =>
        '<option value="'+esc(m.ref||m.id||'')+'" '+((m.ref||m.id||'')===current?'selected':'')+'>'+esc(m.name||m.id||m.ref||'Deployment')+'</option>'
      ).join('');
      html='<div class="grid cols-2">'+
        '<div class="card form-card"><div class="card-head"><div><strong>CHAT MODEL</strong><small>Choose the model used for direct Chat messages. AUTO follows the default role routing.</small></div><button class="btn" type="button" data-chat-model-refresh>SYNC</button></div><div class="card-body">'+
        '<label class="field">MODEL<select id="chatModelSelect">'+modelOptions+'</select></label>'+
        '<div id="chatModelResult" class="result">'+(current==='auto'?'Using default role routing.':'Chat is pinned to '+esc(current)+'.')+'</div></div></div>'+
        '<div class="card form-card"><div class="card-head"><div><strong>CHAT FONT SIZE</strong><small>Adjust message readability without changing the rest of the dashboard.</small></div><span id="chatFontSizeValue" class="badge good">'+esc(String(font))+' PX</span></div><div class="card-body">'+
        '<input id="chatFontSizeRange" class="settings-range" type="range" min="8" max="16" step="1" value="'+esc(String(font))+'" aria-label="Chat font size">'+
        '<div class="range-labels"><span>8 PX</span><span>16 PX</span></div>'+
        '<div class="result">Preview updates immediately and is saved locally for this dashboard.</div></div></div></div>'+
        '<div class="card" style="margin-top:12px"><div class="card-head"><div><strong>CHAT BEHAVIOR</strong><small>Hermes-style live activity stays visible while the final assistant response replaces the temporary status.</small></div></div><div class="card-body list">'+
        rowValue('Live model status','Enabled','shows connection / thinking state')+
        rowValue('Tool activity','Enabled','shows tool start/result rows during a run')+
        rowValue('Reasoning text','Hidden','the UI never exposes private chain-of-thought')+
        '</div></div>';
      loadModels().catch(()=>{});
    } else if(tab==='general'){
      const health=d.health, ready=d.ready;
      html='<div class="grid cols-2">'+
        '<div class="card"><div class="card-head"><div><strong>DASHBOARD</strong><small>Local presentation settings only.</small></div></div><div class="card-body list">'+
        settingRow('Ambient motion','Subtle JARVIS animation', 'motion', state.settings.motion!==false)+
        settingRow('Compact information','Denser spacing for long sessions','compact',!!state.settings.compact)+
        settingRow('Confirm risky actions','Extra confirmation before local control changes','confirm',state.settings.confirm!==false)+
        '</div></div>'+
        '<div class="card"><div class="card-head"><div><strong>READINESS</strong><small>Live liveness and readiness probes.</small></div><button class="btn" type="button" data-settings-refresh>REFRESH</button></div><div class="card-body list">'+
        rowValue('Gateway',failed(ready)?'Unavailable':valueSummary(ready),'readiness')+
        rowValue('Capability health',failed(health)?'Unavailable':valueSummary(health),'system health')+
        rowValue('Session',localStorage.getItem('hermus_session_id')||'new','web session')+
        '</div></div></div>'+
        '<div class="card" style="margin-top:12px"><div class="card-head"><div><strong>DESKTOP APP BEHAVIOR</strong><small>Useful shortcuts without replacing normal navigation.</small></div></div><div class="card-body"><div class="shortcut-grid"><div><kbd>Ctrl / ⌘ K</kbd><span>Command palette</span></div><div><kbd>Esc</kbd><span>Close palette</span></div><div><kbd>Enter</kbd><span>Send command from top rail</span></div><div><kbd>Shift + Enter</kbd><span>New line in Chat</span></div></div></div></div>';
    } else if(tab==='models'){
      const sels=d.selected?.selections||state.selectedModels||{};
      const health=d.modelHealth;
      html='<div class="grid cols-2"><div class="card"><div class="card-head"><div><strong>ROLE ROUTING</strong><small>Persisted preferences from the model gateway.</small></div>'+link('models','OPEN MODEL HUB')+'</div><div class="card-body list">'+
        Object.entries(sels).map(([k,v])=>rowValue(k,String(v),'selected deployment')).join('') || '<div class="empty">No explicit role selections. AUTO is active.</div>'+
        '</div></div><div class="card"><div class="card-head"><div><strong>MODEL HEALTH</strong><small>Provider circuits and runtime telemetry.</small></div><button class="btn" type="button" data-model-settings-sync>SYNC</button></div><div class="card-body">'+
        renderCollectionSummary(health,'No model health payload returned.')+'</div></div></div>';
    } else if(tab==='providers'){
      const p=d.providers,k=d.keys;
      html='<div class="grid cols-2"><div class="card"><div class="card-head"><div><strong>PROVIDERS</strong><small>Known vs configured vs usable.</small></div><button class="btn" type="button" data-provider-refresh>SYNC</button></div><div class="card-body list">'+
        (Array.isArray(p?.providers)?p.providers.map(x=>rowValue(x.name||x.id||x.provider||'Provider',valueSummary(x.usable??x.available??x.configured??x.ok),'provider state')).join(''):'<div class="empty">'+esc(errText(p)||'No providers returned.')+'</div>')+
        '</div></div><div class="card"><div class="card-head"><div><strong>API KEYS</strong><small>Test credentials and filter the working ones before using them for routing.</small></div><button class="btn primary" type="button" data-key-auto>DISCOVER FREE</button></div><div class="card-body" id="apiKeysHost">'+renderKeyGroups(k?.llm_keys)+'</div></div></div>'+
        '<div class="card form-card" style="margin-top:12px"><div class="card-head"><div><strong>ADD PROVIDER KEY</strong><small>Stored by the canonical multi-key manager; key material is never echoed back into the UI.</small></div></div><div class="card-body"><div class="form-grid"><label class="field">PROVIDER<input id="keyProvider" placeholder="ollama / groq / openai-compatible"></label><label class="field">API KEY<input id="keyValue" type="password" placeholder="••••••••"></label><label class="field">BASE URL<input id="keyBaseUrl" placeholder="http://127.0.0.1:11434"></label><label class="field">DEFAULT MODEL<input id="keyModel" placeholder="optional"></label></div><div class="actions"><button class="btn primary" type="button" data-key-add>ADD KEY</button></div><div id="keyResult" class="result">Not submitted.</div></div></div>';
    } else if(tab==='agents'){
      html='<div class="grid cols-3">'+stat('REGISTERED',String(Array.isArray(d.agents?.agents)?d.agents.agents.length:state.agents.length),'agent manager')+
        stat('RUNNING',String((d.agents?.agents||[]).filter(a=>['running','working','active'].includes(String(a.state||a.status||'').toLowerCase())).length),'active workers')+
        stat('CURRENT MODEL',String(d.selected?.selections?.default||'auto'),'default role')+'</div>'+
        '<div class="card" style="margin-top:12px"><div class="card-body actions">'+link('agents','OPEN AGENT FABRIC')+link('models','OPEN MODEL HUB')+'</div></div>';
    } else if(tab==='safety'){
      const pending=d.pending?.pending||[];
      html='<div class="grid cols-2"><div class="card"><div class="card-head"><div><strong>PENDING APPROVALS</strong><small>Actions waiting for an explicit decision.</small></div><button class="btn" type="button" data-safety-refresh>REFRESH</button></div><div class="card-body list">'+
        (Array.isArray(pending)&&pending.length?pending.map(x=>{
          const id=x.id||x.approval_id||'';return '<div class="row"><div class="row-main"><strong>'+esc(x.title||x.tool||'Approval request')+'</strong><small>'+esc(x.purpose||x.reason||x.risk||'Review before continuing.')+(x.mission_id?' · mission '+esc(x.mission_id):'')+'</small></div><div class="row-actions"><button class="btn primary" type="button" data-approval="'+esc(id)+'" data-decision="approve">APPROVE</button><button class="btn danger" type="button" data-approval="'+esc(id)+'" data-decision="deny">DENY</button></div></div>';
        }).join(''):'<div class="empty">No pending approvals.</div>')+
        '</div></div><div class="card"><div class="card-head"><div><strong>RED-LINE POLICY</strong><small>Runtime safety remains authoritative.</small></div></div><div class="card-body">'+renderPolicy(d.policy)+'</div></div></div>'+
        '<div class="card form-card" style="margin-top:12px"><div class="card-head"><div><strong>TOOL POLICY OVERRIDE</strong><small>Use for an explicit allow / ask / deny decision on one tool; changes are persisted by the permission manager.</small></div></div><div class="card-body"><div class="form-grid"><label class="field">TOOL<input id="policyTool" placeholder="computer_task"></label><label class="field">DECISION<select id="policyDecision"><option value="ask">Ask</option><option value="allow">Allow</option><option value="deny">Deny</option></select></label><label class="field">AGENT (OPTIONAL)<input id="policyAgent" placeholder="agent name"></label></div><div class="actions"><button class="btn primary" type="button" data-policy-save>SET POLICY</button><button class="btn" type="button" data-view="missions">OPEN MISSIONS</button></div><div id="policyResult" class="result">Not submitted.</div></div></div>';
    } else if(tab==='voice'){
      html='<div class="grid cols-3">'+
        summaryCard('SPEECH',d.speech,'/speech/status')+summaryCard('TRANSCRIPTION',d.transcription,'local STT')+summaryCard('AVATAR',d.avatar,'optional connector')+
        '</div><div class="card form-card" style="margin-top:12px"><div class="card-head"><div><strong>VOICE TEST</strong><small>Use the active local speech backend and return a playable clip.</small></div></div><div class="card-body"><div class="toolbar"><label class="field grow">TEXT<input id="voiceTestText" value="HERMUS voice test ready." placeholder="Say something"></label><button class="btn primary" type="button" data-voice-test>SYNTHESIZE</button></div><div id="voiceTestResult" class="result">No audio generated.</div></div></div>';
    } else if(tab==='computer'){
      const c=d.computer;
      html='<div class="grid cols-4">'+stat('CONTROL',c?.halted?'HALTED':(c?.active?'ENABLED':'DISABLED'),'action gate')+stat('RUNNING',String(c?.task_stats?.running||0),'desktop tasks')+stat('TASKS',String(c?.task_stats?.total||0),'persisted history')+stat('REPAIR RATE',c?.repair_stats?.success_rate!=null?c.repair_stats.success_rate+'%':'—','recorded repairs')+'</div>'+
        '<div class="card" style="margin-top:12px"><div class="card-body actions">'+link('computer','OPEN COMPUTER WORKSPACE')+'<button class="btn danger" type="button" data-settings-computer-stop>EMERGENCY STOP</button><button class="btn" type="button" data-settings-computer-release>RELEASE STOP</button></div></div>'+
        '<div class="card" style="margin-top:12px"><div class="card-head"><div><strong>CONTROL STATE</strong><small>Desktop backend availability and current world.</small></div></div><div class="card-body">'+renderCollectionSummary(c?.control||c,'No computer control state.')+'</div></div>';
    } else if(tab==='memory'){
      html='<div class="grid cols-3">'+summaryCard('EMBEDDINGS',d.embeddings,'semantic backend')+summaryCard('LEARNING',d.learning,'learning records')+summaryCard('WORKSPACE CONTEXT',d.workspace,'project context')+'</div>'+
        '<div class="card" style="margin-top:12px"><div class="card-body actions">'+link('knowledge','OPEN KNOWLEDGE')+link('build','OPEN WORKSHOP')+'</div></div>';
    } else if(tab==='workspace'){
      const ws=d.workspace;
      html='<div class="grid cols-2"><div class="card"><div class="card-head"><div><strong>ACTIVE WORKSPACE</strong><small>Canonical workspace owner.</small></div><button class="btn" type="button" data-workspace-refresh>SYNC</button></div><div class="card-body list">'+
        rowValue('Current',ws?.current||'none','active project')+rowValue('Root',ws?.base_dir||'unknown','workspace root')+rowValue('Projects',Array.isArray(ws?.projects)?ws.projects.length:'—','available projects')+
        '</div></div><div class="card form-card"><div class="card-head"><div><strong>CREATE PROJECT</strong><small>Fast path for a new HERMUS workspace.</small></div></div><div class="card-body"><div class="form-grid"><label class="field">NAME<input id="settingsProjectName" placeholder="new-project"></label><label class="field full">DESCRIPTION<textarea id="settingsProjectDescription"></textarea></label></div><button class="btn primary" type="button" data-settings-project-create>CREATE</button><div id="settingsProjectResult" class="result">Not submitted.</div></div></div></div>';
    } else if(tab==='integrations'){
      const mcp=d.mcp, plugins=d.plugins, custom=d.custom;
      html='<div class="grid cols-3">'+stat('MCP SERVERS',String(Array.isArray(mcp?.servers)?mcp.servers.length:0),'configured connectors')+stat('PLUGINS',String(Array.isArray(plugins?.plugins)?plugins.plugins.length:0),'loaded plugins')+stat('CUSTOM APIS',String(custom?.count??0),'saved API definitions')+'</div>'+
        '<div class="grid cols-2" style="margin-top:12px"><div class="card"><div class="card-head"><div><strong>MCP</strong><small>Connect enabled servers through the canonical manager.</small></div><button class="btn primary" type="button" data-mcp-connect>CONNECT ENABLED</button></div><div class="card-body list">'+(Array.isArray(mcp?.servers)&&mcp.servers.length?mcp.servers.map(s=>rowValue(s.name||s.id||'server',valueSummary(s.connected??s.enabled??true),'server state')).join(''):'<div class="empty">No MCP servers configured.</div>')+'</div></div>'+
        '<div class="card"><div class="card-head"><div><strong>PLUGINS</strong><small>Runtime-registered integrations and tools.</small></div><button class="btn" type="button" data-plugins-reload>RELOAD</button></div><div class="card-body list">'+(Array.isArray(plugins?.plugins)&&plugins.plugins.length?plugins.plugins.map(p=>rowValue(p.name||p.id||'plugin',valueSummary(p.enabled??true),'plugin state')).join(''):'<div class="empty">No plugins loaded.</div>')+'</div></div></div>';
    } else if(tab==='runtime'){
      const e=d.engine, doc=d.doctor;
      html='<div class="grid cols-2"><div class="card"><div class="card-head"><div><strong>LOCAL ENGINE</strong><small>Hardware, routing and local model runtime.</small></div><button class="btn" type="button" data-engine-refresh>REFRESH</button></div><div class="card-body">'+renderEngine(e)+'</div></div>'+
        '<div class="card"><div class="card-head"><div><strong>HERMUS DOCTOR</strong><small>Diagnose runtime problems before changing anything.</small></div><button class="btn primary" type="button" data-doctor-run>RUN DOCTOR</button></div><div class="card-body">'+renderDoctor(doc)+'</div></div></div>'+
        '<div class="card" style="margin-top:12px"><div class="card-body actions"><button class="btn primary" type="button" data-engine-start>START LOCAL ENGINE</button><button class="btn danger" type="button" data-engine-stop>STOP LOCAL ENGINE</button><button class="btn" type="button" data-engine-install>INSTALL ENGINE</button></div><div id="runtimeActionResult" class="card-body compact-result">Ready.</div></div>';
    } else if(tab==='updates'){
      html='<div class="grid cols-3">'+summaryCard('LOCAL',d.updateLocal,'commit')+summaryCard('REMOTE',d.updateRemote,'commit')+summaryCard('UPDATE CHECK',d.updateCheck,'availability')+'</div>'+
        '<div class="card" style="margin-top:12px"><div class="card-head"><div><strong>UPDATE CONTROL</strong><small>Git pull and environment update are real system mutations.</small></div></div><div class="card-body actions"><button class="btn" type="button" data-update-check>CHECK FOR UPDATES</button><button class="btn danger" type="button" data-update-pull>UPDATE HERMUS</button></div><div id="updateResult" class="card-body compact-result">No update action requested.</div></div>';
    }
    host.innerHTML=html||'<div class="empty">Section unavailable.</div>';
  }

  function initSettingsA11y() {
    const tablist=qs('#settingsTabs'), panel=qs('#settingsHost'), search=qs('#settingsSearch');
    if(tablist) tablist.setAttribute('role','tablist');
    qsa('[data-settings-tab]').forEach((b,i)=>{
      b.setAttribute('role','tab');
      b.setAttribute('aria-controls','settingsHost');
      b.setAttribute('aria-selected',i===0?'true':'false');
      b.setAttribute('tabindex',i===0?'0':'-1');
      b.addEventListener('keydown',e=>{
        if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) return;
        e.preventDefault();
        const tabs=qsa('[data-settings-tab]');
        const idx=tabs.indexOf(b);
        const next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(idx+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
        tabs[next].focus();
        openSettingsTab(tabs[next].dataset.settingsTab);
      });
    });
    if(panel){panel.setAttribute('role','tabpanel');panel.setAttribute('tabindex','-1');}
    if(search) search.addEventListener('input',()=>{
      const q=search.value.trim().toLowerCase();
      qsa('[data-settings-tab]').forEach(b=>{
        const ok=!q||b.textContent.toLowerCase().includes(q);
        b.hidden=!ok;
      });
    });
  }

  function trapPaletteFocus(e) {
    const p=qs('#commandPalette');
    if(!p?.classList.contains('open')||e.key!=='Tab') return;
    const focusables=qsa('button,input,[href],[tabindex]:not([tabindex="-1"])',p).filter(x=>!x.disabled&&!x.hidden&&x.offsetParent!==null);
    if(!focusables.length) return;
    const first=focusables[0],last=focusables[focusables.length-1];
    if(e.shiftKey && document.activeElement===first){e.preventDefault();last.focus();}
    else if(!e.shiftKey && document.activeElement===last){e.preventDefault();first.focus();}
  }

  function settingRow(title, desc, key, enabled) {
    return '<div class="row"><div class="row-main"><strong>'+esc(title)+'</strong><small>'+esc(desc)+'</small></div><button class="btn" type="button" data-setting="'+esc(key)+'">'+(enabled?'ON':'OFF')+'</button></div>';
  }
  function rowValue(label,value,detail) {
    return '<div class="row"><div class="row-main"><strong>'+esc(label)+'</strong><small>'+esc(detail)+'</small></div>'+badge(value||'—',String(value||'').toLowerCase().includes('ready')?'good':'')+'</div>';
  }
  function summaryCard(title,data,detail) {
    return '<div class="card"><div class="card-head"><div><strong>'+esc(title)+'</strong><small>'+esc(detail)+'</small></div></div><div class="card-body">'+
      (failed(data)?'<div class="empty">'+esc(errText(data))+'</div>':'<div class="summary-value">'+esc(valueSummary(data?.status||data?.backend||data?.state||data?.ready||data?.available||data?.count||'READY'))+'</div>')+'</div></div>';
  }
  function renderCollectionSummary(data,fallback) {
    if (!data) return '<div class="empty">'+esc(fallback)+'</div>';
    if (failed(data)) return errorCard(errText(data));
    return '<div class="list">'+Object.entries(data).slice(0,24).map(([k,v])=>rowValue(k,valueSummary(v),'runtime field')).join('')+'</div>';
  }
  function renderPolicy(p) {
    if(!p||failed(p)) return '<div class="empty">'+esc(errText(p)||'No policy payload returned.')+'</div>';
    return '<div class="list"><div class="row"><div class="row-main"><strong>'+esc(p.name||'Safety policy')+'</strong><small>'+esc(p.summary||'Runtime safety remains authoritative.')+'</small></div>'+badge(p.version||'ACTIVE','good')+'</div>'+
      '<div class="policy-zones">'+Object.entries(p.zones||{}).slice(0,12).map(([k,v])=>'<div class="policy-zone"><b>'+esc(k)+'</b><span>'+esc(valueSummary(v))+'</span></div>').join('')+'</div></div>';
  }
  function renderEngine(e) {
    if(!e||failed(e)) return errorCard(errText(e)||'Engine status unavailable.');
    return '<div class="list">'+rowValue('Status',valueSummary(e.status||e.state||e.ready),'engine')+rowValue('Device',valueSummary(e.device||e.gpu||e.accelerator),'hardware')+rowValue('Recommendation',valueSummary(e.recommended||e.plan?.recommendation),'routing')+rowValue('Missing',Array.isArray(e.missing)?e.missing.length+' items':'none','setup')+'</div>';
  }
  function renderDoctor(d) {
    if(!d||failed(d)) return errorCard(errText(d)||'Doctor status unavailable.');
    return '<div class="list">'+rowValue('Status',valueSummary(d.status||d.state||d.ready),'doctor')+rowValue('Findings',Array.isArray(d.findings)?d.findings.length:'0','known signals')+rowValue('Last report',valueSummary(d.last_report||d.recent),'history')+'</div>';
  }
  function renderKeyGroups(groups) {
    if(!groups||failed(groups)) return '<div class="empty">'+esc(errText(groups)||'No provider keys configured.')+'</div>';
    const entries=Object.entries(groups).filter(([,list])=>(Array.isArray(list)?list:[list]).length);
    if(!entries.length) return '<div class="empty">No API keys configured.</div>';

    const rows=[];
    for(const [provider,list] of entries){
      const arr=Array.isArray(list)?list:[list];
      for(const k of arr){
        const id=k?.name||k?.id||k?.key||'key';
        const preview=k?.preview||k?.key_preview||'configured';
        const health=String(k?.health_status||'').toLowerCase();
        const working=k?.healthy===true||health==='healthy'||health==='ok'||health==='working';
        const failedKey=k?.healthy===false||['failed','auth_failed','rate_limited','unhealthy'].includes(health);
        const state=working?'working':failedKey?'failed':'untested';
        rows.push({provider,k,id,preview,state});
      }
    }

    return '<div class="key-toolbar">'+
      '<label class="field grow">FILTER API KEYS<select id="keyStatusFilter" aria-label="Filter API keys">'+
      '<option value="all">ALL · '+rows.length+'</option>'+
      '<option value="working">WORKING · '+rows.filter(x=>x.state==='working').length+'</option>'+
      '<option value="failed">NOT WORKING · '+rows.filter(x=>x.state==='failed').length+'</option>'+
      '<option value="untested">UNTESTED · '+rows.filter(x=>x.state==='untested').length+'</option>'+
      '</select></label>'+
      '<button class="btn primary" type="button" data-key-test-all>TEST ALL KEYS</button>'+
      '</div>'+
      '<div id="keyGroupsHost">'+renderFilteredKeyGroups(rows,'all')+'</div>';
  }

  function renderFilteredKeyGroups(rows,filter) {
    const selected=filter||qs('#keyStatusFilter')?.value||'all';
    const visible=rows.filter(x=>selected==='all'||x.state===selected);
    if(!visible.length) return '<div class="empty">No API keys match this filter.</div>';
    const grouped=new Map();
    for(const x of visible){
      if(!grouped.has(x.provider)) grouped.set(x.provider,[]);
      grouped.get(x.provider).push(x);
    }
    return '<div class="list">'+[...grouped.entries()].map(([provider,items])=>
      '<div class="key-group"><div class="eyebrow">'+esc(provider)+' · '+items.length+'</div>'+
      items.map(({k,id,preview,state})=>{
        const statusLabel=state==='working'?'WORKING':state==='failed'?'NOT WORKING':'UNTESTED';
        const statusKind=state==='working'?'good':state==='failed'?'bad':'warn';
        const meta=[preview,k?.default_model?'model · '+k.default_model:'',k?.models_count!=null?k.models_count+' models':''].filter(Boolean).join(' · ');
        return '<div class="row key-row" data-key-state="'+state+'">'+
          '<div class="row-main"><strong>'+esc(id)+'</strong><small>'+esc(meta||'credential stored')+'</small></div>'+
          badge(statusLabel,statusKind)+
          '<div class="row-actions"><button class="btn" type="button" data-key-test data-key-provider="'+esc(provider)+'" data-key-name="'+esc(id)+'">TEST</button>'+
          '<button class="btn danger" type="button" data-key-remove="'+esc(id)+'" data-key-provider="'+esc(provider)+'">REMOVE</button>'+
          '<span class="compact-result" data-key-result="'+esc(provider)+'/'+esc(id)+'"></span></div></div>';
      }).join('')+'</div>'
    ).join('')+'</div>';
  }

  async function loadSettings() {
    const endpoints=[
      ['health','/api/v1/system/health'],['ready','/readyz'],['selected','/models/selected'],['modelHealth','/models/health'],
      ['providers','/providers/available'],['keys','/keys/list'],['agents','/agents'],['pending','/permissions/pending'],
      ['policy','/red-lines/policy'],['speech','/speech/status'],['transcription','/speech/transcription/status'],['avatar','/speech/avatar/status'],
      ['computer','/computer/status'],['embeddings','/embeddings/status'],['learning','/learning?limit=20'],['workspace','/workspace'],
      ['mcp','/mcp/servers'],['plugins','/plugins'],['custom','/custom-apis/list'],['engine','/engine/status?probe=true'],
      ['doctor','/doctor/status'],['updateLocal','/update/local'],['updateRemote','/update/remote'],['updateCheck','/update/check']
    ];
    const values=await Promise.all(endpoints.map(async ([k,url])=>[k,await safe(url)]));
    state.settingsData=Object.fromEntries(values);
    renderSettingsTab(state.settingsTab);
    setOnline(!failed(state.settingsData.ready) || !failed(state.settingsData.health));
  }

  async function createKey() {
    const provider=qs('#keyProvider')?.value.trim(), key=qs('#keyValue')?.value, base_url=qs('#keyBaseUrl')?.value.trim(), model=qs('#keyModel')?.value.trim();
    if(!provider){toast('Provider is required',true);return;}
    if(!key&& !['ollama','lmstudio'].includes(provider.toLowerCase())){toast('API key is required for this provider',true);return;}
    try{
      const out=await api('/keys/add',{method:'POST',body:JSON.stringify({provider,key:key||'',base_url:base_url||undefined,model:model||undefined,auto_discover:true})});
      qs('#keyResult').textContent=out.success===false?'Add failed · '+(out.error||'unknown'):'Provider key saved and discovery requested.';
      qs('#keyValue').value='';
      toast('Provider saved');
      await loadSettings();
      await loadModels({refresh:true,probe:true});
    }catch(e){qs('#keyResult').textContent='Add failed · '+e.message;toast(e.message,true);}
  }
  function renderKeyGroupsFilteredFromPayload(groups,filter='all'){
    if(!groups||failed(groups)) return '<div class="empty">'+esc(errText(groups)||'No API keys configured.')+'</div>';
    const rows=[];
    for(const [provider,list] of Object.entries(groups)){
      for(const k of (Array.isArray(list)?list:[list])){
        const id=k?.name||k?.id||k?.key||'key';
        const health=String(k?.health_status||'').toLowerCase();
        const working=k?.healthy===true||health==='healthy'||health==='ok'||health==='working';
        const failedKey=k?.healthy===false||['failed','auth_failed','rate_limited','unhealthy'].includes(health);
        rows.push({provider,k,id,preview:k?.preview||k?.key_preview||'configured',state:working?'working':failedKey?'failed':'untested'});
      }
    }
    return renderFilteredKeyGroups(rows,filter);
  }

  document.addEventListener('change',e=>{
    if(!e.target.closest('#keyStatusFilter')) return;
    const host=qs('#apiKeysHost');
    const groups=state.settingsData.keys?.llm_keys;
    if(host&&groups) host.innerHTML=renderKeyGroupsFilteredFromPayload(groups,e.target.value);
  },false);

  async function testStoredKey(button){
    const provider=button.dataset.keyProvider||'',name=button.dataset.keyName||'';
    const resultNode=qs('[data-key-result="'+CSS.escape(provider+'/'+name)+'"]');
    button.disabled=true;
    button.textContent='TESTING…';
    if(resultNode) resultNode.textContent='testing…';
    try{
      const d=await api('/keys/test',{method:'POST',body:JSON.stringify({provider,name})});
      if(d.working){
        toast(provider+'/'+name+' is working');
      }else{
        toast(provider+'/'+name+' is not working',true);
      }
      await loadSettings();
      const host=qs('#apiKeysHost');
      const filter=qs('#keyStatusFilter')?.value||'all';
      const groups=state.settingsData.keys?.llm_keys;
      if(host&&groups) host.innerHTML=renderKeyGroupsFilteredFromPayload(groups,filter);
    }catch(e){
      toast('API key test failed · '+e.message,true);
      button.disabled=false;
      button.textContent='TEST';
      if(resultNode) resultNode.textContent=e.message;
    }
  }

  async function testAllStoredKeys(){
    const buttons=qsa('[data-key-test]');
    if(!buttons.length){toast('No stored API keys to test',true);return;}
    buttons.forEach(b=>{b.disabled=true;b.textContent='TESTING…';});
    for(const b of buttons.slice()){
      await testStoredKey(b);
    }
    toast('API key test pass complete');
  }

  async function removeKey(provider,id) {
    if(state.settings.confirm!==false && !confirm('Remove the configured '+provider+' credential "'+id+'"?'))return;
    try{
      await api('/keys/remove',{method:'POST',body:JSON.stringify({provider,key:id,name:id})});
      toast('Provider removed');
      await loadSettings();
      await loadModels({refresh:true,probe:false});
    }catch(e){toast(e.message,true);}
  }

  function setSetting(name) {
    state.settings[name]=!(state.settings[name]??(name==='confirm'));
    localStorage.setItem('hermus_dashboard_settings',JSON.stringify(state.settings));
    applySettings();renderSettingsTab('general');toast(name+' setting updated');
  }
  function setChatFontSize(value) {
    const size=Math.max(8,Math.min(16,Number(value)||8));
    state.settings.chat_font_size=size;
    localStorage.setItem('hermus_dashboard_settings',JSON.stringify(state.settings));
    applySettings();
    const valueNode=qs('#chatFontSizeValue');
    if(valueNode)valueNode.textContent=size+' PX';
  }
  function applySettings() {
    document.body.classList.toggle('compact-mode',!!state.settings.compact);
    document.body.classList.toggle('motion-off',state.settings.motion===false);
    const size=Math.max(8,Math.min(16,Number(state.settings.chat_font_size)||8));
    document.documentElement.style.setProperty('--chat-font-size',size+'px');
    document.documentElement.style.setProperty('--chat-meta-size',Math.max(6,size-2)+'px');
    document.documentElement.style.setProperty('--chat-activity-size',Math.max(7,size-1)+'px');
  }

  async function refreshView(key) {
    switch(key){
      case'overview':return refreshOverview();
      case'chat':return renderChat();
      case'build':return loadBuild();
      case'agents':return loadAgents();
      case'models':return loadModels();
      case'tools':return loadTools();
      case'missions':return loadMissions();
      case'knowledge':return loadKnowledge();
      case'personal':return loadPersonal();
      case'browser':return loadBrowser();
      case'terminal':toolWorkspace('terminal');break;
      case'image':toolWorkspace('image');break;
      case'video':toolWorkspace('video');break;
      case'computer':return loadComputer();
      case'space':break;
      case'settings':return loadSettings();
    }
  }
  async function loadView(key){try{await refreshView(key)}catch(e){toast(e.message,true);}}

  const paletteItems=[
    ...Object.entries(views).map(([key,v])=>({key,label:v[0],desc:v[1],group:'workspace'})),
    ...settingTabs.map(([key,label])=>({key:'settings',tab:key,label:'Settings · '+label,desc:'Control Center section',group:'control'}))
  ];
  function openPalette() {
    state.paletteFocus=document.activeElement;
    const p=qs('#commandPalette');if(!p)return;
    p.classList.add('open');p.setAttribute('aria-hidden','false');
    const i=qs('#paletteInput');i.value='';renderPalette();
    setTimeout(()=>i.focus(),0);
  }
  function closePalette() {
    const p=qs('#commandPalette');if(!p)return;
    p.classList.remove('open');p.setAttribute('aria-hidden','true');
    try{state.paletteFocus?.focus()}catch{}
  }
  function renderPalette() {
    const q=(qs('#paletteInput')?.value||'').toLowerCase(),host=qs('#paletteList');
    const rows=paletteItems.filter(x=>(x.label+' '+x.desc+' '+x.key).toLowerCase().includes(q));
    host.innerHTML=rows.map((x,i)=>'<button class="palette-item" type="button" data-palette-view="'+esc(x.key)+'" '+(x.tab?'data-palette-tab="'+esc(x.tab)+'"':'')+'><b>'+esc(x.label)+'</b><span>'+esc(x.desc)+'</span><kbd>'+(i+1)+'</kbd></button>').join('')||'<div class="empty">No workspace or control section found.</div>';
  }

  document.addEventListener('click',async e=>{
    const view=e.target.closest('[data-view]');
    if(view){
      e.preventDefault();
      const key=view.dataset.view, tab=view.dataset.settingsTabTarget;
      openView(key,{settingsTab:tab||state.settingsTab});
      if(key==='settings'&&tab)openSettingsTab(tab);
      return;
    }
    if(e.target.closest('#commandFocus')){e.preventDefault();openPalette();return;}
    if(e.target.closest('#paletteClose')||e.target===qs('#commandPalette')){closePalette();return;}
    const pv=e.target.closest('[data-palette-view]');
    if(pv){
      const key=pv.dataset.paletteView,tab=pv.dataset.paletteTab;
      closePalette();openView(key,{settingsTab:tab||state.settingsTab});
      if(key==='settings'&&tab)openSettingsTab(tab);
      return;
    }
    if(e.target.closest('[data-open-wizard]')){openWizard();return;}
    if(e.target.closest('[data-wizard-close]')||e.target===qs('#setupWizard')){closeWizard();return;}
    if(e.target.closest('[data-wizard-next]')){await wizardNext();return;}
    if(e.target.closest('[data-wizard-back]')){wizardBack();return;}
    if(e.target.closest('[data-wizard-later]')){closeWizard(false);return;}
    if(e.target.closest('[data-wizard-recheck]')||e.target.closest('[data-wizard-model-refresh]')){await wizardCheck();renderWizard();return;}
    if(e.target.closest('[data-wizard-free]')){try{await api('/keys/auto-provision-free',{method:'POST',body:'{}'});toast('Free provider discovery started');await wizardCheck();renderWizard();}catch(err){toast(err.message,true)}return;}
    if(e.target.closest('[data-wizard-add-provider]')){try{
      const provider=qs('#wizardKeyProvider')?.value.trim(),key=qs('#wizardKeyValue')?.value||'',base_url=qs('#wizardKeyBaseUrl')?.value.trim(),model=qs('#wizardKeyModel')?.value.trim();
      if(!provider){toast('Provider is required',true);return;}
      if(!key&&!['ollama','lmstudio'].includes(provider.toLowerCase())){toast('API key required for this provider',true);return;}
      const out=await api('/keys/add',{method:'POST',body:JSON.stringify({provider,key,base_url:base_url||undefined,model:model||undefined,auto_discover:true})});
      qs('#wizardProviderResult').textContent=out.success===false?'Provider setup failed · '+(out.error||'unknown'):'Provider configured. HERMUS will rediscover its models.';
      await wizardCheck();toast('Provider configured');}catch(err){qs('#wizardProviderResult').textContent='Provider setup failed · '+err.message;toast(err.message,true)}return;}
    if(e.target.closest('[data-wizard-create-project]')){try{
      const name=qs('#wizardProjectName')?.value.trim();if(!name){toast('Project name required',true);return;}
      const out=await api('/workspace/create',{method:'POST',body:JSON.stringify({name,description:qs('#wizardProjectDescription')?.value.trim()||''})});
      qs('#wizardWorkspaceResult').textContent=out.success===false?'Project creation failed · '+(out.error||'unknown'):'Project created and ready.';
      wizard.data.check.workspace=await safe('/workspace');toast('Workspace created');}catch(err){qs('#wizardWorkspaceResult').textContent='Project creation failed · '+err.message;toast(err.message,true)}return;}
    if(e.target.closest('[data-wizard-voice-test]')){try{
      const d=await api('/speech/synthesize',{method:'POST',body:JSON.stringify({text:'HERMUS voice setup is working.',session_id:localStorage.getItem('hermus_session_id')||'',user_id:'default'})});
      toast(d.audio_url?'Voice test ready':'Voice backend responded');}catch(err){toast('Voice unavailable · '+err.message,true)}return;}
    if(e.target.closest('[data-wizard-computer]')){closeWizard();openView('computer');return;}
    if(e.target.closest('[data-command]')){sendCommand(e.target.closest('[data-command]').dataset.command);return;}
    if(e.target.closest('#chatSend')){await sendChat();return;}
    if(e.target.closest('#voiceToggle')){toggleVoice();return;}
    const a=e.target.closest('[data-agent-action]');
    if(a){await agentAction(a.dataset.agent,a.dataset.agentAction);return;}
    if(e.target.closest('[data-agent-new]')){openView('agents');setTimeout(()=>qs('#agentName')?.focus(),0);return;}
    if(e.target.closest('[data-agent-create]')){await createAgent();return;}
    if(e.target.closest('[data-model-save]')){await saveModel();return;}
    if(e.target.closest('[data-model-pick]')){
      const ref=e.target.closest('[data-model-pick]').dataset.modelPick;
      const deployment=qs('#modelDeploymentInput');
      if(deployment){deployment.value=ref;await saveModel();}
      return;
    }
    if(e.target.closest('[data-model-refresh]')){await loadModels({refresh:true,probe:true});toast('Models refreshed');return;}
    if(e.target.closest('[data-model-settings-sync]')){await loadSettings();toast('Model health synced');return;}
    if(e.target.closest('[data-tool-use]')){const n=e.target.closest('[data-tool-use]').dataset.toolUse;openView('chat');setTimeout(()=>sendCommand('Use the "'+n+'" tool for the current task.'),0);return;}
    if(e.target.closest('[data-mission-new]')){openView('missions');setTimeout(()=>qs('#missionGoal')?.focus(),0);return;}
    if(e.target.closest('[data-mission-create]')){await createMission();return;}
    if(e.target.closest('[data-mission-resume]')){const id=e.target.closest('[data-mission-resume]').dataset.missionResume;try{await api('/missions/'+encodeURIComponent(id)+'/resume',{method:'POST',body:'{}'});toast('Mission resume requested');loadMissions()}catch(err){toast(err.message,true)}return;}
    if(e.target.closest('[data-mission-open]')){const id=e.target.closest('[data-mission-open]').dataset.missionOpen;const d=await safe('/missions/'+encodeURIComponent(id));const box=qs('#missionDetailHost');if(box)box.innerHTML=failed(d)?errorCard(errText(d)):'<div class="card" style="margin-top:12px"><div class="card-head"><div><strong>MISSION DETAIL</strong><small>'+esc(id)+'</small></div></div><div class="card-body">'+renderCollectionSummary(d,'No mission detail.')+'</div></div>';return;}
    if(e.target.closest('[data-routine-new]')){routineForm();return;}
    if(e.target.closest('[data-routine-save]')){await submitRoutine(false);return;}
    if(e.target.closest('[data-routine-validate]')){await submitRoutine(true);return;}
    if(e.target.closest('[data-routine-toggle]')){const b=e.target.closest('[data-routine-toggle]');try{await api('/routines/'+encodeURIComponent(b.dataset.id)+'/enable',{method:'POST',body:JSON.stringify({enabled:b.dataset.enabled==='true'})});toast('Routine updated');loadMissions()}catch(err){toast(err.message,true)}return;}
    if(e.target.closest('[data-routine-delete]')){const b=e.target.closest('[data-routine-delete]');if(state.settings.confirm!==false&&!confirm('Delete this routine?'))return;try{await api('/routines/'+encodeURIComponent(b.dataset.id),{method:'DELETE'});toast('Routine deleted');loadMissions()}catch(err){toast(err.message,true)}return;}
    if(e.target.closest('[data-knowledge-save]')){const text=qs('#knowledgeNote')?.value.trim();if(text){try{await api('/memory2/remember',{method:'POST',body:JSON.stringify({kind:'semantic',content:text,importance:5,project:state.project||null})});qs('#knowledgeNote').value='';qs('#knowledgeResult').textContent='Saved to HERMUS memory.';toast('Knowledge saved')}catch(err){toast(err.message,true)}}return;}
    if(e.target.closest('[data-personal-run]')){try{await api('/personal-space/run',{method:'POST',body:'{}'});toast('HERMUS curiosity cycle started');loadPersonal()}catch(err){toast(err.message,true)}return;}
    if(e.target.closest('[data-personal-refresh]')){loadPersonal();return;}
    if(e.target.closest('[data-proposal]')){const b=e.target.closest('[data-proposal]'),action=b.dataset.proposal,id=b.dataset.proposalId;try{await api('/personal-space/proposals/'+encodeURIComponent(id)+'/'+(action==='approve'?'approve':'dismiss'),{method:'POST',body:'{}'});toast('Proposal '+action+'d');loadPersonal()}catch(err){toast(err.message,true)}return;}
    if(e.target.closest('[data-browser-open]')){await browserOpen();return;}
    if(e.target.closest('[data-browser-analyze]')){const d=state.browserPage;if(!d){toast('Fetch a page first',true);return;}await sendCommand('Analyze this browser result and explain the important findings: '+String(d.content||d.text||'').slice(0,7000));return;}
    if(e.target.closest('[data-browser-save]')){const d=state.browserPage;if(!d){toast('Fetch a page first',true);return;}const note=String(d.title||'Web finding')+' · '+String(d.url||'')+' · '+String(d.content||d.text||'').slice(0,2500);try{await api('/memory2/remember',{method:'POST',body:JSON.stringify({kind:'semantic',content:note,importance:4,project:state.project||null})});toast('Web finding saved')}catch(err){toast(err.message,true)}return;}
    if(e.target.closest('[data-tool-run]')){await runToolWorkspace(e.target.closest('[data-tool-run]').dataset.toolRun);return;}
    if(e.target.closest('[data-build-refresh]')){await refreshBuild();return;}
    if(e.target.closest('[data-build-new]')){projectForm();return;}
    if(e.target.closest('[data-project-create]')){await createProject();return;}
    if(e.target.closest('[data-project-form-close]')){qs('#projectCreateHost').innerHTML='';return;}
    if(e.target.closest('[data-build-ask]')){openView('chat');qs('#chatInput')?.focus();return;}
    if(e.target.closest('[data-project]')){await selectProject(e.target.closest('[data-project]').dataset.project);return;}
    if(e.target.closest('[data-file]')){await openFile(e.target.closest('[data-file]').dataset.file);return;}
    if(e.target.closest('#buildSave')){await saveFile();return;}
    if(e.target.closest('[data-computer-run]')){await computerRun();return;}
    if(e.target.closest('[data-computer-stop]')||e.target.closest('[data-settings-computer-stop]')){if(state.settings.confirm!==false&&!confirm('Emergency stop all computer control?'))return;try{await api('/computer/stop',{method:'POST',body:JSON.stringify({reason:'dashboard emergency stop'})});toast('Computer control halted');loadComputer();loadSettings();}catch(err){toast(err.message,true)}return;}
    if(e.target.closest('[data-computer-release]')||e.target.closest('[data-settings-computer-release]')){try{await api('/computer/release',{method:'POST',body:'{}'});toast('Computer control released');loadComputer();loadSettings();}catch(err){toast(err.message,true)}return;}
    if(e.target.closest('[data-computer-refresh]')){await loadComputer();return;}
    if(e.target.closest('[data-computer-pause]')){const id=e.target.closest('[data-computer-pause]').dataset.computerPause;try{await api('/computer/control/pause/'+encodeURIComponent(id),{method:'POST',body:'{}'});toast('Pause requested');loadComputer()}catch(err){toast(err.message,true)}return;}
    if(e.target.closest('[data-computer-resume]')){const id=e.target.closest('[data-computer-resume]').dataset.computerResume;try{await api('/computer/control/resume/'+encodeURIComponent(id),{method:'POST',body:'{}'});toast('Computer task resumed');loadComputer()}catch(err){toast(err.message,true)}return;}
    if(e.target.closest('[data-computer-cancel]')){const id=e.target.closest('[data-computer-cancel]').dataset.computerCancel;if(state.settings.confirm!==false&&!confirm('Cancel computer task '+id+'?'))return;try{await api('/computer/control/cancel/'+encodeURIComponent(id),{method:'POST',body:'{}'});toast('Computer task cancellation requested');loadComputer()}catch(err){toast(err.message,true)}return;}
    if(e.target.closest('[data-settings-tab]')){openSettingsTab(e.target.closest('[data-settings-tab]').dataset.settingsTab);return;}
    if(e.target.closest('[data-chat-model-refresh]')){await loadModels({refresh:true,probe:true});renderSettingsTab('chat');toast('Chat models refreshed');return;}
    if(e.target.closest('#chatModelSelect')){
      const model=e.target.closest('#chatModelSelect').value||'auto';
      state.settings.chat_model=model;
      localStorage.setItem('hermus_dashboard_settings',JSON.stringify(state.settings));
      const result=qs('#chatModelResult');
      if(result)result.textContent=model==='auto'?'Using default role routing.':'Chat is pinned to '+model+'.';
      toast('Chat model updated');
      return;
    }
    if(e.target.closest('[data-setting]')){setSetting(e.target.closest('[data-setting]').dataset.setting);return;}
    if(e.target.closest('[data-settings-refresh]')||e.target.closest('[data-provider-refresh]')||e.target.closest('[data-safety-refresh]')||e.target.closest('[data-workspace-refresh]')){await loadSettings();return;}
    if(e.target.closest('[data-key-add]')){await createKey();return;}
    if(e.target.closest('#chatFontSizeRange')){setChatFontSize(e.target.closest('#chatFontSizeRange').value);return;}
    if(e.target.closest('#keyStatusFilter')) return;
    if(e.target.closest('[data-key-test]')){
      await testStoredKey(e.target.closest('[data-key-test]'));
      return;
    }
    if(e.target.closest('[data-key-test-all]')){
      await testAllStoredKeys();
      return;
    }
    if(e.target.closest('[data-key-auto]')){try{await api('/keys/auto-provision-free',{method:'POST',body:'{}'});toast('Free provider discovery started');loadSettings()}catch(err){toast(err.message,true)}return;}
    if(e.target.closest('[data-key-remove]')){await removeKey(e.target.closest('[data-key-remove]').dataset.keyProvider,e.target.closest('[data-key-remove]').dataset.keyRemove);return;}
    if(e.target.closest('[data-approval]')){const b=e.target.closest('[data-approval]');try{await api('/permissions/pending/resolve',{method:'POST',body:JSON.stringify({id:b.dataset.approval,decision:b.dataset.decision,retry:b.dataset.decision==='approve'})});toast('Approval '+b.dataset.decision+'d');loadSettings()}catch(err){toast(err.message,true)}return;}
    if(e.target.closest('[data-policy-save]')){try{await api('/permissions/set',{method:'POST',body:JSON.stringify({tool:qs('#policyTool')?.value.trim()||'',decision:qs('#policyDecision')?.value||'ask',agent:qs('#policyAgent')?.value.trim()||null})});qs('#policyResult').textContent='Policy saved.';toast('Permission policy updated');loadSettings()}catch(err){qs('#policyResult').textContent='Failed · '+err.message;toast(err.message,true)}return;}
    if(e.target.closest('[data-voice-test]')){const text=qs('#voiceTestText')?.value.trim();if(!text)return;try{const d=await api('/speech/synthesize',{method:'POST',body:JSON.stringify({text,session_id:localStorage.getItem('hermus_session_id')||'',user_id:'default'})});qs('#voiceTestResult').innerHTML='<audio controls src="'+esc(d.audio_url||'')+'"></audio><div style="margin-top:6px">Generated'+(d.backend?' · '+esc(d.backend):'')+'</div>';toast('Voice clip ready')}catch(err){qs('#voiceTestResult').textContent='Voice unavailable · '+err.message;toast(err.message,true)}return;}
    if(e.target.closest('[data-mcp-connect]')){try{await api('/mcp/connect',{method:'POST',body:'{}'});toast('MCP enabled servers connected');loadSettings()}catch(err){toast(err.message,true)}return;}
    if(e.target.closest('[data-plugins-reload]')){try{await api('/plugins/reload',{method:'POST',body:'{}'});toast('Plugins reloaded');loadSettings()}catch(err){toast(err.message,true)}return;}
    if(e.target.closest('[data-settings-project-create]')){const name=qs('#settingsProjectName')?.value.trim();if(!name){toast('Project name required',true);return;}try{await api('/workspace/create',{method:'POST',body:JSON.stringify({name,description:qs('#settingsProjectDescription')?.value.trim()||''})});qs('#settingsProjectResult').textContent='Project created successfully.';toast('Workspace created');loadSettings()}catch(err){qs('#settingsProjectResult').textContent='Failed · '+err.message;toast(err.message,true)}return;}
    if(e.target.closest('[data-engine-refresh]')){await loadSettings();return;}
    if(e.target.closest('[data-engine-start]')){await runtimeAction('/engine/nollama/start','Local engine start requested.');return;}
    if(e.target.closest('[data-engine-stop]')){await runtimeAction('/engine/nollama/stop','Local engine stopped.');return;}
    if(e.target.closest('[data-engine-install]')){await runtimeAction('/engine/nollama/install','Local engine install requested.');return;}
    if(e.target.closest('[data-doctor-run]')){try{const d=await api('/doctor/run',{method:'POST',body:JSON.stringify({use_llm:true})});toast('Doctor completed');state.settingsData.doctor=d;renderSettingsTab('runtime')}catch(err){toast(err.message,true)}return;}
    if(e.target.closest('[data-update-check]')){const d=await safe('/update/check');qs('#updateResult').textContent=failed(d)?'Check failed · '+errText(d):('Update check complete · '+valueSummary(d));return;}
    if(e.target.closest('[data-update-pull]')){if(state.settings.confirm!==false&&!confirm('Update HERMUS from the configured remote? This can modify the local environment.'))return;const d=await safe('/update/pull',{method:'POST',body:'{}'});qs('#updateResult').textContent=failed(d)?'Update failed · '+errText(d):'Update request completed. Restart HERMUS if the updater requires it.';return;}
  },false);

  async function runtimeAction(path,message){
    try{const d=await api(path,{method:'POST',body:'{}'});qs('#runtimeActionResult').textContent=message+(d?.error?' · '+d.error:'');toast(message);await loadSettings();}
    catch(e){qs('#runtimeActionResult').textContent='Failed · '+e.message;toast(e.message,true);}
  }

  async function agentAction(encoded,action) {
    const name=decodeURIComponent(encoded);
    try{
      if(action==='chat'){openView('chat');state.messages.push({who:'jarvis',text:'Agent chat ready for '+name+'.',time:'Now'});renderChat();return;}
      await api(action==='start'?'/agents/start':'/agents/stop',{method:'POST',body:JSON.stringify({name})});
      toast((action==='start'?'Started ':'Stopped ')+name);await loadAgents();refreshOverview();
    }catch(e){toast(e.message,true);}
  }
  async function sendChat(){const i=qs('#chatInput'),text=i?.value.trim();if(!text)return;i.value='';await sendCommand(text);}
  async function toggleVoice(){
    if(state.voice){state.voice.stop();return;}
    if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){toast('Voice input is unavailable in this browser',true);return;}
    try{
      const stream=await navigator.mediaDevices.getUserMedia({audio:true}),rec=new MediaRecorder(stream),chunks=[];state.voice=rec;
      qs('#voiceToggle').textContent='STOP';
      rec.ondataavailable=e=>e.data.size&&chunks.push(e.data);
      rec.onstop=async()=>{stream.getTracks().forEach(t=>t.stop());state.voice=null;qs('#voiceToggle').textContent='VOICE';
        try{const fd=new Blob(chunks,{type:rec.mimeType||'audio/webm'}),q=localStorage.getItem('hermus_session_id')||'';
          const r=await fetch('/voice/command?session_id='+encodeURIComponent(q)+'&user_id=default',{method:'POST',headers:{'Content-Type':fd.type,...(token?{'X-Hermus-Token':token}:{})},body:fd});
          if(!r.ok)throw new Error('Voice request failed');toast('Voice request sent');
        }catch(e){toast(e.message,true);}
      };
      rec.start();toast('Listening…');
    }catch(e){toast(e.message,true);}
  }

  qs('#commandbarForm')?.addEventListener('submit',e=>{e.preventDefault();const i=qs('#commandbarInput');if(i)sendCommand(i.value);if(i)i.value='';});
  qs('#overviewCommandForm')?.addEventListener('submit',e=>{e.preventDefault();const i=qs('#overviewCommand');if(i)sendCommand(i.value);if(i)i.value='';});
  qs('#paletteInput')?.addEventListener('input',renderPalette);
  document.addEventListener('input',e=>{if(e.target?.id==='chatFontSizeRange')setChatFontSize(e.target.value)},false);

  qs('#paletteInput')?.addEventListener('keydown',e=>{if(e.key==='Enter'){const first=qs('[data-palette-view]');if(first)first.click();}});
  qs('#chatInput')?.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendChat();}});
  qs('#buildEditor')?.addEventListener('input',()=>{qs('#buildDirty').textContent='UNSAVED';});
  window.addEventListener('popstate',()=>{openView(location.hash.slice(1)||'overview',{history:false});});
  window.addEventListener('hashchange',()=>{const key=location.hash.slice(1);if(key&&views[key]&&key!==state.view)openView(key,{history:false});});
  document.addEventListener('keydown',e=>{
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();openPalette();}
    trapPaletteFocus(e);
    const wz=qs('#setupWizard');
    if(wz?.classList.contains('open')){
      if(e.key==='Escape'){e.preventDefault();closeWizard();return;}
      if(e.key==='Enter'&&e.ctrlKey){e.preventDefault();wizardNext();return;}
    }
    if(e.key==='Escape')closePalette();
  });

  async function boot() {
    initSettingsA11y();
    applySettings();
    const hash=location.hash.slice(1);
    openView(views[hash]?hash:'overview',{history:false});
    await refreshOverview();
    renderChat();
    if(localStorage.getItem('hermus_setup_complete')!=='1') setTimeout(()=>openWizard(),350);
  }
  boot();
})();