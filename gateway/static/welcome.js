/* HERMUS — first run: three choices, then one real answer.
 *
 * Every step has a default already selected, so Enter alone finishes. Nothing
 * here invents a success: the last step only unlocks the companion when the
 * server recorded a real answer from a real model. */
(function () {
  "use strict";

  var state = null;
  var step = "brain";
  var choice = "local";
  var provider = "groq";
  var profile = "ask";
  var prepared = false;
  var answered = false;
  var failed = false;

  function $(sel) { return document.querySelector(sel); }
  function $$(sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); }
  function show(el, on) { if (el) el.classList.toggle("hidden", !on); }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json().catch(function () { return {}; }); });
  }

  /* ---------------------------------------------------------------- steps */
  function go(next) {
    step = next;
    $$(".step").forEach(function (s) { s.classList.toggle("on", s.id === "step-" + next); });
    var order = ["brain", "permissions", "name", "first"];
    var at = order.indexOf(next);
    $$(".dot").forEach(function (d, i) {
      d.classList.toggle("on", i === at);
      d.classList.toggle("past", i < at);
    });
    var focus = { brain: "#next-brain", permissions: "#next-perm", name: "#nameinput", first: "#firstinput" }[next];
    var node = $(focus);
    if (node) window.setTimeout(function () { node.focus(); }, 60);
    if (next === "first") startFirst();
  }

  /* A local model is only "ready" if a runtime is actually serving one. An
     installed-but-silent Ollama, or one with nothing pulled, still needs work —
     and that work is shown before anything is downloaded, never after. */
  function localReady() {
    if (!state) return false;
    var d = state.detection;
    return (d.ollama.serving && d.ollama.models.length > 0) || d.lmstudio.serving || d.nollama.serving;
  }

  function pickChoice(next) {
    choice = next;
    $$("#brain-choices .choice").forEach(function (c) {
      c.setAttribute("aria-checked", String(c.dataset.choice === next));
    });
    show($("#freekey-field"), next === "freekey");
    var needsPrepare = next === "local" && !localReady();
    show($("#prepare-field"), needsPrepare);
    if (needsPrepare) describePrepare();
    var note = $("#brain-note");
    if (next === "local") note.textContent = "Press Enter to use the model on this machine.";
    else if (next === "freekey") note.textContent = "Paste a key, then press Enter.";
    else note.textContent = "It will tell you plainly that it has no model.";
  }

  function pickProfile(next) {
    profile = next;
    $$("#perm-choices .choice").forEach(function (c) {
      c.setAttribute("aria-checked", String(c.dataset.profile === next));
    });
  }

  function pickProvider(next) {
    provider = next;
    $$("#provider-seg button").forEach(function (b) {
      b.setAttribute("aria-checked", String(b.dataset.provider === next));
    });
    var links = { groq: "console.groq.com/keys", mistral: "console.mistral.ai/api-keys", openrouter: "openrouter.ai/keys" };
    $("#key-hint").textContent = "Get a free key at " + links[next] + ". It is stored in .env on this machine, never sent anywhere else.";
  }

  /* ------------------------------------------------------------ detection */
  function describeDetection() {
    if (!state) return;
    var d = state.detection;
    var o = d.ollama;
    var situation = $("#brain-situation");
    var foot = $("#local-foot");
    if (o.serving && o.models.length) {
      situation.textContent = "Found " + o.models.length + (o.models.length === 1 ? " model" : " models") + " already installed on this machine.";
      foot.textContent = "ready: " + o.models.slice(0, 2).join(", ");
    } else if (o.installed) {
      situation.textContent = "Ollama is installed on this machine but not running. One click starts it and downloads the small model.";
      foot.textContent = "downloads about 0.6 GB, once";
    } else if (d.lmstudio.serving || d.nollama.serving) {
      situation.textContent = "Found a local model server on this machine.";
    } else if (d.lmstudio.installed) {
      situation.textContent = "LM Studio is installed but not serving. Or install Ollama below — both are free.";
      foot.textContent = "needs a running local server";
    } else {
      situation.textContent = "No model on this machine yet. Installing one is free, private, and stays on your computer.";
      foot.textContent = "needs Ollama, about 0.6 GB";
    }
    var keys = state.free_keys || {};
    var have = Object.keys(keys).filter(function (k) { return keys[k]; });
    $("#freekey-foot").textContent = have.length ? "already set: " + have.join(", ") : "no key set yet";
    /* Reopen on whatever the person already chose; the default stays local. */
    var stored = state.brain && state.brain.kind;
    choice = stored === "freekey" && have.length ? "freekey" : "local";
    pickChoice(choice);
    pickProfile(state.permissions || "ask");
    pickProvider(provider);
    $("#nameinput").value = state.name || state.defaults.name || "Hermus";
    var label = $("#enter-companion");
    if (label) label.textContent = "Open " + ($("#nameinput").value || "Hermus");
  }

  function describePrepare() {
    var p = state && state.prepare ? state.prepare : null;
    var installed = !!(state && state.detection && state.detection.ollama.installed);
    var line = $("#prepare-line");
    var log = $("#prepare-log");
    var btn = $("#prepare-btn");
    if (!p || p.phase === "idle") {
      line.textContent = installed
        ? "Ollama is installed but not running. One click starts it and downloads the small model."
        : "Ollama is not installed yet. Install it from ollama.com/download (free, no account), then press Check again.";
      btn.textContent = installed ? "Start it and download the small model" : "Check again";
      show(log, false);
      show(btn, true);
      return;
    }
    if (p.phase === "running") {
      line.textContent = p.detail || "working…";
      show(btn, false);
      show(log, true);
      log.textContent = (p.log || []).join("\n");
      log.scrollTop = log.scrollHeight;
      window.setTimeout(function () {
        if (step === "brain" && !prepared) pollPrepare();
      }, 1200);
      return;
    }
    line.textContent = p.ok ? "Ready. Nothing else to download." : (p.detail || "That did not finish.");
    btn.textContent = p.phase === "needs_install" ? "Check again" : "Try again";
    show(log, true);
    log.textContent = (p.log || []).join("\n");
    show(btn, !p.ok);
    if (p.ok) prepared = true;
  }

  function pollPrepare() {
    fetch("/api/v1/onboarding/prepare/status")
      .then(function (r) { return r.json(); })
      .then(function (p) {
        if (state) state.prepare = p;
        describePrepare();
        if (p.done) refresh().then(function () { if (p.ok) prepared = true; });
      })
      .catch(function () { window.setTimeout(pollPrepare, 2000); });
  }

  function refresh() {
    return fetch("/api/v1/onboarding/state")
      .then(function (r) { return r.json(); })
      .then(function (s) { state = s; describeDetection(); describePrepare(); return s; });
  }

  /* -------------------------------------------------------------- actions */
  function continueBrain() {
    if (choice === "freekey") {
      var key = ($("#keyinput").value || "").trim();
      if (!key) { $("#keyinput").focus(); return; }
      post("/api/v1/onboarding/brain", { choice: "freekey", provider: provider, key: key }).then(function (r) {
        if (!r.ok) { $("#key-hint").textContent = r.error || "That key was not accepted."; return; }
        go("permissions");
      });
      return;
    }
    if (choice === "stub") {
      post("/api/v1/onboarding/brain", { choice: "stub" }).then(function () { go("permissions"); });
      return;
    }
    /* Nothing local is serving a model yet: show the preparation step and
       start it here, so the download is announced rather than a surprise. */
    if (!localReady()) {
      show($("#prepare-field"), true);
      post("/api/v1/onboarding/prepare").then(function () { pollPrepare(); });
      describePrepare();
      return;
    }
    post("/api/v1/onboarding/brain", { choice: "local" }).then(function (r) {
      if (!r.ok) {
        $("#brain-situation").textContent = r.error || "That did not resolve a model.";
        if (r.fix) $("#local-foot").textContent = r.fix;
        return;
      }
      go("permissions");
    });
  }

  function continuePermissions() {
    post("/api/v1/onboarding/permissions", { profile: profile }).then(function () { go("name"); });
  }

  function continueName() {
    var name = ($("#nameinput").value || "").trim() || "Hermus";
    $("#enter-companion").textContent = "Open " + name;
    post("/api/v1/onboarding/name", { name: name }).then(function () { go("first"); });
  }

  /* ------------------------------------------------------------ first words */
  var streamed = false;

  function startFirst() {
    if (streamed) return;
    streamed = true;
    var log = $("#square-log");
    log.innerHTML = "";
    var said = document.createElement("span");
    said.className = "said";
    said.textContent = "you · " + ($("#firstinput").value || "What can you do?");
    log.appendChild(said);
    var who = document.createElement("span");
    who.className = "who";
    who.textContent = (($("#nameinput").value || "Hermus")).toUpperCase();
    log.appendChild(who);
    var body = document.createElement("span");
    log.appendChild(body);
    send();
  }

  function send() {
    var text = ($("#firstinput").value || "").trim() || "What can you do?";
    var log = $("#square-log");
    var out = log.querySelector("span:last-child");
    out.textContent = "";
    $("#send-first").disabled = true;
    $("#first-fix").classList.add("hidden");
    $("#first-note").textContent = "Talking to the model you chose…";
    answered = false;
    failed = false;

    fetch("/api/v1/onboarding/hello", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text })
    }).then(function (resp) {
      if (!resp.body || !resp.body.getReader) throw new Error("streaming is unavailable in this browser");
      var reader = resp.body.getReader();
      var decoder = new TextDecoder();
      var buf = "";
      var caret = document.createElement("i");
      caret.className = "caret";
      out.appendChild(caret);

      function handle(chunk) {
        buf += chunk;
        var parts = buf.split("\n\n");
        buf = parts.pop();
        parts.forEach(function (block) {
          var ev = "message", data = "";
          block.split("\n").forEach(function (line) {
            if (line.indexOf("event: ") === 0) ev = line.slice(7).trim();
            else if (line.indexOf("data: ") === 0) data += line.slice(6);
          });
          if (!data) return;
          var payload;
          try { payload = JSON.parse(data); } catch (e) { return; }
          onEvent(ev, payload, out, caret);
        });
      }

      function pump() {
        return reader.read().then(function (r) {
          if (r.done) return;
          handle(decoder.decode(r.value, { stream: true }));
          return pump();
        });
      }
      return pump();
    }).catch(function (err) {
      fail("Could not reach the model — " + err.message, "");
    }).finally(function () {
      $("#send-first").disabled = false;
    });
  }

  function onEvent(ev, payload, out, caret) {
    if (ev === "status") {
      if (payload.state === "loading") {
        $("#first-model").textContent = payload.pickup || ("loading " + payload.model);
        $("#first-note").textContent = "Loading the model into memory — the first load is the slow one.";
      } else if (payload.state === "streaming") {
        $("#first-note").textContent = "First token in " + payload.first_token_ms + " ms.";
      }
      return;
    }
    if (ev === "token") {
      out.insertBefore(document.createTextNode(payload.text), caret);
      return;
    }
    if (ev === "error") {
      fail(payload.message, payload.fix || "");
      return;
    }
    if (ev === "done") {
      if (caret && caret.parentNode) caret.parentNode.removeChild(caret);
      if (payload.ok) {
        answered = true;
        $("#first-model").textContent = payload.pickup || "";
        $("#first-note").textContent = "That answer came from " + payload.model + ".";
        if (payload.complete) {
          var btn = $("#enter-companion");
          btn.disabled = false;
          btn.focus();
        } else {
          $("#first-note").textContent = "Answered, but the run is not complete yet — open the screen and finish from there.";
          $("#enter-companion").disabled = false;
        }
      } else if (!answered) {
        fail(payload.reason || "the model did not answer", "");
      }
    }
  }

  function fail(message, fix) {
    /* One failure statement per attempt: the `error` event carries the reason
       and the command, the `done` event carries only the reason, so a second
       call must not overwrite the useful one with the shorter one. */
    if (answered || failed) return;
    failed = true;
    var log = $("#square-log");
    var out = log.querySelector("span:last-child");
    var caret = log.querySelector(".caret");
    if (caret && caret.parentNode) caret.parentNode.removeChild(caret);
    if (out) out.textContent = message + " ";
    var fixLine = $("#first-fix");
    if (fix) {
      fixLine.textContent = "Fix it with: " + fix;
      fixLine.classList.remove("hidden");
    }
    $("#first-note").textContent = "No model answered, so this run stays incomplete.";
    $("#enter-companion").disabled = true;
  }

  /* --------------------------------------------------------------- wiring */
  function bind() {
    $$("#brain-choices .choice").forEach(function (c) {
      c.addEventListener("click", function () { pickChoice(c.dataset.choice); });
      c.addEventListener("dblclick", function () { pickChoice(c.dataset.choice); continueBrain(); });
    });
    $$("#provider-seg button").forEach(function (b) {
      b.addEventListener("click", function () { pickProvider(b.dataset.provider); });
    });
    $$("#perm-choices .choice").forEach(function (c) {
      c.addEventListener("click", function () { pickProfile(c.dataset.profile); });
    });
    $("#next-brain").addEventListener("click", continueBrain);
    $("#next-perm").addEventListener("click", continuePermissions);
    $("#next-name").addEventListener("click", continueName);
    $("#back-perm").addEventListener("click", function () { go("brain"); });
    $("#back-name").addEventListener("click", function () { go("permissions"); });
    $("#back-first").addEventListener("click", function () { streamed = false; go("name"); });
    $("#prepare-btn").addEventListener("click", function () {
      post("/api/v1/onboarding/prepare").then(function () { pollPrepare(); });
    });
    $("#send-first").addEventListener("click", function () {
      streamed = true;
      var log = $("#square-log");
      log.innerHTML = "";
      var said = document.createElement("span"); said.className = "said";
      said.textContent = "you · " + ($("#firstinput").value || "What can you do?");
      log.appendChild(said);
      var who = document.createElement("span"); who.className = "who";
      who.textContent = ($("#nameinput").value || "Hermus").toUpperCase();
      log.appendChild(who);
      log.appendChild(document.createElement("span"));
      send();
    });
    $("#enter-companion").addEventListener("click", function () {
      post("/api/v1/onboarding/finish").then(function (r) {
        if (r.ok) window.location.href = carryToken(r.next);
        else { $("#first-note").textContent = r.reason || "Not finished yet."; }
      });
    });

    document.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" || e.shiftKey) return;
      var tag = (e.target.tagName || "").toLowerCase();
      if (step === "first") {
        if (tag === "input") { e.preventDefault(); $("#send-first").click(); }
        return;
      }
      if (tag === "button" && e.target.id === "prepare-btn") return;
      e.preventDefault();
      if (step === "brain") continueBrain();
      else if (step === "permissions") continuePermissions();
      else if (step === "name") continueName();
    });
  }

  /* A launcher may open this page with the gateway token in the query. The token
     belongs to the browser, not to this page: keep it where the room looks, and
     carry it into whatever we navigate to, or the door we open leads to a room
     that answers 401 to every turn. */
  function carryToken(href) {
    var token = "";
    try {
      token = new URLSearchParams(location.search).get("token") || localStorage.getItem("hermus_gateway_token") || "";
      if (token) localStorage.setItem("hermus_gateway_token", token);
    } catch (e) {
      return href || "/companion";
    }
    if (!token || !href) return href || "/companion";
    return href + (href.indexOf("?") === -1 ? "?" : "&") + "token=" + encodeURIComponent(token);
  }

  window.addEventListener("DOMContentLoaded", function () {
    bind();
    refresh().then(function () {
      if (state.complete) { window.location.href = carryToken("/companion"); return; }
      go("brain");
    }).catch(function () {
      $("#brain-situation").textContent = "Could not reach the gateway. Reload this page.";
    });
  });
})();
