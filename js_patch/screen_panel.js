// ---------- SCREEN PANEL ----------
function screenWsUrl() {
  const t = gatewayToken ? "?token=" + encodeURIComponent(gatewayToken) : "";
  return ((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/api/fleet/ws/fleet/screen") + t;
}

function initScreenPanel() {
  const sel = $("#screenAgentSelect");
  if (sel && $("#agentRosterGrid")) {
    const cards = $("#agentRosterGrid").querySelectorAll("[data-agent-id]");
    const agents = [];
    cards.forEach(c => {
      const id = c.getAttribute("data-agent-id");
      const name = (c.querySelector(".agent-name") || c).textContent.trim();
      if (id && name) agents.push({ id, name });
    });
    if (agents.length) {
      sel.innerHTML = '<option value="">Select agent...</option>' + agents.map(a =>
        '<option value="' + esc(a.id) + '">' + esc(a.name) + '</option>').join("");
    }
  }
  const statusEl = $("#screenStatus");
  const placeholder = $("#screenPlaceholder");
  const canvas = $("#screenCanvas");
  const overlay = $("#screenOverlay");

  function connectScreen() {
    if (screenWs) { try { screenWs.close(); } catch(e){} screenWs = null; }
    const agentId = sel && sel.value || "";
    try {
      screenWs = new WebSocket(screenWsUrl());
      screenWs.onopen = () => {
        if (statusEl) { statusEl.textContent = "connecting..."; statusEl.className = "screen-status"; }
      };
      screenWs.onclose = () => {
        screenWs = null;
        if (statusEl) { statusEl.textContent = "disconnected"; statusEl.className = "screen-status"; }
        if (!screenWsReconnectTimer && agentId) screenWsReconnectTimer = setTimeout(connectScreen, 10000);
      };
      screenWs.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (!msg || !msg.type) return;
          if (msg.type === "frame") {
            if (msg.data) {
              if (canvas) {
                const img = new Image();
                img.onload = () => { canvas.width = img.width; canvas.height = img.height; canvas.getContext("2d").drawImage(img, 0, 0); };
                img.src = "data:image/jpeg;base64," + msg.data;
              }
              if (placeholder) placeholder.style.display = "none";
              if (statusEl) { statusEl.textContent = "live"; statusEl.className = "screen-status ok"; }
              if (overlay) overlay.style.display = "";
            } else {
              if (placeholder) { placeholder.style.display = ""; placeholder.querySelector("p").textContent = msg.message || "Screen capture not yet available"; }
              if (statusEl) { statusEl.textContent = "unavailable"; statusEl.className = "screen-status warn"; }
              if (canvas) canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
              if (overlay) overlay.style.display = "none";
            }
          }
        } catch(e) {}
      };
    } catch(e) {}
  }
  if (sel) sel.onchange = connectScreen;
  if (sel && sel.value) connectScreen();
}
