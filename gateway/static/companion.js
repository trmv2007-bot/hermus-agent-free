/* HERMUS — the companion screen's only client.
 *
 * One input, one character, one real turn at a time. It streams the canonical
 * run bus (POST /stream/command), so what appears here is what the gateway
 * really produced — including the honest failure when no model can answer. */
(function () {
  "use strict";

  var name = "Hermus";
  var busy = false;
  var modelInfo = {};
  var modelLabel = "";

  /* The gateway token, when the person set one. Same convention as the
     Control Room, so the companion keeps working on a locked gateway. */
  var token = "";
  try {
    token = new URLSearchParams(location.search).get("token") || localStorage.getItem("hermus_gateway_token") || "";
    if (token) localStorage.setItem("hermus_gateway_token", token);
  } catch (e) { /* private mode: the gateway is open locally anyway */ }

  function $(sel) { return document.querySelector(sel); }

  function addRow(kind, text) {
    var words = $("#words");
    var row = document.createElement("p");
    row.className = kind;
    row.textContent = text;
    words.appendChild(row);
    words.scrollTop = words.scrollHeight;
    return row;
  }

  function mood(next) { $("#being").setAttribute("data-mood", next); }

  /* A reasoning model streams its scratchpad as tokens. The companion shows
     the answer, so anything inside a think block is dropped before it renders —
     including the zero-width markers some runtimes put in the tag. */
  function plain(text) {
    return String(text || "")
      .replace(/<\u200b?think[^>]*>/gi, "\u0000")
      .replace(/<\/\u200b?think>/gi, "\u0001")
      .split("\u0000").map(function (chunk, i) {
        if (i === 0) return chunk;
        var close = chunk.indexOf("\u0001");
        return close === -1 ? "" : chunk.slice(close + 1);
      }).join("")
      .replace(/\u0000|\u0001/g, "")
      .trim();
  }

  function setBusy(on) {
    busy = on;
    $("#go").disabled = on;
    mood(on ? "busy" : "idle");
  }

  function say() {
    var input = $("#say");
    var text = (input.value || "").trim();
    if (!text || busy) return;
    input.value = "";
    addRow("said", text);

    var reply = addRow("reply", "");
    var seenDelta = false;
    var raw = "";

    /* No model, no pretend: the answer is the truth plus the one command. */
    if (modelInfo.kind === "stub") {
      reply.textContent = "I cannot answer — no model is reachable on this machine. " +
        (modelInfo.fix ? "Fix it with: " + modelInfo.fix : "");
      mood("error");
      $("#modelLine").textContent = "no model — nothing was sent";
      $("#say").focus();
      return;
    }

    setBusy(true);
    $("#modelLine").textContent = "thinking…";

    fetch("/stream/command", {
      method: "POST",
      headers: (function () {
        var h = { "Content-Type": "application/json" };
        if (token) h["X-Hermus-Token"] = token;
        return h;
      })(),
      body: JSON.stringify({ text: text, platform: "web", user_id: "local", prefer: "chat", stream: true })
    }).then(function (resp) {
      if (resp.status === 401) throw new Error("this gateway needs its access token — add ?token=… to the address once");
      if (resp.status === 400) throw new Error("the gateway rejected the message");
      if (!resp.body || !resp.body.getReader) throw new Error("this browser cannot stream responses");
      var reader = resp.body.getReader();
      var decoder = new TextDecoder();
      var buf = "";

      function handle(block) {
        var ev = "message";
        var data = "";
        block.split("\n").forEach(function (line) {
          if (line.indexOf("event: ") === 0) ev = line.slice(7).trim();
          else if (line.indexOf("data: ") === 0) data += line.slice(6);
        });
        if (!data) return;
        var payload;
        try { payload = JSON.parse(data); } catch (e) { return; }
        var d = payload.data || payload;

        if (ev === "llm_delta" && d.text) {
          seenDelta = true;
          raw += d.text;
          reply.textContent = plain(raw);
          $("#words").scrollTop = $("#words").scrollHeight;
          return;
        }
        if (ev === "agent_response") {
          /* The runtime's own answer wins over the delta accumulation: deltas
             can be cut mid-sentence by a step boundary. */
          if (d.text) { reply.textContent = plain(d.text); seenDelta = true; }
          if (d.model) $("#modelLine").textContent = d.model;
          return;
        }
        if (ev === "approval_required" || ev === "tool_call") {
          $("#modelLine").textContent = ev === "approval_required"
            ? "waiting for your approval — open Systems"
            : "using a tool…";
          return;
        }
        if (ev === "mission_finished" && d.state === "failed") {
          var reason = (d.failure && (d.failure.reason || d.failure.message)) || "that did not finish";
          reply.textContent = reply.textContent || ("That failed — " + reason);
          return;
        }
      }

      function pump() {
        return reader.read().then(function (r) {
          if (r.done) return;
          buf += decoder.decode(r.value, { stream: true });
          var blocks = buf.split("\n\n");
          buf = blocks.pop();
          blocks.forEach(function (b) { if (b.trim()) handle(b); });
          return pump();
        });
      }
      return pump();
    }).catch(function (err) {
      if (!reply.textContent) reply.textContent = "I could not reach the gateway — " + err.message;
    }).finally(function () {
      setBusy(false);
      if (!reply.textContent) {
        reply.textContent = "Nothing came back. The gateway did not finish this turn.";
        mood("error");
      } else {
        $("#modelLine").textContent = modelLabel;
      }
      $("#say").focus();
    });
  }

  window.addEventListener("DOMContentLoaded", function () {
    fetch("/api/v1/onboarding/state")
      .then(function (r) { return r.json(); })
      .then(function (s) {
        name = s.name || "Hermus";
        document.title = name;
        var brain = s.brain || {};
        modelInfo = { kind: (s.resolved && s.resolved.kind) || brain.kind, fix: s.fix || "" };
        $("#greeting").textContent = brain.model
          ? "I am " + name + ". " + (modelInfo.kind === "stub" ? "I have no model yet." : "Ask me anything.")
          : "Hello. I am " + name + ".";
        modelLabel = s.pickup ? s.pickup + (brain.source ? " · " + brain.source : "") : "";
        $("#modelLine").textContent = modelLabel;
        if (modelInfo.kind === "stub") mood("error");
      })
      .catch(function () { /* the screen still works without the label */ });

    $("#go").addEventListener("click", say);
    $("#say").addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); say(); }
    });
  });
})();
