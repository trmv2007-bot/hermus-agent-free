# What makes an assistant feel like JARVIS — research findings

Research only. No code, no implementation commits. Target context: local agent UI with a
central glowing orb, pan/zoom room, and panels; Windows 11, RTX 3050 8GB, free local models.

---

## 0. The one-sentence answer

A JARVIS is a **mixed-initiative system that decides when to speak, ends its turn, and shows
honest state**. A chatbot is a **reactive text box that answers everything, at length, and
never decides anything.** Almost every "it feels magical" complaint is one of those three
things failing.

The four axes below are ranked by how much each one moves the felt experience:
**when it speaks > how long it takes to speak > that it can be interrupted > what it says.**

---

## 1. Persona

### What the research says

- **Users project a persona onto your agent whether you design one or not.** Google's
  conversation-design guidance makes this the opening argument: "Users will project a persona
  onto your Action whether you plan for one or not. So it's in your best interest to
  purposefully design the experience you want users to perceive, instead of leaving it up to
  chance." Their method: brainstorm adjectives → narrow to **4–6 core traits** → pick one
  character that embodies them → write **one paragraph, no more**.
- **Traits, not demographics.** Google explicitly says to avoid specifying gender or age,
  because they "almost never critically define or differentiate a persona" and fixing gender
  upfront halves your voice options. The persona's job is to answer one question: *"What
  would this persona say or do in this situation?"* It is a writing tool, not a costume.
- **The persona is also the mental model for capability.** A good persona gives users a
  handle for "what can this thing do and how does it work."
- **Perceived social intelligence is behavioral, not internal.** A 2026 survey of 200 US
  adults (arXiv 2605.29938) asked what makes an agent seem socially intelligent. Top
  answers were all observable behaviors: navigating conversation 36%, humanlike
  communication 31.5%, current-context awareness 30%, recognizing emotion 29.5%, expressing
  empathy 27%, **proactively offering help 25%**, task competence 23.5%. Only 12% mentioned
  remembering across sessions, 14.5% social norms, 9.5% the agent having its own emotions.
  The *lowest*-rated ability was "acts with purpose and follows its own intentions" (2.94/5).
  **Read: build behavior, not backstory.**
- **The same survey found a support–adoption gap**: people endorsed socially intelligent
  agents for *others* far more than for their own use, and their top concerns were privacy,
  reduced human connection, and overreliance. An ambient personal agent has to earn this.
- **Anthropomorphism cuts both ways.** Waterloo's "Genie in the Bottle" work found users
  attribute lifelike qualities to assistants (age, hair, expression) and disclose more as a
  result. A glowing orb with a voice is squarely in that zone. The standard mitigation in the
  literature is to keep the system's *bot-ness* explicit while using humanlike affordances —
  i.e. never let the interface imply a human is on the other end.
- **Character must be consistent across surfaces, or it's worse than none.** Persona-fidelity
  research (PersonaGym, atomic-level OOC detection) treats out-of-character output as a
  measurable failure, not a vibe. If the system prompt is dry and wry but the TTS voice is
  chipper, or the orb's labels are corporate, you have built a character with a multiple
  personality.

### What JARVIS specifically does that a chatbot doesn't

- Short declaratives. No hedging, no "Great question", no restating the request.
- **Prioritizes and reorders** so the user hears the *consequence* before the mechanism.
  Film-dialogue analyses of the Iron Man scripts note exactly this: background information
  arrives *after* the impact, not before.
- Addresses the user by name sparingly and never asks permission to act.
- Rarely editorialize about the user's feelings.
- It has working memory of the room and the session, and refers to it by pointing.

### The "bot-ness" tension, stated plainly

The same expressiveness that makes it feel like JARVIS is what makes users over-trust it.
The literature's own note is the useful design constraint: **polished, confident explanations
increase overreliance precisely because they remove the friction that would prompt a
double-check.** A JARVIS that is always composed is a JARVIS that projects confidence the
underlying system doesn't have. Every visual state must be backed by a machine-checkable
signal — including the orb.

---

## 2. Proactivity — *when it speaks*

This is the axis with the most evidence and the biggest gap between theory and shipped
systems.

### The rule everything descends from

Horvitz's 1999 mixed-initiative model, restated by the 2026 TMLR survey
(*When Should the Agent Speak?*):

> **intervene ⇔ E[benefit of acting] − E[cost of interrupting] > θ**

Two things to hold onto: **silence is a first-class action** (and gets time-discounted), and
the *cost* term is expected over an **inferred attentional state**, not a constant.

### What interruption actually costs (this is why timing dominates)

- A single alert costs a **20–25 minute** cognitive recovery arc, and **27% of interrupted
  tasks are never resumed** (Iqbal & Horvitz 2007). The commonly cited "23 minutes to regain
  full focus" figure is from the UCI interruption literature and is the same order of
  magnitude; treat both as "tens of minutes," not as a precise constant.
- Batching to **3 notifications/day** improved end-of-day productivity with a moderate effect
  size (CHI 2016). Halving notification rate cut reported stress ~6.5 points and improved
  morning HRV by 5–6 ms.
- **The breakpoint principle**: interruption cost depends on *where in the task* it lands.
  Interrupt at a task boundary (step complete, thought finished), not mid-step. A time delay
  is a poor proxy for a boundary.

### Where the field actually is in 2026

The 2026 survey's central finding is that the cost term **disappeared** from modern systems:

| Priced today | By | Status |
|---|---|---|
| False alarm | PRISM (2026), Bayes risk | Works: false-alarm 50.22% → 22.94%, F1 66.47 → 86.61 |
| Cognitive load | Precision Proactivity (2025) | Measured, not wired into the gate |
| Social violation | EgoSocial (2025) | Named, not quantified |
| Compute | WakeAnchor, ProAgent (2026) | Priced — and it's *our* compute, not the user's cost |
| **Attentional state** | **nobody** | **untouched since 1999** |
| **Cost of silence** | **nobody** | **untouched since 1999** |

Three hard numbers worth internalizing:

1. **PRISM's cost ratio is a deployment constant, swept by the authors, fixed for the whole
   deployment.** It charges the same price for interrupting someone staring out a window and
   someone mid-sentence in a meeting. That missing state-dependence *is* the 1999 insight.
2. **Gating on "user accepted it" is a trap.** Acceptance runs 0.676 when the moment is well
   chosen, 0.444 at random, 0.388 when anti-timed — and gating on it alone drives PRISM's
   false-alarm rate to **62.50%**, because people cheerfully accept useful things delivered at
   a terrible moment. Acceptance measures *I wanted this*, never *I wanted this now*.
3. **One-sided reward produces mutism.** Penalize false positives without rewarding timely
   help and GPT-4o's recall collapsed **98.11% → 56.76%**. The model found silence was the
   optimum faster than its designers did.
4. **Models read the room but cannot pick the moment.** EgoSocial: 51.65–56.08 macro-F1
   recognizing that a social interaction is under way, but **12.50–17.67%** on intervention
   timing. Expect to hand-tune this; it will not come free from a better base model.

### The escalation ladder that falls out of the evidence

L1 = push everything as it arrives. L2 = prioritize, group, batch, and hold personal items
until the user is alone. L3 = lifelong learning of habits to refine placement.

An actual living-lab study of L1 vs L2 (Miksik et al., arXiv 2005.01322) found:

- L1 (immediate, unprioritized, all updates equal priority) was experienced as **invasive
  and disturbing**. Verbatim: "I found it invasive, couldn't concentrate on the tasks" /
  "too many updates, barely had time to think" / "it just threw information at me in a random
  order."
- L2 (prioritized, batched, sensitive content deferred until alone) was **strongly
  preferred**: "Importance ranking is good"; users liked batched news.
- Users complained specifically about: random ordering, **no reason given for the update**,
  read-outs too long to follow, no control over volume or tempo. They asked for: pause,
  opt-out, "details or summary", "repeat the last update", "customize/filter".
- **The animated pre-announcement was loved**: users liked the device orienting toward them
  before speaking, calling it eye contact, and said once accustomed to it they *missed the
  first part* of any update given without it.

### The three-level initiation ladder (Zargham et al., CUI 2022, 15 participants)

For non-urgent proactive speech, in order:

1. **Non-verbal cue** — a signal, then wait for the user to prompt. Least distracting;
   preferred when interrupting conversation between people.
2. **Verbal cue** — announce the *subject* only ("I noticed something about your usage —
   want me to share it?"), then wait for permission.
3. **Direct intervention** — reserved for urgent, health/safety, and time-saving cases.

Findings: 12 of 15 said they'd disconnect the device over an uninvited, unpermissioned
intervention. The recurring sentiment is the **proactivity dilemma** — "Very useful but very
scary." On agency: "I'm a person and I decide for my life. AI should not decide for me."
Control requests: temporary pause, condition on who's in the room, time-of-day limits, a
proactivity slider.

A 2024 systematic review of 21 primary studies (Bérubé et al.) reached a sobering
conclusion: **only safety-critical and emergency situations showed clear benefit from
proactivity; everything else was mixed.** Their context model is worth copying anyway:
*activity/status, time, location, people* → *initiation* (direct vs indirect) → *action*
(signal, notification, question, suggestion, performance).

### Concrete batching rule from production practice

> If the human cannot take any action within the next 15–30 minutes that would change the
> outcome, batch it. If immediate action could change the outcome, notify in real time.

Plus the four DevOps-derived rules that transfer directly to a personal agent:

- **Actionability gate** — if a human can't act on it, don't interrupt. Log it.
- **Auto-resolution suppression** — if the agent fixed it before you could react, suppress
  the notification or fold it into a digest.
- **Correlation before dispatch** — group related events; clustering cuts volume ~70% in
  production deployments.
- **Feedback loop** — track which notifications get acted on and adjust.

### What a *mediator* should interrupt on

The JarvisBench line of work (arXiv 2607.16610, 2608.14870) is the most directly applicable
recent work: an always-on voice mediator over a long-horizon worker agent. Its checkpoints
fire on: **repeated tool failures, stalled progress, ambiguous observations, risky or
irreversible actions, and low-confidence finalization.** Note what is *not* in that list:
progress for progress's sake. "Updates on everything" is not proactivity, it's a log with a
mouth.

---

## 3. Voice

### Latency is a budget, and the bar is 200ms

- The human baseline is a **median turn gap of ~200 ms** across 10 languages (Stivers et al.
  2009). Human "standard maximum silence" in conversation is ~1 second (Jefferson). Smooth
  transitions occur in 200–250 ms (Heldner & Edlund).
- **A 2-second response is not perceived as a fast API call. It is perceived as an awkward
  silence.** The user can't see the pipeline, so every millisecond of transport, STT,
  reasoning and synthesis is billed to the pause.
- CUI 2025 (VR, 9 agents × 3 filler types × 3 latencies of 1.5s/4.0s/6.5s): delay
  significantly worsened perceived response time and broader perception metrics, and was
  **less bearable beyond 4 seconds.**

### The single most actionable finding for an orb UI

- **Natural conversational fillers** (a voice line + a gesture/posture change)
  **significantly improved perceived response time**, especially in high-delay conditions.
- **Artificial fillers** — a processing spinner and a processing sound effect — **did not
  significantly improve perceived response time at all.**

This is a direct indictment of decorative animation. An orb that pulses because a CSS timer
says so is an artificial filler and buys nothing perceptually. An orb that *changes state*
to reflect real work, paired with a short truthful voice line ("checking that now"), is the
natural-filler version and measurably helps.

### A real local-stack budget (RTX 4060 8GB, WSL2 — closest available proxy)

Warm, measured from end of user speech:

| Stage | Cumulative | Notes |
|---|---|---|
| endpointing decision (smart-turn v3.2, ONNX CPU) | ~220 ms | replaced a fixed 672 ms silence window; saved ~450 ms/turn at the cost of ~1 turn in 10 being cut short |
| faster-whisper large-v3-turbo, int8_float16 (1.6 GB) | 221 ms | largest single stage; the obvious next optimization |
| + first LLM token (Qwen3-4B-Instruct, 3.1 GB) | 259 ms | 38 ms TTFT |
| + first audio out (Kokoro-82M, 0.3 GB) | 409 ms | measured 393–411 ms over localhost; websocket hop ~12 ms |
| **end-to-end** | **~0.6 s** | author notes this is arithmetic, not a stopwatch |
| resident VRAM, all models loaded | ~4.0 GB of 8.2 GB | leaves headroom to have been wrong |

**What actually made it feel fast**: the LLM's output is streamed into a chunker that splits
at the first speakable boundary (~a dozen characters) and sends that fragment to TTS
immediately, with the rest still generating. **Time-to-first-audio, not time-to-completion,
is what the user hears.**

### Endpointing: the highest-leverage timing decision

A fixed N-millisecond silence window fails in both directions — it cuts off mid-sentence
pauses (thinking, recalling a number, reading a card) and, if lengthened to tolerate them,
appends the full timeout of dead air to every turn. Production endpointing layers three
signals:

1. **Acoustic** — VAD classifies each frame as speech/non-speech.
2. **Linguistic** — the streaming transcript predicts completion vs continuation
   ("I want to pay my" → continue; "I want to pay my bill" → complete).
3. **Behavioral** — a tiered policy: on *medium* confidence, start reasoning **speculatively
   but do not speak**; on *high* confidence, commit; if the user resumes, discard the
   speculative work silently.

The speculative tier is what makes an agent simultaneously patient and fast.

### Interruption / barge-in

- **Two layers must both fire.** *Media layer:* the instant inbound speech is detected during
  playback, stop/mute outbound audio and flush the playback buffer. *Logic layer:* cancel or
  invalidate **everything in flight** — pending LLM generation, queued synthesis, unexecuted
  function calls. **Stale responses are the signature failure of half-implemented barge-in.**
- After cancelling, re-enter listening with conversational state intact. The partial response
  belongs in history *as an interrupted turn*, not erased, so the reasoning layer knows what
  the user did and did not hear.
- Metric worth instrumenting: **barge-in latency** — first interrupting syllable → agent
  silence. It correlates directly with whether users describe the agent as "listening."
- **Echo is the hard part.** While the assistant speaks, the mic hears the assistant. The
  working solution compares incoming audio against what the speaker was playing ~100 ms
  earlier and opens the gate only on audio the echo can't explain. A wake word is the reliable
  path in — measured against a recording of the assistant talking through the speaker, the
  wake-word model scored 0.001, i.e. it simply doesn't hear itself.
- **Models that ship without an explicit turn-taking control module interrupt at natural
  pauses constantly.** Full-Duplex-Bench reports high takeover rates across all evaluated
  full-duplex models; the one with a dedicated speaking/listening-state module had a
  significantly lower takeover rate. Half-duplex (turn-by-turn) models sound less natural
  than humans precisely because humans listen and speak simultaneously.
- The speech-target problem (Quartet/Presenter, Horvitz/Paek/Ringger) is the always-listening
  version: is this utterance *for* me? Their result is instructive — humans hit 0.92 on
  detecting requests in a lecture, the system got 0.38, **but its false-alarm rate was
  0.007** (1 mistake in 135 audience-directed phrases). The sensitivity failure was the
  speech recognizer; the *policy* for when not to respond was already good. Don't over-invest
  in the addressee model; invest in the recognizer.

### Brevity — the biggest single text-side lever

CHI 2022 ("Keep it Short", 72 participants, 3 response styles):

- Current VAs "almost always utilize full sentences, even for commands where no verbal
  feedback would be necessary at all."
- Short keyword responses were perceived **equally useful, equally likeable, and sometimes
  more comprehensible** than full sentences — and rated **more efficient**.
- They need only **40–67% of the speech time**.
- For home-automation commands, preference for full sentences was as low as **21%**.
- Younger participants preferred shorter styles more often.
- Their recommendation: **brevity should adapt to the request** — match the response to
  whether the user asked for a number or for background.

YapBench (arXiv 2601.00624, 76 models) shows an **order-of-magnitude spread** in excess
length on brevity-ideal prompts, with two distinct failure modes: **vacuum-filling** on
ambiguous input, and **explanation/formatting overhead** on one-line technical requests.
Both are fixable with response shaping, not a better model.

### Conversational maxims (a usable spec)

Miehling et al. adapt Grice for human-AI: **quantity, quality, relevance, manner**, plus two
new ones — **benevolence** (avoid and engage harmful content) and **transparency**
(recognize one's own knowledge boundaries, operational constraints, and intents). The
transparency maxim is the one JARVIS honors: "sir, the arc reactor is at 47%" *is* a
transparent report of an operational constraint.

### Design for closure, not re-engagement

Albert, Sargent & Schoorens' conversation-analytic study of an actual smart-homecare agent
produced nine provocations from real transcripts. The ones that transfer:

1. **Leave the repair slot.** After initiating repair, stop and wait. Filling that slot is
   what makes an agent feel like it's talking at you.
2. **One response option at a time.** Multi-option turns push extra work onto the user.
3. **"Huh?" works fine.** Verbose explanations of what went wrong are worse than the simplest
   open-class repair. Trust the user to know how it works.
4. **Recognize moves to close.** When the user closes a sequence, the sequence is done.
5. **No joint proposals** you can't complete ("shall we do this together?").
6. **A single nudge is enough.** Persistent reminders are experienced not as support but as a
   violation of agency.
7. **No wordplay** that adds nothing.
8. **Recipient design.** "Generic 'friendly AI language' is built for a generic user, in a
   generic situation. There is no such thing."
9. **Design for closure, not for being "conversational."**

Their conclusion is the best one-line brief in this whole corpus: *the most conversationally
competent thing an LLM can do is to recognize that the conversation is over and shut up.*
The "one more thing" / "anything else?" pattern now has a name — **attention capture** — and
is classified as harmful.

### Silence as a designed tool

Context-aware pacing (CHI 2026, N=50) derived five mechanisms from human counseling
transcripts: **Reflective Silence, Facilitative Silence, Empathic Silence, Holding Space,
Immediate Response**. Against a static always-immediate baseline, it significantly improved
perceived human-likeness, smoothness and interactivity, and produced **deeper self-disclosure
and more engagement** (more emotional language, more first-person pronouns, more turns).
One participant: "I have time to carefully read line by line. While it's still outputting, I
can start typing my response, which makes the interaction feel more like a real dialogue."

### Latency hygiene worth stealing

- Use a **non-reasoning instruct build**. Hybrid-reasoning local models stream CoT into a
  separate field while the content field stays empty — the assistant appears to hang, then
  says nothing.
- **Pin the context window.** A default 64K allocation inflated the KV cache to 8.6 GB and
  spilled a third of the model onto CPU.
- **Keep models warm.** Cold-loading a model per utterance adds seconds.
- **STT and LLM compete for the same 8 GB.** Size them to both stay resident.
- On this class of hardware: a 7B on GPU is 6–8 GB and 1–3 s (and collides with STT); on CPU
  it's 5–15 s, which is unusable for voice. **The workable envelope is a 3–4B instruct model
  plus STT, both resident.** Tool calls cost ~0 on non-tool turns (schemas sit in the cached
  prompt prefix) and ~350–500 ms on tool turns, because the model runs twice: once to decide,
  once to report.

---

## 4. Interruption, attention, and presence

### Attention is the scarce resource

Horvitz's Attentional User Interface work treats attention as "a rare commodity — and
critical currency," and models it as a latent state with ~15 discrete values, fed by calendar,
device activity, ambient acoustics, and pose. The Notification Platform's design is the
template: for every candidate event, iterate every (device × modality) pair, compute
expected value of alerting now minus the attention-sensitive cost of disruption minus
fidelity loss, and pick the max. Two mechanisms survive from it and are worth naming
explicitly:

- **Bounded deferral** — commit to delivering a message before a message-specific deadline,
  then use local sensing to find the *best* moment inside that window. "Not now, not never."
- **Cost-sensitive escalation** — the same event gets a thumbnail in high-focus states and a
  full alert with an audio herald when attention is low and the value is high.

### Peripheral design rules you can implement literally

From Berkeley's glanceable-display work (2006):

- **Match user expectations** — the representation should look like what it means
  (red = hot is a strong match; square = hot is weak). Reduces learning effort.
- **Use abstraction** — extract the essential quality, exaggerate one recognizable feature.
- **Make visuals distinct** from each other and their background; "elements have to be
  distinguishable in an instant." Their biggest complaint with their own designs was subtle
  variation (slight transparency/blur/size changes) that defeated identification.
- **Maintain consistency** — changing placement, behavior or color for similar information
  causes disorientation and slows perception.

Calm technology (Weiser & Seely Brown 1996) yields three principles: **peripheral attention**
(live at the edge of awareness, move to foreground only when relevant), **enhanced periphery**
(a blink or posture is *more* trackable peripherally than a wall of log text, not less), and
**contextual familiarity** (continuity across past, present, future, not a snapshot).

### Ambient presence for agents — and the honesty trap

The strongest single warning in this area: **a status indicator that looks alive but doesn't
reflect ground truth is a dark pattern with a face.** The mechanism is identical to a fake
progress bar — the user reads an animation as a truthful signal of internal state, and the
interface exploits that inference. The specific failure: **a "thinking" animation that loops
regardless of whether the agent is reasoning, stalled, or silently errored is functionally the
same lie as a fake spinner, wearing a cuter costume.**

Concrete rules that follow:

- **Every animated state needs a machine-checkable backing condition.** If the backend can't
  distinguish "thinking" from "stuck," the display shouldn't be able to either. Prefer an
  honest "unknown" over a fabricated "still working on it!"
- **Time things honestly.** A real elapsed-time counter is more trustworthy *and* more
  engaging than a decorative loop. If it's been four minutes, it says four minutes.
- **Elapsed-time and failure states must be as legible as success states.** A presence display
  that only ever looks pleased is lying by omission.
- **The character never editorialize beyond what the agent actually said.** "Almost done!"
  when the agent said nothing is putting words in its mouth, and users will read it as
  information.
- **Escalation triggers must be genuinely rare and genuinely accurate**, or the signal decays
  into noise.
- **Don't add finer-grained "moods" that don't correspond to anything checkable.** Keep the
  state set small and map each state to one real condition.
- **Ship an honest "unknown" state.** This is the cheapest way to make the other states
  trustworthy.

### The Clippy lesson, stated properly

Office Assistant was built on real Nass/Reeves social-actor research and it still failed. The
diagnosis that generalizes: **Clippy's sin wasn't having a face — it was having a face that
demanded your attention on its own schedule.** An interruption-model assistant assumes it
knows better than you about when your focus should shift. An ambient-model assistant assumes
the opposite: it stays in your periphery until you look, and even then it only reports, never
nags. Every agent status widget built today is silently re-litigating this.

### Microsoft's agent UX principles (worth stealing verbatim)

- **Connecting, not collapsing** — agents help; they don't belittle the person.
- **Easily accessible yet largely invisible** — multimodal, foreground↔background
  transitions, proactive↔reactive transitions.
- **Agent time** — past / present (interacting proactively) / future (adapting).
- **Nudging more than notifying** — deliver on context, not on a schedule.
- **Transparency, control, consistency are foundational** — background agents need a
  user-facing mechanism to view and control their actions; **agent status must be clearly
  visible at all times**; use familiar UI elements; keep responses concise.

---

## 5. Why JARVIS demos feel magical — the synthesis

Ten mechanisms, in rough order of how much each contributes to the felt effect:

1. **It's already listening.** No wake-word tax, no push-to-talk ritual. It only "hears" what
   is addressed to it, which is a separate, more careful problem from hearing.
2. **It knows when to speak.** It shares what it noticed without being asked, and it stays
   quiet when quiet is right. This is the entire proactivity literature, above.
3. **Zero dead air.** The 200ms turn gap is filled by a voice, a gesture, or a *deliberate*
   silence — never by a spinner.
4. **It is interruptible, and interruption costs nothing.** You can cut in at any point and
   the system resynchronizes without a stale response.
5. **Terse, non-eager register.** Short declaratives. No hedging, no "anything else?", no
   repeated warnings, no wordplay.
6. **Working memory.** It references earlier turns and the physical environment by pointing
   at things.
7. **It acts on the world.** Tools, then a one-line report of the result.
8. **Visible presence that doesn't demand attention.** Peripheral, glanceable, honest.
9. **It never pretends.** When it doesn't know, it names the specific thing it doesn't know
   (the *transparency* maxim).
10. **It is selective.** Most of the time it says nothing.

### The honest caveat

The film JARVIS is fiction with an editor. What looks effortless on screen is a stack of
timed cues, pre-baked answers and cuts. The *reproducible* version of "magical" is much
smaller and much more achievable: **low latency, correct timing, terse grounded answers, real
tools, honest presence.** Every one of those five is an engineering target, not a personality
prompt.

---

## 6. Anti-JARVIS checklist

Directly usable as a design review against the existing orb/room/panels UI:

- [ ] Waits to be addressed — reactive only, never initiates
- [ ] Full-sentence responses to commands that a chime could cover
- [ ] Verbose hedging, boilerplate openers, restating the request
- [ ] "Anything else?" / "Is there something else on your mind?" after every turn
- [ ] Unprioritized notifications, random ordering, no reason given for speaking
- [ ] Interruptions landing mid-task rather than at a breakpoint
- [ ] Decorative animation not backed by a real signal (the fake-spinner problem)
- [ ] A spinner as the *only* latency signal
- [ ] Persona living only in the system prompt, absent from voice, labels, and error text
- [ ] Repetitive warnings and confirmations
- [ ] Asks permission for everything (Clippy) or for nothing (doesn't read the room)
- [ ] Verbose explanations of failures instead of a short repair
- [ ] Stale responses after barge-in
- [ ] No honest "unknown" / "stuck" / "failed" state on the orb
- [ ] Privacy leak: personal content announced when others are present
- [ ] Persona that implies a human on the other end

---

## 7. Sources

Primary / academic:
- Horvitz, *Principles of Mixed-Initiative User Interfaces*, CHI 1999 — the 12 principles;
  expected-utility action threshold; LookOut timing studies.
- Horvitz, Apperley, Subramonian, *Attentional User Interfaces*, CACM 2003 — attention as
  commodity, bounded deferral, Notification Platform, DeepListener/Quartet, speech-target
  problem.
- Paek, Horvitz, Ringger, *Continuous Listening for Unconstrained Spoken Dialog* — four
  levels of analysis (channel / signal / intention / conversation), Presenter results.
- Iqbal & Horvitz 2007 — 20–25 min recovery arc, 27% never resumed.
- *Email Duration, Batching and Self-interruption*, CHI 2016.
- Matthews et al., *Designing and Evaluating Glanceable Peripheral Displays*, DIS 2006 —
  match expectations, abstract, make distinct, maintain consistency.
- Weiser & Seely Brown, *The Coming Age of Calm Technology*, 1996.
- Zargham et al., *The Proactivity Dilemma*, CUI 2022 — three-level initiation model, agency,
  control.
- Bérubé et al., *Proactive behavior in voice assistants: systematic review and conceptual
  model*, CHB Reports 2024 (21 studies).
- Miksik et al., *Building Proactive Voice Assistants*, arXiv 2005.01322 — L1/L2, living-lab
  findings, animated pre-announcement.
- Haas et al., *Keep it Short*, CHI 2022 (72 participants).
- Mahmood et al., LLM-powered voice assistants design guidelines (IJHCS) — response-length
  guidelines, breakdown recovery rates, mental-model repair.
- Albert, Sargent & Schoorens — nine design provocations from real smart-homecare
  transcripts.
- Miehling et al., *Conversational Maxims for Human-AI Interactions*, arXiv 2403.15115.
- Maslych et al., *Mitigating Response Delays…*, CUI 2025 — natural vs artificial fillers, 4s
  cliff.
- Jiang et al., *Hear You in Silence: Context-Aware Pacing*, CHI 2026 (N=50).
- Lin et al., *Full-Duplex-Bench* — takeover rate, explicit turn-taking module.
- Mathur et al., *When Should AI Read the Room?*, arXiv 2605.29938 (N=200).
- Chen & Chen, *JarvisBench* / *Just A Rather Very Intelligent Spoken Agent*,
  arXiv 2607.16610, 2608.14870 — always-on mediator, checkpoint triggers.
- Borisov et al., *YapBench*, arXiv 2601.00624 (76 models).
- An, *When Should the Agent Speak?* survey, TMLR 2026 — the cost term; PRISM, EgoSocial,
  one-sided-reward collapse.
- IHBench (Boson AI) — six interruption types, recovery vs barge-in as separate axes.
- Google conversation-design persona guidance; Microsoft Design agent UX principles.
- Nielsen Norman Group on intelligent-assistant usability and attitudes (the gulf between
  promised and delivered usability; users' mental models are command-shaped, not
  conversation-shaped).

Secondary / practitioner (treat as directional, not evidence):
- Zylos Research on ambient agent presence, notification triage, and sonification.
- Picovoice voice-UX chapter (latency budget, endpointing, barge-in, acknowledgments).
- Big Iron self-hosted voice pipeline guide; Brainwagon local-Alexa build report.
- Waterloo "Genie in the Bottle" anthropomorphization study.
