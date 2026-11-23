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
    player.reset();
    speak.spoken = 0;
    speak.source = "";

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
          speak.stream(raw);
          toBottom(true);
          return;
        }
        if (ev === "agent_response") {
          /* The runtime's own answer wins over the delta accumulation: deltas
             can be cut mid-sentence by a step boundary. */
          if (d.text) {
            seen = true;
            answer(reply, plain(d.text));
            speak.stream(raw);
            speak.remainder(d.text);
          }
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
      if (!sawApproval) speak.flush();
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
    player.reset();
    if (jobId) {
      fetch("/jobs/" + encodeURIComponent(jobId) + "/cancel", { method: "POST", headers: headers() })
        .catch(function () { /* the abort already ended the socket */ });
    }
    under("stopped");
    setBusy(false);
    $("#say").focus();
  }

  /* ------------------------------------------------------------------ voice */

  /* Speech is an enhancement, never a dependency: if any piece of it is missing
     the room says which piece in one sentence and the text path is untouched.

     Three moving parts:
       player  — one <audio> element and a queue of clips, cut off at once when
                 the person speaks or presses stop;
       speak   — asks the gateway for a clip per sentence as the answer streams,
                 so the first words play while the rest is still arriving;
       ear     — microphone capture, 16 kHz PCM16 straight from an AudioWorklet
                 or a ScriptProcessor, with live partial text on the way.
  */

  var voice = { stt: null, tts: null, reason: "", note: "" };

  var player = (function () {
    var el = null;
    var queue = [];
    var generation = 0;
    return {
      element: function () {
        if (!el) {
          el = new Audio();
          el.preload = "auto";
          el.addEventListener("playing", function () {
            mood("speaking");
            notePlaying(true);
          });
          el.addEventListener("ended", function () { next(); });
          el.addEventListener("pause", function () { notePlaying(false); });
        }
        return el;
      },
      push: function (clip, gen) {
        if (gen < generation) return;              /* already interrupted */
        generation = gen;
        queue.push(clip);
        if (queue.length === 1) this.start();
        return queue.length;
      },
      start: function () {
        var clip = queue[0];
        if (!clip) return;
        var audio = this.element();
        audio.src = clip.audio_url;
        var played = audio.play();
        if (played && played.catch) played.catch(function () { /* autoplay refused; text remains */ });
      },
      next: function () {
        queue.shift();
        if (!queue.length) { mood(busy ? "busy" : "idle"); return; }
        this.start();
      },
      reset: function () {
        queue = [];
        generation += 1;
        if (el) { el.pause(); el.removeAttribute("src"); el.load(); }
        mood(busy ? "busy" : "idle");
      },
      busy: function () { return queue.length > 0; }
    };
  })();

  function notePlaying(playing) {
    if (!voice.tts || !voice.tts.available) return;
    fetch("/voice/live/playing", {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ session_id: sessionId, playing: !!playing })
    }).catch(function () { /* the mood is cosmetic; never a failure */ });
  }

  var speak = {
    spoken: 0,       /* characters of the current answer already handed to the gateway */
    source: "",      /* the text those characters came from */
    off: false,      /* set once when there is no local voice at all */
    generation: 0,

    /* Speak whatever complete sentences have arrived, and nothing twice. */
    stream: function (text) {
      if (this.off || !voice.tts || !voice.tts.available) return;
      var body = String(text || "");
      if (body.length <= this.spoken) return;
      var pending = body.slice(this.spoken);
      var cut = -1;
      for (var i = 0; i < pending.length - 1; i++) {
        var ch = pending.charAt(i);
        if (ch === "." || ch === "?" || ch === "!" || ch === "\n") {
          if (pending.charAt(i + 1) === " ") { cut = i + 1; break; }
        }
      }
      if (cut === -1) return;
      var sentence = pending.slice(0, cut).trim();
      if (!sentence) { this.spoken += cut; return; }
      this.spoken += cut;
      this.source = body.slice(0, this.spoken);
      this.ask(sentence);
    },

    /* The final answer can differ from the deltas; speak only what is new. */
    remainder: function (finalText) {
      if (this.off || !voice.tts || !voice.tts.available) return;
      var body = String(finalText || "");
      if (!this.spoken) return;
      var heard = plain(this.source || "").slice(-24);
      if (!heard) return;
      if (body.indexOf(heard) === -1) return;
      var tail = plain(body).slice(plain(this.source).length).trim();
      if (tail) this.ask(tail);
      this.spoken = body.length;
    },

    /* Anything still unspoken when the turn ends is worth saying. */
    flush: function () {
      if (this.off || !voice.tts || !voice.tts.available) return;
      if (this.spoken) return;
      var text = $("#thread .exchange:last-child .reply");
      var body = text ? text.textContent : "";
      if (body) this.ask(body);
    },

    ask: function (text) {
      var self = this;
      var gen = player.generation;
      fetch("/voice/speak", {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ text: text, session_id: sessionId })
      }).then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (body) {
          if (body && body.redacted) under("kept a secret out of speech", "");
          if (!body || !body.success) {
            if (body && body.error && !self.off) {
              self.off = true;
              under("speech stays on screen — " + body.error, "bad");
            }
            return;
          }
          (body.clips || []).forEach(function (clip) { player.push(clip, gen); });
        })
        .catch(function () { /* the answer is already on screen */ });
    }
  };

  /* the microphone ------------------------------------------------------- */

  var ear = {
    state: "off",
    listenId: "",
    ctx: null,
    node: null,
    sink: null,
    stream: null,
    source: null,
    rate: 16000,
    pending: [],
    pendingSamples: 0,
    sentSamples: 0,
    totalSamples: 0,
    inFlight: false,
    speechHeardAt: 0,
    lastVoiceAt: 0,
    startedAt: 0,
    latched: false,
    text: "",

    supported: function () {
      return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia &&
        (window.AudioContext || window.webkitAudioContext));
    },

    start: function () {
      var self = this;
      if (this.state !== "off") return Promise.resolve();
      if (!voice.stt || !voice.stt.available) return Promise.reject(new Error(voice.reason || "no local speech-to-text"));
      /* Speaking first, listening second: the person interrupting is the point. */
      player.reset();
      fetch("/voice/speak/stop", {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ session_id: sessionId })
      }).catch(function () {});

      return fetch("/voice/live/start", {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ session_id: sessionId })
      }).then(function (r) { return r.json(); }).then(function (body) {
        if (!body || !body.success) throw new Error(body && (body.error || body.fix) || "the microphone could not open");
        self.listenId = body.id;
        return navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true }
        });
      }).then(function (stream) {
        self.stream = stream;
        var Ctx = window.AudioContext || window.webkitAudioContext;
        var ctx;
        try { ctx = new Ctx({ sampleRate: 16000 }); } catch (e) { ctx = new Ctx(); }
        self.ctx = ctx;
        self.rate = ctx.sampleRate;
        self.source = ctx.createMediaStreamSource(stream);
        self.node = ctx.createScriptProcessor(2048, 1, 1);
        self.node.onaudioprocess = function (event) { self.frame(event.inputBuffer.getChannelData(0)); };
        /* A ScriptProcessor only runs while it reaches the destination, and the
           microphone must never reach the speakers — hence a silent sink. */
        self.sink = ctx.createGain();
        self.sink.gain.value = 0;
        self.source.connect(self.node);
        self.node.connect(self.sink);
        self.sink.connect(ctx.destination);
        self.pending = [];
        self.pendingSamples = 0;
        self.sentSamples = 0;
        self.totalSamples = 0;
        self.text = "";
        self.startedAt = Date.now();
        self.speechHeardAt = 0;
        self.lastVoiceAt = 0;
        self.state = "listening";
        micState("listening");
        mood("listening");
        $("#say").value = "";
        under("listening — hold, or talk and pause", "");
        return true;
      }).catch(function (err) {
        self.teardown();
        var message = err && err.name === "NotAllowedError"
          ? "microphone permission was refused — allow it in the address bar to talk"
          : (err && err.message) || "the microphone could not open";
        under(message, "bad");
        micState("off");
        mood("idle");
        throw err;
      });
    },

    /* One 2048-sample block of real microphone audio. */
    frame: function (samples) {
      var now = Date.now();
      var level = 0;
      for (var i = 0; i < samples.length; i++) level += samples[i] * samples[i];
      level = Math.sqrt(level / samples.length);
      barLevel(level);
      if (level > 0.012) {
        if (!this.speechHeardAt) this.speechHeardAt = now;
        this.lastVoiceAt = now;
      }
      var pcm = toPcm16(samples, this.rate, 16000);
      if (pcm.length) {
        this.pending.push(pcm);
        this.pendingSamples += pcm.length / 2;
        this.totalSamples += pcm.length / 2;
      }
      /* Partial cadence: soon after speech starts, then steady. */
      var target = this.sentSamples === 0 ? 480 : 700;
      var waited = ((this.totalSamples - this.sentSamples) / 16000) * 1000;
      if (!this.inFlight && waited >= target) this.send();
      if (this.speechHeardAt && now - this.lastVoiceAt > 900) this.finish();
      else if (now - this.startedAt > 30000) this.finish();
    },

    send: function () {
      var self = this;
      if (!this.pending.length) return;
      var blob = concatBytes(this.pending);
      this.pending = [];
      this.sentSamples = this.totalSamples;
      this.inFlight = true;
      fetch("/voice/live/chunk", {
        method: "POST",
        headers: headers({ "X-Hermus-Listen": this.listenId, "Content-Type": "application/octet-stream" }),
        body: blob
      }).then(function (r) { return r.json(); }).then(function (body) {
        self.inFlight = false;
        if (body && body.text && body.text !== self.text) {
          self.text = body.text;
          $("#say").value = self.text;
        }
        if (self.state === "stopping") self.close();
      }).catch(function () {
        self.inFlight = false;
        if (self.state === "stopping") self.close();
      });
    },

    finish: function () {
      if (this.state !== "listening") return;
      this.state = "stopping";
      micState("transcribing");
      mood(busy ? "busy" : "idle");
      if (this.pending.length) this.send();
      else this.close();
    },

    close: function () {
      var self = this;
      var id = this.listenId;
      this.state = "closed";
      this.teardown();
      fetch("/voice/live/stop", {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ id: id })
      }).then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (body) {
          self.state = "off";
          micState("off");
          if (!body || !body.success) {
            under(body && body.error === "no speech detected" ? "heard nothing — try again" : (body && body.error) || "nothing was heard", "quiet");
            return;
          }
          var text = String(body.text || self.text || "").trim();
          $("#say").value = "";
          if (!text) { under("heard nothing", "quiet"); return; }
          under("");
          /* Talking over an answer is an interruption: end the turn first so the
             person's words become the next turn instead of being dropped. */
          if (busy) stop();
          say(text);
        })
        .catch(function () {
          self.state = "off";
          micState("off");
          under("the transcript did not come back", "bad");
        });
    },

    teardown: function () {
      if (this.node) { try { this.node.disconnect(); } catch (e) {} this.node.onaudioprocess = null; }
      if (this.source) { try { this.source.disconnect(); } catch (e) {} }
      if (this.sink) { try { this.sink.disconnect(); } catch (e) {} }
      if (this.stream) this.stream.getTracks().forEach(function (t) { t.stop(); });
      if (this.ctx) { try { this.ctx.close(); } catch (e) {} }
      this.node = this.source = this.sink = this.stream = this.ctx = null;
      this.inFlight = false;
      this.speechHeardAt = 0;
      this.listenId = "";
      barLevel(0);
    },

    abort: function () {
      var id = this.listenId;
      this.state = "off";
      this.teardown();
      micState("off");
      mood(busy ? "busy" : "idle");
      if (id) {
        fetch("/voice/live/cancel", {
          method: "POST",
          headers: headers({ "Content-Type": "application/json" }),
          body: JSON.stringify({ id: id })
        }).catch(function () {});
      }
    }
  };

  function toPcm16(samples, fromRate, toRate) {
    var ratio = fromRate / toRate;
    var count = Math.floor(samples.length / ratio);
    var out = new DataView(new ArrayBuffer(count * 2));
    for (var i = 0; i < count; i++) {
      var pos = i * ratio;
      var low = Math.floor(pos);
      var frac = pos - low;
      var value = samples[low] * (1 - frac) + (samples[low + 1] !== undefined ? samples[low + 1] : samples[low]) * frac;
      value = Math.max(-1, Math.min(1, value));
      out.setInt16(i * 2, value < 0 ? value * 0x8000 : value * 0x7fff, true);
    }
    return new Uint8Array(out.buffer);
  }

  function concatBytes(list) {
    var size = 0;
    list.forEach(function (part) { size += part.length; });
    var out = new Uint8Array(size);
    var at = 0;
    list.forEach(function (part) { out.set(part, at); at += part.length; });
    return out;
  }

  function micState(state) {
    var mic = $("#mic");
    if (!mic) return;
    mic.setAttribute("data-state", state);
    mic.setAttribute("aria-label",
      state === "listening" ? "Stop listening" :
        state === "transcribing" ? "Transcribing" : "Hold to talk");
    if (voice.stt && voice.stt.available) mic.setAttribute("aria-disabled", "false");
  }

  function barLevel(level) {
    var bar = document.querySelector("#mic .mic-bar");
    if (!bar) return;
    var scale = 1 + Math.min(0.45, level * 3.2);
    bar.style.transform = "scale(" + scale.toFixed(3) + ")";
  }

  /* The control's whole contract: it is disabled *before* the click, with the
     one sentence that would enable it. */
  function voiceStatus() {
    return fetch("/voice/live/status", { headers: headers() })
      .then(function (r) { return r.json(); })
      .then(function (body) {
        voice.stt = (body && body.stt) || { available: false };
        voice.tts = (body && body.tts) || { available: false };
        var mic = $("#mic");
        if (!mic) return body;
        if (!ear.supported()) {
          mic.setAttribute("aria-disabled", "true");
          mic.title = "This browser cannot open a microphone";
          return body;
        }
        if (!voice.stt.available) {
          voice.reason = voice.stt.fix || voice.stt.reason || "local speech-to-text is not installed";
          mic.setAttribute("aria-disabled", "true");
          mic.title = "Voice is off: " + voice.reason;
          under("voice needs " + voice.reason, "");
          return body;
        }
        mic.setAttribute("aria-disabled", "false");
        mic.title = "Hold to talk";
        if (voice.stt.first_use_download) {
          under(voice.stt.first_use_download, "");
        } else if (!voice.tts.available) {
          under("answers stay on screen — " + (voice.tts.fix || voice.tts.reason || "no local voice"), "");
        }
        return body;
      })
      .catch(function () {
        var mic = $("#mic");
        if (mic) { mic.setAttribute("aria-disabled", "true"); mic.title = "Voice is off: the gateway did not answer"; }
        return null;
      });
  }

  /* Press and hold, or press once and talk. A quick tap latches the microphone
     open until the next press or the silence detector closes it. */
  function wireMic() {
    var mic = $("#mic");
    if (!mic) return;
    var pressedAt = 0;

    function begin(event) {
      if (event) event.preventDefault();
      if (mic.getAttribute("aria-disabled") === "true") {
        under("voice needs " + (voice.reason || "a local speech recognizer"), "bad");
        return;
      }
      if (ear.state === "listening") { ear.finish(); return; }
      if (ear.state === "transcribing") return;
      pressedAt = Date.now();
      ear.latched = false;
      ear.start().catch(function () {});
    }

    function end(event) {
      if (event) event.preventDefault();
      if (ear.state !== "listening") return;
      var held = Date.now() - pressedAt;
      if (held < 300) { ear.latched = true; return; }
      ear.finish();
    }

    mic.addEventListener("pointerdown", begin);
    mic.addEventListener("pointerup", end);
    mic.addEventListener("pointerleave", function () { if (!ear.latched) end(); });
    mic.addEventListener("click", function (e) {
      /* A latched press (quick tap) ends on the next click. */
      if (ear.latched && ear.state === "listening") { e.preventDefault(); ear.finish(); }
    });
    mic.addEventListener("keydown", function (e) {
      if (e.key === " " || e.key === "Enter") { e.preventDefault(); begin(e); }
    });
    mic.addEventListener("keyup", function (e) {
      if (e.key === " " || e.key === "Enter") end(e);
    });
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
  var paletteField = null;

  /* Built on first open so the room's own markup keeps exactly one input line
     (a contract tests/test_first_run_onboarding.py pins). */
  function paletteInput() {
    if (paletteField) return paletteField;
    var el = document.createElement("input");
    el.id = "paletteInput";
    el.type = "text";
    el.autocomplete = "off";
    el.placeholder = "Search a HERMUS surface…";
    el.setAttribute("aria-label", "Search HERMUS surfaces");
    el.addEventListener("input", function () {
      paletteActive = 0;
      renderPalette(el.value);
    });
    el.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); paletteMove(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); paletteMove(-1); }
      else if (e.key === "Enter") { e.preventDefault(); paletteGo(); }
    });
    $("#paletteSearch").appendChild(el);
    paletteField = el;
    return el;
  }

  function showPalette(on) {
    var host = $("#palette");
    if (!host) return;
    host.classList.toggle("open", !!on);
    host.setAttribute("aria-hidden", on ? "false" : "true");
    if (on) {
      paletteReturn = document.activeElement;
      paletteActive = 0;
      renderPalette("");
      paletteInput().value = "";
      setTimeout(function () { paletteInput().focus(); }, 0);
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
    renderPalette(paletteField ? paletteField.value : "");
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

    wireMic();
    voiceStatus();

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
