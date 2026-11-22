/* Agents tab controller: registry CRUD, crew mail and GPU telemetry.
 *
 * Extracted from gateway/control.html (it used to be a 460-line inline
 * script). The GPU meter now reads the real endpoint instead of inventing a
 * random number; the particle/orb decoration was deleted with the old shell.
 */

// Agent System State
let agentSystem = {
  agents: [],
  messages: [],
  apiKeys: {},
  vramUsage: 0
};

const escAgents = (value) => String(value == null ? "" : value).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Create Agent
function createAgent() {
  const provider = document.getElementById('agentProviderSelect').value;
  const model = document.getElementById('agentModelSelect').value;
  const name = document.getElementById('agentNameInput').value || `Agent-${Date.now()}`;
  const role = document.getElementById('agentRoleSelect').value;

  fetch('/api/v1/agents/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider, model, name, role })
  }).then(r => r.json()).then(data => {
    showToast(`Agent ${name} created!`, 'ok');
    refreshAgents();
  }).catch(e => {
    showToast(`Failed to create agent: ${e}`, 'error');
  });
}

// Create Specialized Agent
function createSpecializedAgent(role) {
  fetch('/api/v1/agents/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      role: role,
      name: `Agent-${role}-${Date.now()}`,
      provider: 'ollama',
      model: 'mistral:7b'
    })
  }).then(r => r.json()).then(data => {
    showToast(`Specialized ${role} agent created!`, 'ok');
    refreshAgents();
  }).catch(e => {
    showToast(`Failed to create agent: ${e}`, 'error');
  });
}

// Create Custom Agent
function createCustomAgent() {
  createAgent();
}

// Add API Key
function addApiKey() {
  const provider = document.getElementById('apiKeyProvider').value;
  const key = document.getElementById('apiKeyInput').value;

  if (!key) {
    showToast('Please enter an API key', 'error');
    return;
  }

  fetch('/api/v1/agents/api-keys/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider, key })
  }).then(r => r.json()).then(data => {
    showToast(`API key for ${provider} added!`, 'ok');
    document.getElementById('apiKeyInput').value = '';
    refreshApiKeys();
  }).catch(e => {
    showToast(`Failed to add API key: ${e}`, 'error');
  });
}

// Send Agent Message
function sendAgentMessage() {
  const senderId = document.getElementById('messageSender').value;
  const targetId = document.getElementById('messageTarget').value;
  const content = document.getElementById('messageContent').value;

  if (!senderId || !targetId || !content) {
    showToast('Please select sender, target, and enter message', 'error');
    return;
  }

  fetch('/api/v1/agents/message', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sender_id: senderId, target_id: targetId, content })
  }).then(r => r.json()).then(data => {
    showToast(`Message sent from ${senderId} to ${targetId}`, 'ok');
    document.getElementById('messageContent').value = '';
    refreshAgentMessages();
  }).catch(e => {
    showToast(`Failed to send message: ${e}`, 'error');
  });
}

// Broadcast to All Agents
function broadcastToAll() {
  const content = prompt('Enter message to broadcast:');
  if (!content) return;

  fetch('/api/v1/agents/broadcast', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content })
  }).then(r => r.json()).then(data => {
    showToast(`Broadcast sent to ${data.count || 0} agents`, 'ok');
    refreshAgentMessages();
  }).catch(e => {
    showToast(`Failed to broadcast: ${e}`, 'error');
  });
}

// Ping All Agents
function pingAllAgents() {
  fetch('/api/v1/agents/ping', {
    method: 'POST'
  }).then(r => r.json()).then(data => {
    showToast(`Pinged ${data.count || 0} agents`, 'ok');
  }).catch(e => {
    showToast(`Failed to ping: ${e}`, 'error');
  });
}

// Start All Agents
function startAllAgents() {
  fetch('/api/v1/agents/start-all', {
    method: 'POST'
  }).then(r => r.json()).then(data => {
    showToast(`Started ${data.count || 0} agents`, 'ok');
    refreshAgents();
  }).catch(e => {
    showToast(`Failed to start agents: ${e}`, 'error');
  });
}

// Stop All Agents
function stopAllAgents() {
  if (!confirm('Stop all agents?')) return;

  fetch('/api/v1/agents/stop-all', {
    method: 'POST'
  }).then(r => r.json()).then(data => {
    showToast(`Stopped ${data.count || 0} agents`, 'ok');
    refreshAgents();
  }).catch(e => {
    showToast(`Failed to stop agents: ${e}`, 'error');
  });
}

// Every read on this tab goes through the room's one scheduler (scheduler.js):
// the console state bundle already carries agents, messages, keys and VRAM, so
// these polls cost no extra sockets. A failed read rejects, exactly as the old
// fetch().then() chain did, so each caller keeps its own fallback.
function agentsGet(path) {
  const bus = window.HermusBus;
  const transport = bus ? bus.get(path, { headers: { Accept: 'application/json' } })
    : fetch(path).then(r => r.text().then(t => ({ ok: r.ok, status: r.status, data: t ? JSON.parse(t) : {} })));
  return transport.then(res => {
    if (!res.ok) throw new Error(path + ' -> ' + res.status);
    return res.data || {};
  });
}

// Refresh Agents — a transient gateway restart must degrade to a note, not an
// unhandled pageerror from an uncaught rejection inside a 5s poller.
function refreshAgents() {
  return agentsGet('/api/v1/agents/list')
    .then(data => {
      agentSystem.agents = data.agents || [];
      updateAgentUI();
      updateAgentVisualization();
      updateAgentDropdowns();
    })
    .catch(() => {
      const note = document.getElementById('agentCount');
      if (note) note.textContent = 'fleet unavailable, retries automatically';
    });
}

// Refresh API Keys
function refreshApiKeys() {
  return agentsGet('/api/v1/agents/api-keys/list')
    .then(data => {
      agentSystem.apiKeys = data.keys || {};
      updateApiKeysUI();
    })
    .catch(() => { /* transient; the next poll retries */ });
}

// Refresh Agent Messages
function refreshAgentMessages() {
  return agentsGet('/api/v1/agents/messages')
    .then(data => {
      agentSystem.messages = data.messages || [];
      updateMessagesUI();
    })
    .catch(() => { /* transient; the next poll retries */ });
}

// Update Agent UI
function updateAgentUI() {
  const container = document.getElementById('activeAgents');
  if (!container) return;

  container.innerHTML = '';

  agentSystem.agents.forEach(agent => {
    const card = document.createElement('div');
    card.className = 'kpi';
    card.innerHTML = `
      <div class="v">${agent.name || agent.agent_id.substring(0, 8)}</div>
      <div class="l">${agent.state || 'unknown'}</div>
      <div class="l">Provider: ${agent.provider || 'ollama'}</div>
      <div class="l">Role: ${agent.role || 'general'}</div>
    `;
    container.appendChild(card);
  });

  // Update agent count
  const countEl = document.getElementById('agentCount');
  if (countEl) {
    countEl.textContent = `agents: ${agentSystem.agents.length}`;
  }
}

// Update Agent Visualization
function updateAgentVisualization() {
  const container = document.getElementById('agentsVisualization');
  if (!container) return;

  const agents = agentSystem.agents || [];
  if (!agents.length) {
    container.innerHTML = '<div class="panel-state">No agents yet. Create one from the Agents tab.</div>';
    return;
  }

  container.innerHTML = agents.map((agent) => {
    const name = escAgents(agent.name || agent.agent_id || 'agent');
    const role = escAgents(agent.role || 'general');
    const provider = escAgents(agent.provider || 'ollama');
    const state = escAgents(agent.state || 'unknown');
    return '<div class="crew-card">'
      + '<div class="name">' + name + '</div>'
      + '<div class="role">' + role + ' &middot; ' + provider + '</div>'
      + '<div class="chip-row"><span class="tag">' + state + '</span></div>'
      + '</div>';
  }).join('');
}

// Update Agent Dropdowns
function updateAgentDropdowns() {
  const senderSelect = document.getElementById('messageSender');
  const targetSelect = document.getElementById('messageTarget');

  if (!senderSelect || !targetSelect) return;

  senderSelect.innerHTML = '<option value="">Select Agent</option>';
  targetSelect.innerHTML = '<option value="">Select Target</option>';

  agentSystem.agents.forEach(agent => {
    const option1 = document.createElement('option');
    option1.value = agent.agent_id;
    option1.textContent = `${agent.name || agent.agent_id.substring(0, 8)} (${agent.role || 'general'})`;
    senderSelect.appendChild(option1);

    const option2 = document.createElement('option');
    option2.value = agent.agent_id;
    option2.textContent = `${agent.name || agent.agent_id.substring(0, 8)} (${agent.role || 'general'})`;
    targetSelect.appendChild(option2);
  });
}

// Update API Keys UI
function updateApiKeysUI() {
  const container = document.getElementById('apiKeysList');
  if (!container) return;

  container.innerHTML = '';

  for (const [provider, keys] of Object.entries(agentSystem.apiKeys)) {
    const div = document.createElement('div');
    div.style.margin = '5px 0';
    div.innerHTML = `<strong>${provider}</strong>: ${keys.length} key(s)`;
    container.appendChild(div);
  }
}

// Update Messages UI
function updateMessagesUI() {
  const container = document.getElementById('agentMessages');
  if (!container) return;

  container.innerHTML = '';

  if (agentSystem.messages.length === 0) {
    container.innerHTML = '<div class="note">No agent messages yet</div>';
    return;
  }

  agentSystem.messages.slice().reverse().forEach(msg => {
    const div = document.createElement('div');
    div.style.padding = '5px 0';
    div.style.borderBottom = '1px dashed #1c2534';
    div.innerHTML = `
      <strong>${msg.sender_id || 'system'}</strong> -> ${msg.target_id || 'all'}: ${msg.content || '(no content)'}
    `;
    container.appendChild(div);
  });
}

// Update GPU meter from the real probe. No fabricated numbers: when the
// gateway reports no local GPU the meter stays at zero and says so.
function updateVRAMMonitor() {
  const fill = document.getElementById('vramFill');
  const percent = document.getElementById('vramPercent');
  const used = document.getElementById('vramUsed');
  const pill = document.getElementById('vramUsage');
  const note = document.getElementById('gpuNote');

  if (!fill || !percent || !used) return;

  return agentsGet('/api/v1/agents/vram/status')
    .then((data) => {
      if (data && data.warming) {
        // The gateway is still probing this machine's hardware for the first
        // time. Saying "no GPU" here would be a lie, and asking the endpoint
        // directly would cost the same ~10s probe the state bundle exists to
        // keep off the first screen.
        fill.style.width = '0%';
        percent.textContent = 'warming';
        used.textContent = 'probing the GPU';
        if (pill) { pill.textContent = 'GPU: …'; pill.className = 'pill unavailable'; }
        if (note) note.textContent = 'The gateway is still measuring this machine. This panel fills in within a minute.';
        return;
      }
      const vram = data.vram || {};
      const total = Number(vram.total || (data.gpu && data.gpu.vram_total) || 0);
      const value = Number(vram.used || 0);
      const pct = Number(vram.percent != null ? vram.percent : (total ? (value / total) * 100 : 0));
      const known = total > 0;

      fill.style.width = (known ? Math.max(0, Math.min(100, pct)) : 0) + '%';
      percent.textContent = known ? Math.round(pct) + '%' : 'unavailable';
      used.textContent = known ? value.toFixed(1) + ' / ' + total.toFixed(0) + ' GB' : 'no GPU telemetry';

      if (pill) {
        pill.textContent = known ? ('GPU: ' + Math.round(pct) + '%') : 'GPU: --';
        pill.className = 'pill ' + (known ? (pct > 85 ? 'err' : pct > 65 ? 'warn' : 'ok') : 'unavailable');
      }
      if (note) {
        note.textContent = known
          ? 'Live VRAM from /api/v1/agents/vram/status.'
          : 'The gateway reports no local GPU telemetry; the meter stays at zero rather than inventing a number.';
      }
    })
    .catch(() => {
      fill.style.width = '0%';
      percent.textContent = 'unavailable';
      used.textContent = 'endpoint unreachable';
      if (pill) { pill.textContent = 'GPU: --'; pill.className = 'pill unavailable'; }
      if (note) note.textContent = 'Reads /api/v1/agents/vram/status. Unreachable is shown as unreachable.';
    });
}

// Show Toast Notification
function showToast(message, type = 'info') {
  const container = document.getElementById('toasts');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `
    <span class="toast-msg">${message}</span>
    <button class="toast-close" onclick="this.parentElement.remove()">&#10005;</button>
  `;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 300);
  }, 5000);
}

// Initialize on load
window.addEventListener('load', () => {
  // One scheduler owns every timer in the room (scheduler.js); these four
  // cadences are registrations, not intervals.
  const bus = window.HermusBus;
  if (bus) {
    bus.every('agents', 5000, refreshAgents, { immediate: false });
    bus.every('agent-keys', 15000, refreshApiKeys, { immediate: false });
    bus.every('agent-messages', 6000, refreshAgentMessages, { immediate: false });
    bus.every('agent-vram', 5000, updateVRAMMonitor, { immediate: false });
  }

  // Initial updates
  refreshAgents();
  refreshApiKeys();
  refreshAgentMessages();
  updateVRAMMonitor();
});

// Expose functions to global scope
window.createAgent = createAgent;
window.createSpecializedAgent = createSpecializedAgent;
window.createCustomAgent = createCustomAgent;
window.addApiKey = addApiKey;
window.sendAgentMessage = sendAgentMessage;
window.broadcastToAll = broadcastToAll;
window.pingAllAgents = pingAllAgents;
window.startAllAgents = startAllAgents;
window.stopAllAgents = stopAllAgents;
window.refreshAgents = refreshAgents;
