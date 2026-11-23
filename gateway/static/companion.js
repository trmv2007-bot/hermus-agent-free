/* HERMUS — the room's only client.
 *
 * One character, one line, one quiet door to the panels. It streams the
 * canonical run bus (POST /stream/command), so what appears is what the gateway
 * really produced — including the honest failure when nothing can answer, and
 * the stored answer when the stream itself dies (the job result outlives the
 * socket).
 *
 * There is deliberately no polling in this file: the room asks for one thing on
 * load and then only answers what the person types. */
(function () {
  "use strict";

  /* Every destination the product had before the room existed. The Systems
     sheet and the palette are both built from this one list, so a surface can
     never be listed in one and missing from the other. */
  var SECTIONS = [
    { tab: "overview", name: "Overview", note: "the core · mission · live channel" },
    { tab: "jobs", name: "Jobs", note: "every run and its result" },
    { tab: "missions", name: "Missions", note: "multi-step goals and reports" },
    { tab: "approvals", name: "Approvals", note: "scoped grants and pending prompts" },
    { tab: "safety", name: "Safety", note: "red lines · emergency stop" },
    { tab: "safety", name: "Undo journal", note: "put the last change back", anchor: "undoJournal" },
    { tab: "capabilities", name: "Capabilities", note: "what it can and cannot do" },
    { tab: "memory", name: "Memory", note: "what it remembers" },
    { tab: "presence", name: "Presence", note: "identity · goals · check-ins" },
    { tab: "computer", name: "Computer", note: "screen and input control" },
    { tab: "agents", name: "Agents", note: "delegation and the harness" },
    { tab: "systems", name: "Diagnostics", note: "doctor and engine probes" }
  ];

  var name = "Hermus";
  var modelInfo = {};
  var modelLabel = "";
  var busy = false;
  var jobId = "";
  var abort = null;
  var sessionId = "";
  var paletteActive = 0;

  /* The gateway token, when the person set one. Same convention as the Control
     Room, so the room keeps working on a locked gateway. */
  var token = "";
  try {
    token = new URLSearchParams(location.search).get("token") || localStorage.getItem("hermus_gateway_token") || "";
    if (token) localStorage.setItem("hermus_gateway_token", token);
    sessionId = localStorage.getItem("hermus_room_session") || "";
    if (!sessionId) {
      sessionId = "room_" + Math.random().toString(36).slice(2, 12);
      localStorage.setItem("hermus_room_session", sessionId);
    }
  } catch (e) { /* private mode: no continuity, the room still answers */ }

  function $(sel) { return document.querySelector(sel); }

  function headers(extra) {
    var h = extra || {};
    if (token) h["X-Hermus-Token"] = token;
    return h;
  }

  function mood(next) { $("#being").setAttribute("data-mood", next); }

  /* A reasoning model streams its scratchpad as tokens. The room shows the
     answer, so anything inside a think block is dropped before it renders —
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

  /* ---------------------------------------------------------------- the line */

  function setBusy(on) {
    busy = on;
    document.body.setAttribute("data-busy", on ? "true" : "false");
    var go = $("#go");
    go.dataset.mode = on ? "stop" : "send";
    go.textContent = on ? "Stop" : "Send";
    go.setAttribute("aria-label", on ? "Stop this turn" : "Send your message");
    mood(on ? "busy" : "idle");
    if (!on) { jobId = ""; abort = null; }
  }

  function under(text, tone) {
    var el = $("#under");
    el.textContent = text || "";
    if (tone) el.setAttribute("data-tone", tone);
    else el.removeAttribute("data-tone");
  }

  function saidCount() {
    return document.querySelectorAll("#thread .exchange").length;
  }

  function markSaid() {
    document.body.setAttribute("data-said", saidCount() > 0 ? "true" : "false");
  }

  function nearBottom() {
    var stage = $("#stage");
    return stage.scrollHeight - stage.scrollTop - stage.clientHeight < 90;
  }

  function toBottom(wasPinned) {
    var stage = $("#stage");
    if (wasPinned) stage.scrollTop = stage.scrollHeight;
  }

  function exchange(saidText) {
    var hello = $("#thread .hello");
    if (hello) hello.remove();
    var row = document.createElement("section");
    row.className = "exchange";
    var said = document.createElement("p");
    said.className = "said";
    said.textContent = saidText;
    var reply = document.createElement("p");
    reply.className = "reply";
    reply.dataset.streaming = "true";
    row.appendChild(said);
    row.appendChild(reply);
    $("#thread").appendChild(row);
    markSaid();
    toBottom(true);
    return { row: row, reply: reply };
  }

  function answer(reply, text, tone) {
    reply.textContent = text;
    reply.dataset.streaming = "false";
    if (tone) reply.dataset.tone = tone;
    else reply.removeAttribute("data-tone");
  }

  /* ------------------------------------------------------- the stored answer */

  /* The run stream is a live channel, not the record. When it ends without an
     answer — a dropped socket, a cancelled turn, a step that died mid-sentence —
     the gateway still holds the turn's result, so ask it instead of showing an
     empty thread. */
  function fetchStoredAnswer(id, reply, options) {
    if (!id) return Promise.resolve(false);
    var opts = options || {};
    return fetch("/jobs/" + encodeURIComponent(id) + "/result", { headers: headers() })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (body) {
        var result = (body && body.result) || null;
        var text = result && (result.response || result.text || result.output || "");
        if (!text) return false;
        answer(reply, plain(text));
        mood("idle");
        return true;
      })
      .catch(function () { return false; })
      .then(function (ok) {
        if (!ok && opts.whenMissed) answer(reply, opts.whenMissed, opts.tone || "quiet");
        return ok;
      });
  }

  /* --------------------------------------------------------------- one turn */

  function say(text) {
    if (!text || busy) return;
    var reply = exchange(text).reply;
    setBusy(true);
    under("");

    if (modelInfo.kind === "stub") {
      answer(reply, "I cannot answer — no model is reachable on this machine. " +
        (modelInfo.fix ? "Fix it with: " + modelInfo.fix : ""), "quiet");
      setBusy(false);
      mood("error");
      under("no model — nothing was sent", "bad");
      $("#say").focus();
      return;
    }

    var controller = new AbortController();
    abort = controller;
    var seen = false;
    var sawApproval = false;
    var raw = "";
    var failed = "";

    fetch("/stream/command", {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        text: text, platform: "web", user_id: "local", prefer: "chat", stream: true, session_id: sessionId
      }),
      signal: controller.signal
    }).then(function (resp) {
      if (resp.status === 401) throw new Error("this gateway needs its access token — add ?token=… to the address once");
      if (resp.status === 400) throw new Error("the gateway rejected the message");
      jobId = resp.headers.get("X-Hermus-Job") || "";
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
          seen = true;
          raw += d.text;
          answer(reply, plain(raw));
          toBottom(true);
          return;
        }
        if (ev === "agent_response") {
          /* The runtime's own answer wins over the delta accumulation: deltas
             can be cut mid-sentence by a step boundary. */
          if (d.text) { seen = true; answer(reply, plain(d.text)); }
          if (d.model) modelLabel = d.model;
          return;
        }
        if (ev === "approval_required") {
          sawApproval = true;
          reply.dataset.streaming = "false";
          approvalCard(reply, d.approval_request || d.request || {});
          return;
        }
        if (ev === "tool_call") {
          under("working · " + String(d.tool || "using a tool"), "");
          return;
        }
        if (ev === "mission_finished" && d.state === "failed") {
          failed = (d.failure && (d.failure.reason || d.failure.message)) || "that did not finish";
          return;
        }
        if (ev === "run_error" && d.error) failed = String(d.error);
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
      if (err && err.name === "AbortError") {
        answer(reply, plain(raw) || "Stopped.", raw ? null : "quiet");
        return;
      }
      failed = err && err.message ? err.message : "the stream failed";
      /* The socket is the thing that broke, not the turn: the stored result is
         still worth asking for before saying anything failed. */
      return fetchStoredAnswer(jobId, reply, {}).then(function (ok) {
        if (!ok && !reply.textContent) answer(reply, "", "quiet");
      });
    }).then(function () {
      if (controller.signal.aborted || sawApproval) return;
      if (!seen && !reply.textContent) {
        return fetchStoredAnswer(jobId, reply, {
          whenMissed: failed ? "That did not finish — " + failed : "Nothing came back. The gateway did not finish this turn.",
          tone: "quiet"
        });
      }
      return null;
    }).then(function () {
      if (controller.signal.aborted) return;
      reply.dataset.streaming = "false";
      setBusy(false);
      if (!reply.textContent && !sawApproval) answer(reply, "Nothing came back.", "quiet");
      if (reply.dataset.tone === "quiet") mood("error");
      under(modelLabel);
      $("#say").focus();
    }).catch(function () {
      setBusy(false);
    });
  }

  function stop() {
    if (!busy) return;
    if (abort) abort.abort();
    if (jobId) {
      fetch("/jobs/" + encodeURIComponent(jobId) + "/cancel", { method: "POST", headers: headers() })
        .catch(function () { /* the abort already ended the socket */ });
    }
    under("stopped");
    setBusy(false);
    $("#say").focus();
  }

  /* ------------------------------------------------------------- approvals */

  /* A yellow action arrives as one card in the conversation. It is not a modal:
     the person can keep reading, and nothing steals the caret. */
  function approvalCard(reply, request) {
    if (!request || !request.id) {
      answer(reply, "This needs your approval. Grant it in Systems → Approvals.", "quiet");
      return;
    }
    var card = document.createElement("div");
    card.className = "card-inline";
    card.dataset.approvalId = request.id;

    var title = document.createElement("h3");
    title.textContent = request.title || ("Approve " + (request.tool || "this action"));
    var what = document.createElement("p");
    what.textContent = request.suggested_purpose || "It wants to change something on your machine.";
    card.appendChild(title);
    card.appendChild(what);

    var targets = (request.suggested_resources || []).join(" · ");
    if (targets) {
      var t = document.createElement("p");
      t.className = "targets";
      t.textContent = targets;
      card.appendChild(t);
    }

    var act = document.createElement("div");
    act.className = "act";
    var yes = document.createElement("button");
    yes.type = "button";
    yes.className = "btn primary";
    yes.textContent = "Approve";
    var no = document.createElement("button");
    no.type = "button";
    no.className = "btn ghost";
    no.textContent = "Deny";
    act.appendChild(yes);
    act.appendChild(no);
    card.appendChild(act);
    reply.parentNode.appendChild(card);
    toBottom(true);

    function settle(decision, label) {
      yes.disabled = true;
      no.disabled = true;
      return fetch("/permissions/pending/resolve", {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ id: request.id, decision: decision, retry: decision === "approve" })
      }).then(function (r) { return r.json(); }).then(function (body) {
        card.dataset.resolved = "true";
        var line = document.createElement("p");
        var retried = body && body.retry;
        if (decision === "approve") {
          var outcome = retried && (retried.result && (retried.result.output || retried.result.message))
            || (body && body.success ? "allowed" : (body && body.error) || "allowed");
          line.textContent = "Approved. " + String(outcome).slice(0, 400);
        } else {
          line.textContent = "Denied. Nothing was changed.";
        }
        card.appendChild(line);
        title.textContent = label;
        act.remove();
      }).catch(function (err) {
        var line = document.createElement("p");
        line.textContent = "Could not record that: " + err.message;
        card.appendChild(line);
        yes.disabled = false;
        no.disabled = false;
      });
    }

    yes.addEventListener("click", function () { settle("approve", "Approved"); });
    no.addEventListener("click", function () { settle("deny", "Denied"); });
    if (document.body.getAttribute("data-busy") !== "true") yes.focus({ preventScroll: false });
  }

  /* ------------------------------------------------------------ the systems */

  function sectionsMarkup(items) {
    return items.map(function (s) {
      var href = "/control?tab=" + encodeURIComponent(s.tab) + (s.anchor ? "#" + s.anchor : "");
      return '<a href="' + href + '"><b>' + s.name + '</b><span>' + s.note + "</span></a>";
    }).join("");
  }

  function buildSystems() {
    $("#systemsList").innerHTML = sectionsMarkup(SECTIONS);
  }

  function openSystems() {
    var sheet = $("#systems");
    $("#systemsBtn").setAttribute("aria-expanded", "true");
    if (typeof sheet.showModal === "function") {
      if (!sheet.open) sheet.showModal();
    } else {
      sheet.setAttribute("open", "");
      sheet.classList.add("on");
    }
    var first = sheet.querySelector("a, button");
    if (first) first.focus();
  }

  function closeSystems() {
    var sheet = $("#systems");
    $("#systemsBtn").setAttribute("aria-expanded", "false");
    if (typeof sheet.close === "function" && sheet.open) sheet.close();
    else { sheet.removeAttribute("open"); sheet.classList.remove("on"); }
  }

  /* ------------------------------------------------------------ the palette */

  var paletteReturn = null;

  function showPalette(on) {
    var host = $("#palette");
    if (!host) return;
    host.classList.toggle("open", !!on);
    host.setAttribute("aria-hidden", on ? "false" : "true");
    if (on) {
      paletteReturn = document.activeElement;
      paletteActive = 0;
      renderPalette("");
      $("#paletteInput").value = "";
      setTimeout(function () { $("#paletteInput").focus(); }, 0);
    } else {
      var back = paletteReturn && paletteReturn.focus ? paletteReturn : $("#systemsBtn");
      paletteReturn = null;
      if (back && back.focus) back.focus();
    }
  }

  function paletteItems() {
    return SECTIONS.map(function (s) {
      return {
        name: s.name,
        note: s.note,
        href: "/control?tab=" + encodeURIComponent(s.tab) + (s.anchor ? "#" + s.anchor : "")
      };
    });
  }

  function renderPalette(query) {
    var q = String(query || "").toLowerCase();
    var items = paletteItems().filter(function (it) {
      return !q || (it.name + " " + it.note).toLowerCase().indexOf(q) !== -1;
    });
    if (paletteActive >= items.length) paletteActive = Math.max(0, items.length - 1);
    var host = $("#paletteList");
    if (!items.length) {
      host.innerHTML = '<div class="empty-event">No HERMUS surface matches that.</div>';
      return;
    }
    host.innerHTML = items.map(function (it, i) {
      return '<a class="palette-item' + (i === paletteActive ? " active" : "") + '" href="' + it.href +
        '"><b>' + it.name + "</b><span>" + it.note + "</span></a>";
    }).join("");
    var active = host.querySelectorAll(".palette-item")[paletteActive];
    if (active && active.scrollIntoView) active.scrollIntoView({ block: "nearest" });
  }

  function paletteMove(delta) {
    var count = $("#paletteList").querySelectorAll(".palette-item").length;
    if (!count) return;
    paletteActive = (paletteActive + delta + count) % count;
    renderPalette($("#paletteInput").value);
  }

  function paletteGo() {
    var items = $("#paletteList").querySelectorAll(".palette-item");
    var it = items[paletteActive] || items[0];
    if (it) location.href = it.getAttribute("href");
  }

  /* ---------------------------------------------------------------- wiring */

  function restore() {
    if (!sessionId) return;
    fetch("/conversation/" + encodeURIComponent(sessionId), { headers: headers() })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (snap) {
        var turns = (snap && snap.turns) || [];
        if (!turns.length) { hello(); return; }
        hello();
        var lastQ = "";
        turns.slice(-6).forEach(function (t) {
          if (t.role === "user") { lastQ = t.text || ""; return; }
          if (!lastQ) return;
          var made = exchange(lastQ);
          answer(made.reply, plain(t.text || ""));
          lastQ = "";
        });
        markSaid();
        toBottom(true);
      })
      .catch(function () { hello(); });
  }

  function hello() {
    if (saidCount() || $("#thread .hello")) return;
    var p = document.createElement("p");
    p.className = "hello";
    p.textContent = name === "Hermus" ? "Ask me anything." : "I am " + name + ". Ask me anything.";
    $("#thread").appendChild(p);
  }

  window.addEventListener("DOMContentLoaded", function () {
    buildSystems();
    document.body.setAttribute("data-busy", "false");
    document.body.setAttribute("data-said", "false");

    fetch("/api/v1/onboarding/state", { headers: headers() })
      .then(function (r) { return r.json(); })
      .then(function (s) {
        name = s.name || "Hermus";
        document.title = name;
        $("#roomName").textContent = name;
        var brain = s.brain || {};
        modelInfo = { kind: (s.resolved && s.resolved.kind) || brain.kind, fix: s.fix || "" };
        modelLabel = s.pickup ? s.pickup + (brain.source ? " · " + brain.source : "") : "";
        /* The first paint says nothing about the machinery. The one exception
           is a missing model, because that changes what the answer will be and
           a person deserves to know before they type. */
        if (modelInfo.kind === "stub") {
          mood("error");
          under("no model yet — Systems explains what to install", "bad");
        }
        hello();
      })
      .catch(function () { hello(); });

    restore();

    $("#line").addEventListener("submit", function (e) {
      e.preventDefault();
      var input = $("#say");
      if (busy) { stop(); return; }
      var text = (input.value || "").trim();
      if (!text) return;
      input.value = "";
      say(text);
    });

    $("#go").addEventListener("click", function (e) {
      if (busy) { e.preventDefault(); stop(); }
    });

    $("#systemsBtn").addEventListener("click", openSystems);
    $("#systemsClose").addEventListener("click", closeSystems);
    $("#systems").addEventListener("close", function () {
      $("#systemsBtn").setAttribute("aria-expanded", "false");
      $("#systemsBtn").focus();
    });
    $("#paletteClose").addEventListener("click", function () { showPalette(false); });
    $("#palette").addEventListener("click", function (e) {
      if (e.target === $("#palette")) showPalette(false);
    });
    $("#paletteInput").addEventListener("input", function (e) {
      paletteActive = 0;
      renderPalette(e.target.value);
    });
    $("#paletteInput").addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); paletteMove(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); paletteMove(-1); }
      else if (e.key === "Enter") { e.preventDefault(); paletteGo(); }
    });
    /* The palette is a modal by role, so Tab stays inside it rather than
       walking into the room behind the scrim. */
    $("#palette").addEventListener("keydown", function (e) {
      if (e.key !== "Tab") return;
      var focusable = $("#palette").querySelectorAll("input, a.palette-item, button");
      if (!focusable.length) return;
      var first = focusable[0];
      var last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });

    window.addEventListener("keydown", function (e) {
      var open = $("#palette").classList.contains("open");
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "p") {
        e.preventDefault();
        showPalette(!open);
        return;
      }
      if (e.key === "Escape") {
        if (open) { e.preventDefault(); showPalette(false); return; }
        if ($("#systems").open) { e.preventDefault(); closeSystems(); }
      }
    });
  });

  /* The room's own contract, exposed for the Node harness; not a page API. */
  window.HermusRoom = {
    sections: SECTIONS,
    plain: plain
  };
})();
