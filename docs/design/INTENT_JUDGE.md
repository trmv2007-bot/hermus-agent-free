# INTENT_JUDGE

Design record for `core/intent.py`: the component that decides whether HERMUS
speaks, when, at what force, and with what reason.

This document records the research, the rules derived from it, the decision
table, and explicitly what the module does **not** do. Every URL below was
retrieved while building this; nothing here is cited from memory.

---

## 1. The problem

An assistant that speaks whenever something happens is worse than no assistant.
It converts a channel into wallpaper, and wallpaper is something you learn to
ignore. The first question is therefore not "what should it say", it is "should
it say anything at all", and the honest answer is usually no.

Three things make this hard to get right in code:

1. **Silence is indistinguishable from absence.** A system that is quiet
   because it is choosing to be, and one that is quiet because it crashed, look
   identical from outside. The fix is not more output, it is *observable
   reasons*.
2. **Urgency is not a property of the event.** An error is urgent in a room
   where work is stopped and noise in a room where the user is mid sentence.
   Urgency is a function of event, room, floor, and recency.
3. **Honesty is asymmetric.** Claiming a success that did not happen is
   categorically worse than staying quiet about one that did.

---

## 2. Research findings

### 2.1 Mixed-initiative UI: timing is a first-class problem

**Horvitz, "Principles of Mixed-Initiative User Interfaces" (CHI 1999)**
<http://erichorvitz.com/chi99horvitz.pdf> (also
<https://dl.acm.org/doi/10.1145/302979.303030>)

The four key problems named in the abstract are "poor guessing about the goals
and needs of users, inadequate consideration of the costs and benefits of
automated action, **poor timing of action**, and inadequate attention to
opportunities that allow a user to guide the invocation of automated services".

Principles that map directly onto this module:

- **(3) Considering the status of a user's attention in the timing of
  services.** "The nature and timing of automated services and alerts can be a
  critical factor in the costs and benefits of actions. Agents should employ
  models of the attention of users and consider the costs and benefits of
  deferring action to a time when action will be less distracting."
- **(7) Minimizing the cost of poor guesses about action and timing**,
  "including appropriate timing out and natural gestures for rejecting attempts
  at service".
- **(8) Scoping precision of service to match uncertainty**, with a stated
  preference for "doing less" but doing it correctly under uncertainty.

The most directly load-bearing detail is in the LookOut failure-handling
section: when the user declines service, "the system will pose a question, wait
patiently for a response, and then make a respectful, apologetic gesture and
evaporate", and "**the system increases its dwell on the desktop if it detects
signs that the user is thinking, including "hmmm…", "uh…"**". The design goal
is stated plainly: an assistant should be "an intuitive, courteous butler, who
might make potentially valuable suggestions from time to time, but who is
careful to note when the user is simply too busy to even respond, and to get out
of the user's way with minimal disturbance."

> This is the citation behind the mid-sentence rule. It is not an inference.
> A system that talks over "hmmm" is worse than one that never speaks, because
> the user cannot dismiss what they did not know was coming.

### 2.2 The guidelines that require a reason string

**Amershi et al., "Guidelines for Human-AI Interaction" (CHI 2019)**
<https://www.microsoft.com/en-us/research/wp-content/uploads/2019/01/Guidelines-for-Human-AI-Interaction-camera-ready.pdf>
(summarised with the full 18 at
<https://www.microsoft.com/en-us/research/blog/guidelines-for-human-ai-interaction-design/>)

Four of the eighteen bear on this module:

- **03 Time services based on context.** "Time when to act or interrupt based
  on the user's current task and environment."
- **08 Support efficient dismissal.** "Make it easy to dismiss or ignore
  undesired AI system services." An interrupt that cannot be dismissed cheaply
  is a violation, not a feature.
- **10 Scope services when in doubt.** "Engage in disambiguation or gracefully
  degrade the AI system's services when uncertain about a user's goals."
- **11 Make clear why the system did what it did.** "Enable the user to access
  an explanation of why the system behaved as it did."

Guideline 11 is why `Decision.reason` is a required, specific, quantified
string and not a debug log. Guideline 08 is why `Urgency.INTERRUPT` is
rationed to two causes instead of being a severity level.

Note the framing: these were developed and validated on graphical interfaces,
and the authors explicitly note "there are opportunities to develop specific
extensions or modifications of the guidelines for voice interaction". That
extension is exactly what this module is, and it is not free rein: the voice
case inherits the same principles with a tighter cost for being wrong, because
an interrupt in a voice room cannot be dismissed by not looking at it.

### 2.3 Alert fatigue is a cost the system pays to itself

**"When not to help: planning for lasting human-AI collaboration" (arXiv 2508.01837)**
<https://arxiv.org/abs/2508.01837>

Models when to speak as a POMDP over the user's latent engagement state. The
mechanism is worth stating because it explains *why* redundancy is expensive:
"if the AI advice is redundant, attention decreases, reflecting diminished
perceived usefulness", whereas "if AI advice corrects a potential error the
human could have made, attention increases".

Simulated results: the adaptive policy reaches 91% correct decisions against
85% for always-on and 77% for always-off. The always-on policy "leads to
decreases in engagement by offering frequent and sometimes redundant
assistance". Their conclusion: "**strategic silence can be just as valuable as
offering guidance**."

> This is the quantitative case for the whole module. Always-speak and
> never-speak are both measurably worse than adaptive timing.

**Vance et al., "The Fog of Warnings" (SOUPS 2019)**
<https://www.usenix.org/system/files/soups2019-vance.pdf>

The finding that most directly shapes the design: habituation **generalises
across look and feel**. "Habituation to a frequent non-security-related
notification does carry over to a one-time security warning", and "the degree
that generalization occurs depends on the similarity in look and feel between a
notification and warning."

> This is the strongest available argument against "make the important one
> louder". A visually distinctive interrupt channel is exactly the thing that
> habituates. The correct response is to *not have more than a couple of
> interrupts per session*, not to style them more aggressively.

**Clinical decision support stewardship** (Chaparro et al., 2022)
<https://pmc.ncbi.nlm.nih.gov/articles/PMC9132737/>

"interruptive alerts should be used only when other less intrusive options have
been thoroughly considered", with a named "immediate cost of increasing
cognitive burden" and a "longer-term cost of alert fatigue and decreased
provider receptiveness".

Reported override rates in the literature run high: 49 to 96% of alerts
overridden <https://pmc.ncbi.nlm.nih.gov/articles/PMC5387195>, and clinicians
override up to 90% of drug interaction alerts
<https://pmc.ncbi.nlm.nih.gov/articles/PMC7976224>. Habituation to repeated
alerts is documented as habitual dismissal rather than reasoned rejection
<https://pmc.ncbi.nlm.nih.gov/articles/PMC7651895>.

**Busse et al., habituation to security dialogs**
<https://www.usenix.org/sites/default/files/soups2018posters-busse.pdf>

"frequent exposure to these dialogs may lead to habituation (i.e. users tend to
ignore them)". This is the "are you sure?" problem, and the design conclusion
is the same one reached here: the confirmation prompt is not a safety feature
if it appears often enough that it stops being read. A judge that asks for
confirmation on routine events has converted a safety mechanism into wallpaper.

### 2.4 Friction is not breakdown

**Guan, "Friction or Breakdown? Rethinking the Timing of GenAI Intervention in Knowledge Work"**
<https://ceur-ws.org/Vol-4265/paper6.pdf>

"pauses, reformulations, low output volume, and rapid acceptance, are ambiguous
unless interpreted in relation to task stage". A user may be pausing to
integrate evidence, not because they are stuck. Intervention timing must
account "for both immediate interaction effects and downstream cognitive
consequences". The framing is "not only when to help, but **when not to help**".

> This is the argument against inferring urgency from hesitation signals. This
> module takes room state and event kind as explicit inputs from the caller
> rather than guessing from typing speed, pause length, or keystroke rate.
> Those signals are genuinely ambiguous and the caller is in a far better
> position to resolve them.

### 2.5 Latency and interruptibility

**Stivers et al. (2009), PNAS**, as summarised in
<https://sciencedirect.com/science/article/abs/pii/S0022096519305909> and
<https://picovoice.ai/guide/voice-agents/voice-ux-latency-turn-taking/>:
across 10 language groups, modal pause before responding lands within 200 ms.
"A response that arrives in two seconds is not perceived as 'a fast API call';
it is perceived as an awkward silence."

**ACIxD barge-in guidance** <http://acixd.org/wiki/doku.php?id=global_barge-in_strategy>:
system speech should stop within 500 ms of the user beginning to barge in.
Continuing past 300 to 500 ms triggers the "stuttering effect", where the
caller assumes the system did not hear them, stops, and restarts.

**Amazon natural turn-taking** <https://www.amazon.science/blog/change-to-alexa-wake-word-process-adds-natural-turn-taking>:
the feature infers "when users have finished speaking, when their speech is
directed at the device, when it's not, and whether or not a reply is expected",
and handles barge-in including *contextual* barge-in where "that one" refers to
the option being read aloud at the moment of interruption.

> Barge-in support is the *reply* path and is out of scope here. What matters
> for this module is the inverse: the judge is the component that decides
> whether to *start* speaking, and a 500 ms budget for ending is worthless if
> the decision to begin was wrong.

### 2.6 Uncertain output needs a stated, not implied, uncertainty

**Wortman Vaughan et al., "I'm Not Sure, But..." (FAccT 2024)**
<https://arxiv.org/pdf/2405.00623v1> / <https://dl.acm.org/doi/10.1145/3630106.3658941>

**Zhang et al., "Appropriate Reliance" (CHI 2025)**
<https://arxiv.org/pdf/2412.15584>

LLMs "present their outputs with impressive detail and confidence" while being
prone to error, and interventions that reduce over-reliance "are generally not
effective at improving appropriate reliance", instead "reduc[ing] reliance
overall at the expense of useful reliance".

> The bearing on this module is narrow and worth stating so it is not
> overstated: hedged *language* is not a reliable fix for an assistant that
> has nothing to report. The stronger guarantee is structural. An event with no
> verified result is not softened, it is withheld, and the reason says so. The
> `verified` / `unverified` field on every decision is the audit trail.

### 2.7 The JARVIS claims

The brief asked to confirm or refute two claims. Both are **directionally
right and insufficient as stated**, and the sources say something sharper than
either.

**Claim A: "the JARVIS feel comes from state-driven motion and sound, not from
the visual theme."**

Supported, and the evidence is from the people who built it.

- VFX supervisor John Nelson, on the original 2008 HUD: "The key question was,
  how do you get inside the helmet but still have this virtual environment that
  feels **organic**? We were trying to get an **organic conversation** going
  between his A.I. assistant J.A.R.V.I.S. and Tony Stark. But no one really knew
  what that meant."
  <https://vfxblog.com/ironman/>
- Jonathan Rothbart, same project, on the design: "fighter jets have HUDs, but
  they're pretty much static with all the information out there in front of you
  at all times, in a fixed place. **We wanted it to be moving**, but we also
  didn't want to make cluttered. Which is why we kind of came up with this
  concept of **things flying in and flying out**, so that we didn't overload all
  this data in front of his face **while he was talking and acting**."
- Same article: the Mark II HUD was rejected in part because it showed "an
  immense amount of data", and the fix was a second version "much more simple,
  pared down, elegant and minimal".
- UI designer Jayse Hansen: the Mark VII diagnostic "showed him an immense
  amount of data on his suit at a glance, but yet you can't read each individual
  thing. **And you're not meant to**, he's looking at the patterns of it... he
  can read the patterns at a glance, rather than just raw information."
  <https://www.pushing-pixels.org/2012/06/01/the-craft-of-screen-graphics-and-movie-user-interfaces-conversation-with-jayse-hansen.html>

So: the load-bearing element was state legibility, arrived at by motion that
reflects state, and the *removal* of content rather than its styling. The
"not meant to" line is the clearest statement of the principle in any of these
sources: the display is a signal, not a readout. The visual theme is downstream
of that decision.

**Correction to the claim:** "sound" is doing more work in the original than
the motion half, and the source for it is not the visual design at all. It is
the turn-taking behaviour, which is a separate, later, and much better
documented body of work (section 2.5). A system that moves beautifully and
answers two seconds late is not JARVIS. The claim is right that theme is not
the mechanism, and wrong to treat motion and sound as a single thing.

**Claim B: "when it speaks > latency > interruptibility > what it says"**

Half right, and the ordering is wrong in a way that matters.

- **"when it speaks" first: confirmed, and it is the strongest finding here.**
  Always-on and always-off both lose to adaptive timing (arXiv 2508.01837),
  and the generalisation effect in Vance et al. means the cost of speaking too
  often is not linear, it degrades every future utterance including the urgent
  ones.
- **"latency second": supported, and it is a property of the pipeline rather
  than of the decision.** The 200 ms modal gap is real and cross-linguistic.
  But latency is not a knob this module turns; a decision function that took
  200 ms would itself be a latency bug. Latency ranks below *when it speaks*
  only because *when it speaks* is a prerequisite for latency mattering at all.
- **"interruptibility third": correct, and for a specific reason.** Barge-in
  and contextual barge-in are mature and well specified, and the 500 ms stop
  budget is settled. They rank below latency because a system that cannot be
  interrupted is a *dangerous* system, but a system that is easy to interrupt
  and slow to start is merely irritating. Difficulty of reversal ranks
  above difficulty of onset.
- **"what it says" last: this is the part to correct.** What it says is
  not a fourth-order concern that follows the other three; it is the *output*
  of the other three, and it is load-bearing in two specific ways. First,
  Amershi guideline 11: a decision without a legible reason is a system the
  user cannot correct, because they cannot tell you what you got wrong. Second,
  the reason string is the observability surface: without it, "why is it quiet"
  and "why did it interrupt" are both unanswerable in production, and the
  system can only be tuned by guesswork.

**Refined ordering actually used to prioritise work:**

1. **When it speaks** (this module, entirely)
2. **Whether what it says can be trusted at all** (this module, the
   `cannot_vouch` rule) - promoted above latency, because an assistant that is
   confidently wrong is worse than one that is slow and correct
3. **Latency** (pipeline, not this module)
4. **Interruptibility** (reply path, not this module)
5. **Wording quality** (generation, not this module; but the *reason* string
   is wording, and it is a hard requirement here)

---

## 3. The decision table

Precedence is top to bottom. The first matching rule wins.

| # | Rule | Condition | Urgency | Speaks |
|---|------|-----------|---------|--------|
| 1 | `cannot_vouch` | `TOOL_FINISHED` or `MISSION_COMPLETE`, `event_verified=False` | SILENT | no |
| 2 | `nothing_to_say` | `event is NONE` | SILENT | no |
| 3 | `user_has_floor` | `user_just_spoke`, event is ERROR | ACKNOWLEDGE | yes, folded into reply |
| 3 | `user_has_floor` | `user_just_spoke`, event is promotable | SILENT | no, deferred |
| 4 | `urgent` | ERROR, first occurrence in window | INTERRUPT | yes |
| 4 | `repeat` | ERROR, occurrence 2 or 4+ in window | SILENT | no |
| 4 | `urgent` | ERROR, occurrence 3 in window | INFORM | yes, escalates |
| 5 | `blocked` | `room is BLOCKED`, young block | INTERRUPT | yes |
| 5 | `block_age` | `room is BLOCKED`, block older than 60s | INFORM | yes |
| 6 | `cooldown` | same signature announced inside routine cooldown | SILENT | no |
| 7 | `rate` | gap under the 20s minimum | SILENT | no |
| 8 | `internal` | `room is COMPACTING` | SILENT | no |
| 9 | `quiet_window` | promotable, verified, gap over 180s | INFORM | yes |
| 10 | `unremarkable` | anything else | SILENT | no |

Two ceilings are applied after the table, in `_deliver`:

- **Room ceiling.** `ATTENTION` and `COMPACTING` cap at INFORM. The user
  already has the floor, so an interrupt is wasteful rather than urgent.
- **Floor protection.** `user_mid_sentence` or `user_typing` caps at INFORM
  and sets `defers=True`. The single exception is an interrupt, which is
  **dropped** rather than queued: a stale interrupt delivered after the
  sentence ends is worse than not delivering it.

Escalation ladder, keyed on occurrences *including* the current one:

| Occurrence | Verdict | Reason |
|-----------|---------|--------|
| 1 | INTERRUPT | first sighting, silence would read as not noticing |
| 2 | SILENT | already reported 30s ago, same fault not new news |
| 3 | INFORM | a persistent fault must not look resolved |
| 4+ | SILENT | the escalation already happened, stop |

---

## 4. Rules, and why each exists

| Rule | Why it exists |
|------|---------------|
| Silence is a first class outcome | Always-on loses to adaptive timing (arXiv 2508.01837). A judge that always speaks is worse than none. |
| Never interrupt mid-sentence | Horvitz's LookOut extended its dwell on "hmmm" precisely to avoid talking over thinking. |
| Interrupt only on error or block | Silence there reads as not noticing, and the cost is asymmetric. Vance et al. make restraint the price of the interrupt working at all. |
| Ration interrupts by an explicit table | Habituation generalises across look and feel, so the count matters more than the styling. |
| Occurrences are counted separately from announcements | Keying escalation off announcements is dead code: the second occurrence is suppressed, so the count never advances. Found by test, not by reading. |
| Cooldown beats the quiet window | A long silence does not make the same event interesting twice. |
| Nothing is unverified | Asserting a result nobody checked is the failure that costs trust permanently. |
| Every decision carries a specific reason | Amershi guideline 11. Also the only way to tune this in production. |
| `defers` is dropped, not queued, for interrupts | A stale interrupt is worse than silence. |
| No model, no network, no clock | The judgement must be replayable. |

---

## 5. What this module does NOT do

Stated plainly, because the boundaries are the design:

- **It does not call a model.** No LLM, no classifier, no embedding lookup. The
  decision is deterministic given its inputs. An assist that wants a generated
  line calls a generator *after* the judge says speak, never inside the judge.
- **It does not decide tool calls, plans, or actions.** It has no ability to
  run anything. It answers "should I talk", never "what should I do".
- **It is not an agent.** No goals, no loop, no memory beyond a bounded list of
  recent marks. It is a pure function plus a small state holder.
- **It does not read a clock.** `Moment.now` is supplied by the caller. This is
  what lets a test place two events three minutes apart without sleeping.
- **It does no IO, hold no handles, and start no threads.**
- **It does not do speech recognition or barge-in.** Mid-sentence detection is
  an input (`user_mid_sentence`), not something it measures. The barge-in path
  is the reply side and is a separate concern.
- **It does not infer user state from behavioural signals.** No keystroke-rate
  or pause-length heuristics, per the friction-versus-breakdown argument.
- **It does not render or theme anything.** Per the research in section 2.7,
  a visually distinctive interrupt channel is the thing that habituates.
- **It does not deduplicate against events it never saw.** It is called; it does
  not subscribe.
- **It is not the source of truth for whether a result is verified.** It
  consumes `event_verified` from whatever produced the event and refuses to
  announce anything it cannot vouch for.

---

## 6. Known limitations

Stated so the next person does not rediscover them badly.

- **`prior_occurrences` defaults to 0 on the raw `decide()` path.** Callers
  using the free function must supply it for error escalation to work. The
  `IntentJudge` wrapper fills it in automatically. This is a real footgun and
  the reason `decide` is not the recommended entry point.
- **Thresholds are guesses.** Every number in `JudgeConfig` is a judgement
  call, not a fitted parameter. They are all in one dataclass so they can be
  swept against real transcripts.
- **No per-user adaptation.** Vance et al. and arXiv 2508.01837 both describe
  engagement that varies per user. This module has one global config.
- **The escalation ladder is a table, not a curve.** Three rungs is
  hand-tuned. A real deployment wants something informed by actual override
  rates.
- **`_QUIET_PROMOTABLE` excludes `MEMORY_WRITTEN` entirely.** A judgement call:
  a memory write is internal bookkeeping, but a sufficiently long block of
  silence might make it worth surfacing. Left out deliberately.
