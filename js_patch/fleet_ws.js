// ---------- FLEET WS LIVE FEED ----------
function fleetWsUrl() {
  const t = gatewayToken ? "?token=" + encodeURIComponent(gatewayToken) : "";
  return ((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/api/fleet/ws/fleet") + t;
}

function initFleetWs() {
  if (fleetWs) { try { fleetWs.close(); } catch(e){} fleetWs = null; }
  try {
    fleetWs = new WebSocket(fleetWsUrl());
    fleetWs.onopen = () => { if ($("#fleetWsStatus")) $("#fleetWsStatus").textContent = "live"; };
    fleetWs.onclose = () => {
      fleetWs = null;
      if ($("#fleetWsStatus")) $("#fleetWsStatus").textContent = "disconnected";
      if (!fleetWsReconnectTimer) fleetWsReconnectTimer = setTimeout(initFleetWs, 5000);
    };
    fleetWs.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (!msg || !msg.type) return;
        if (msg.type === "snapshot") { applyFleetSnapshot(msg.data); return; }
        if (msg.type === "fleet.state_changed") handleFleetStateChange(msg);
        else if (msg.type === "fleet.broadcast") handleFleetBroadcast(msg);
        else if (msg.type === "fleet.result") handleFleetResult(msg);
        else if (msg.type === "fleet.agent_updated") handleFleetAgentUpdated(msg);
        else if (msg.type === "fleet.mission_opened" || msg.type === "fleet.mission_terminated") {
          try { refreshMissions(); } catch(e) {}
        }
      } catch(e) {}
    };
  } catch(e) {}
}

function applyFleetSnapshot(data) {
  if (data && data.agents) {
    const agents = data.agents;
    const totalAgents = agents.length;
    const live = agents.filter(a => ["idle","working","thinking","blocked","paused"].includes(a.state));
    const done = agents.reduce((s, a) => s + ((a.stats && a.stats.tasks_done) || 0), 0);
    const toks = agents.reduce((s, a) => s + ((a.stats && a.stats.tokens) || 0), 0);
    const strip = $("#fleetSummary");
    if (strip) strip.innerHTML = kpi(live.length, "live agents") + kpi(totalAgents, "total agents")
      + kpi(done, "tasks done") + kpi(toks, "tokens");
    const ac = $("#agentCount");
    if (ac) ac.textContent = "agents: " + totalAgents;
  }
}

function handleFleetStateChange(msg) {
  const grid = $("#agentRosterGrid");
  if (!grid) return;
  const card = grid.querySelector('[data-agent-id="' + esc(msg.agent_id || "") + '"]');
  if (card) {
    const pill = card.querySelector(".state-pill");
    if (pill) {
      const st = msg.new_state || msg.state;
      pill.textContent = _fleetState(st);
      pill.className = "state-pill " + ({"idle":"ok","working":"working","thinking":"thinking","blocked":"blocked","paused":"paused","sleeping":"sleeping","error":"err","destroyed":"err"}[st] || "");
    }
    const nameEl = card.querySelector(".agent-name");
    if (nameEl && msg.agent_name) nameEl.textContent = esc(msg.agent_name);
  }
}

function handleFleetBroadcast(msg) {
  const feed = $("#busFeed");
  if (!feed) return;
  const row = document.createElement("div");
  row.className = "bus-row";
  row.innerHTML = '<span class="bus-ts">' + esc(msg.ts || "") + '</span>'
    + '<span class="bus-kind">broadcast</span>'
    + '<span class="bus-content">' + esc(msg.content || "") + '</span>';
  feed.appendChild(row);
  feed.scrollTop = feed.scrollHeight;
  while (feed.children.length > 200) feed.removeChild(feed.firstChild);
}

function handleFleetResult(msg) {
  if (msg.needs_approval) {
    const grid = $("#agentRosterGrid");
    if (grid) {
      const card = grid.querySelector('[data-agent-id="' + esc(msg.agent_id || "") + '"]');
      if (card) { card.classList.add("approval-pending"); setTimeout(() => card.classList.remove("approval-pending"), 8000); }
    }
  }
  const feed = $("#busFeed");
  if (feed) {
    const row = document.createElement("div");
    row.className = "bus-row";
    row.innerHTML = '<span class="bus-ts">' + esc(msg.ts || "") + '</span>'
      + '<span class="bus-kind">result</span>'
      + '<span class="bus-content">task ' + esc(msg.task_id || "") + " → " + (msg.executed ? "done" : "blocked") + '</span>';
    feed.appendChild(row);
    feed.scrollTop = feed.scrollHeight;
    while (feed.children.length > 200) feed.removeChild(feed.firstChild);
  }
}

function handleFleetAgentUpdated(msg) {
  try { refreshAgents(); } catch(e) {}
}
