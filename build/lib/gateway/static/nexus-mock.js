(() => {
  const params = new URLSearchParams(location.search);
  if (params.get('mock') !== '1') return;

  /*
   * Nexus visual mock mode.
   * This intentionally replaces network responses only; production logic stays untouched.
   * Open /control?mock=1 to inspect every dashboard surface without a live gateway.
   */
  const originalFetch = window.fetch.bind(window);
  const json = (value, status=200) => Promise.resolve(new Response(JSON.stringify(value), {
    status, headers: {'Content-Type':'application/json'}
  }));

  const models = [
    {ref:'mock/local-qwen3', id:'qwen3:4b', provider:'local', provider_name:'Local', source:'live', reachable:true,
      capabilities:{tools:'yes',vision:'yes'}, capability_notes:['mock deployment']},
    {ref:'mock/reasoning', id:'reasoning-demo', provider:'mock', source:'live', reachable:true,
      capabilities:{tools:'yes',vision:'no'}},
    {ref:'mock/vision', id:'vision-demo', provider:'mock', source:'live', reachable:true,
      capabilities:{tools:'no',vision:'yes'}}
  ];
  const manifest = {
    groups:['runtime','agents','memory','models','capabilities','safety','workspace'].map((id,i)=>({id,label:id,panels:i+1})),
    panels:[
      {id:'runtime',label:'Runtime',group:'runtime',source:'mock',summary:'Gateway and runtime state',owner:'mock',actions:[]},
      {id:'agents',label:'Agents & devices',group:'agents',source:'mock',summary:'Crew and connected devices',owner:'mock',actions:[]},
      {id:'memory',label:'Memory & learning',group:'memory',source:'mock',summary:'Memory and learning state',owner:'mock',actions:[]},
      {id:'models',label:'Models & keys',group:'models',source:'mock',summary:'Discovered model deployments',owner:'mock',actions:[]},
      {id:'capabilities',label:'Capabilities',group:'capabilities',source:'mock',summary:'Capability readiness',owner:'mock',actions:[]},
      {id:'safety',label:'Safety & recovery',group:'safety',source:'mock',summary:'Authorization and recovery',owner:'mock',actions:[]},
      {id:'workspace',label:'Workspace',group:'workspace',source:'mock',summary:'Workspace state',owner:'mock',actions:[]}
    ]
  };

  function personalSpace() {
    return {status:'proposal_ready', idle_for_seconds:642, pending_proposals:1, daily_cycles:3, daily_cap:8,
      proposals:[{id:'mock-proposal',status:'pending',title:'A useful improvement',category:'research',
        confidence:'0.91',summary:'Mock proposal for visual inspection.',why:'Demonstrates the proposal state.',
        next_action:'Review the proposal.'}]};
  }

  window.fetch = async (input, init={}) => {
    const url = typeof input === 'string' ? input : input.url;
    const path = new URL(url, location.href).pathname + new URL(url, location.href).search;
    const method = String(init.method || (typeof input !== 'string' && input.method) || 'GET').toUpperCase();

    if (path.includes('/stream/run/')) {
      return new Response('', {status:200, headers:{'Content-Type':'text/event-stream'}});
    }
    if (path.includes('/api/v1/system/health')) return json({gateway:{ok:true,running:true},database:{ok:true},queue:{ok:true}});
    if (path.includes('/api/v1/system/capabilities')) return json({capabilities:{browser:true,computer:true,vision:true,memory:true,voice:true,workspace:true}});
    if (path.startsWith('/models/catalog')) return json({count:models.length,models});
    if (path === '/models/selected') return json({selections:{default:'auto',reasoning:'mock/reasoning',vision:'mock/vision',coding:'auto',background:'auto',doctor:'auto',voice:'auto'}});
    if (path === '/models/health') return json({models:{'mock/local-qwen3':{avg_latency_ms:82},'mock/reasoning':{avg_latency_ms:140}}});
    if (path === '/models/select' && method==='POST') return json({selections:{default:'mock/local-qwen3'}});
    if (path.includes('/presence/kernel')) return json({
      summary:{state:'idle',detail:'ready',headline:'All systems ready',active_runs:0,attention_count:1},
      attention:[{severity:'medium',title:'Visual QA mode',detail:'Mock data is active; no real actions are executed.'}],
      events:{recent:[{event_id:'mock-1',summary:'Mock environment connected',type:'info'}]}
    });
    if (path === '/personal-space') return json(personalSpace());
    if (path === '/personal-space/run' && method==='POST') return json({...personalSpace(),status:'curious',pending_proposals:0,proposals:[]});
    if (path.includes('/personal-space/proposals/') && method==='POST') return json({ok:true});
    if (path === '/focus') return json({items:[{title:'Review active mission',priority:'high',detail:'Mock attention item'}]});
    if (path.startsWith('/learning')) return json({lessons:[{title:'Mock learning episode',status:'ready'}]});
    if (path === '/routines') return json({routines:[{id:'mock-routine',name:'Daily review',enabled:true,event_type:'schedule',action_type:'suggestion',task:'Review important changes',cooldown_seconds:3600}]});
    if (path === '/devices') return json({desktop:{status:'online'},browser:{status:'ready'},android:{status:'ready'}});
    if (path === '/workshop/snapshot') return json({current:'mock-project',projects:[{name:'mock-project'}],tree:[
      {name:'README.md',path:'README.md',type:'file',size:420},{name:'src',path:'src',type:'directory'},{name:'app.js',path:'src/app.js',type:'file',size:860}]});
    if (path === '/workshop/file') return json({editable:true,size:420,content:'// HERMUS visual mock\\n\\nexport const ready = true;\\n'});
    if (path === '/context') return json({summary:{state:'idle'},attention:[],project:'mock-project',memory:[{content:'Mock memory'}],goals:[{title:'Build the Nexus'}]});
    if (path === '/api/v1/console/manifest') return json(manifest);
    if (path.startsWith('/api/v1/console/panels')) return json({panels:manifest.panels.map(p=>({...p,status:'ready'}))});
    if (path.startsWith('/api/v1/console/projection/')) return json({status:'ready',source:'mock'});
    if (path.startsWith('/api/v1/commands') || path === '/jobs') return json({run_id:'mock-run'});
    if (path.startsWith('/voice/command')) return json({run_id:'mock-voice-run',ack:{}});
    if (method==='POST') return json({success:true,status:'ok'});
    return json({status:'ready',source:'mock',path});
  };

  window.addEventListener('DOMContentLoaded', () => {
    const badge=document.createElement('div');
    badge.textContent='MOCK · VISUAL QA';
    Object.assign(badge.style,{position:'fixed',top:'76px',right:'16px',zIndex:'200',padding:'7px 10px',
      border:'1px solid rgba(112,231,255,.25)',borderRadius:'999px',background:'rgba(5,9,16,.82)',
      color:'#70e7ff',font:'8px ui-monospace,monospace',letterSpacing:'.14em',backdropFilter:'blur(10px)'});
    document.body.appendChild(badge);
  });
})();