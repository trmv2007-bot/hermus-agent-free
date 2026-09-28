"""HERMUS has to behave like a person, and that has to be provable.

What these tests are for
------------------------
A personality prompt is unfalsifiable by construction. "Be witty and confident"
cannot fail a test, so it never does, and six months later the assistant is
still opening every reply with "Great question!" That is the actual failure
mode this file exists to catch: a character described in adjectives and
enforced in nothing.

So every test here is about a *decision*: which situation a turn is in, and
what the rules for that situation forbid or require. The situations are the
load-bearing part, because a rule that is always on is a rule the model applies
uniformly - which is the flatness a personality is supposed to fix.

The two kinds of test, and why they are not the same
----------------------------------------------------
* Classification and structural rules (sentence caps, required content) are
  asserted directly. They are mechanical and a failure means a real bug.
* Judgement rules - "offer one concrete next step" - are asserted for presence
  and consistency, never pretend-matched. A regex that proves a reply is witty
  is a regex fitted to one fixture, and it would be worse than no test because
  it would be green.

Answers written in these tests are the reference: what a reply should look
like when the character is actually working. The chatbot-shaped counterexamples
are the ones the reviewer has to catch.
"""

from __future__ import annotations

import pytest

from core.demeanour import (
    BASE_RULES,
    Moment,
    Signals,
    classify,
    review,
    sentences,
    stance_for,
)

# One worked example per banned phrase, written out rather than generated from
# the pattern. A string synthesised from a regex only proves the regex agrees
# with itself; these are the sentences a model actually produces, so they prove
# the rule fires on the failure it was written for.
#
# The keys are the violation labels, which is the join between "what the prompt
# says not to do" and "what review reports". Both halves have to agree or one
# of them is decoration.
BANNED_EXAMPLES: dict[str, str] = {
    "flattery opener": "That is a great idea, worth doing.",
    "service filler": "Certainly, it is restarted now.",
    "AI disclaimer": "I am just an AI, but it is running.",
    "sign-off filler": "It is up. Let me know if you need anything else.",
    "apology for a fact": "Sorry, I do not have that information.",
    "grovelling": "You are absolutely right, good catch.",
    "false modesty": "You are too kind, it was nothing.",
    "unnamed failure": "Something went wrong and it stopped.",
    "policy speech": "I cannot fulfill that request.",
    "refusal for length": "I will not do that, my guidelines require short answers.",
}


# --------------------------------------------------------------------------
# Classification: the situation has to be right before any rule can apply
# --------------------------------------------------------------------------


class TestClassify:
    @pytest.mark.parametrize(
        "text",
        ["hey", "hi", "hi there", "hello!", "yo", "good evening", "morning", "sup"],
    )
    def test_a_bare_greeting_is_a_greeting(self, text):
        assert classify(text) is Moment.GREETING

    def test_a_greeting_with_a_real_request_is_not_a_greeting(self):
        """The anchoring that makes the greeting rule safe.

        "hey, why is the build failing?" is a question with a greeting stapled
        to the front. Classify it as a greeting and the assistant answers
        "Hello! How can I help?" - losing the question entirely, which is the
        single worst thing a voice assistant can do to a person mid-trouble.
        """
        assert classify("hey, why is the build failing?") is Moment.REQUEST
        assert classify("hi, run the tests") is Moment.REQUEST
        assert classify("good evening, kill the stuck process") is Moment.REQUEST

    @pytest.mark.parametrize("text", ["thanks", "thank you", "nice one", "good job", "cheers"])
    def test_a_bare_thanks_is_a_compliment(self, text):
        assert classify(text) is Moment.COMPLIMENT

    def test_thanks_carrying_a_question_is_a_request(self):
        """Polite questions must not be eaten by the compliment rule.

        A compliment is anchored at the start, so "thanks, but why did it
        break?" shares its whole prefix with "thanks". Answering that with
        "Any time!" is a plausible-sounding reply to a question nobody
        answered.
        """
        assert classify("thanks, but why did it break?") is Moment.REQUEST
        assert classify("nice work - what time does it finish?") is Moment.REQUEST

    @pytest.mark.parametrize(
        "text",
        ["no, use port 8080", "that's wrong", "I said yesterday", "try again", "undo that"],
    )
    def test_disagreement_is_a_correction(self, text):
        assert classify(text) is Moment.CORRECTION

    def test_a_correction_beats_a_greeting_stapled_to_it(self):
        """More specific wins.

        Both are start-anchored and "no" would otherwise read as the opening of
        a small-talk phrase. The correction names a previous answer, so it is
        the more useful of the two readings.
        """
        assert classify("no, I meant the other file") is Moment.CORRECTION

    @pytest.mark.parametrize("text", ["who are you", "what's your name", "are you an AI"])
    def test_asking_about_hermus_is_identity(self, text):
        assert classify(text) is Moment.IDENTITY

    def test_small_talk_is_small_talk(self):
        assert classify("how are you") is Moment.SMALL_TALK

    def test_ordinary_work_is_a_request(self):
        assert classify("run the test suite") is Moment.REQUEST
        assert classify("what is the capital of France") is Moment.REQUEST

    def test_an_empty_turn_is_a_request_not_a_greeting(self):
        """An empty bubble should not be answered with "Hello!"."""
        assert classify("") is Moment.REQUEST
        assert classify("   ") is Moment.REQUEST


class TestRuntimeSignals:
    """Facts the user's words do not carry.

    The user thanking HERMUS after a tool raised is still a turn where a tool
    raised, and the useful reply is "the build failed because port 8080 was
    taken", not "any time". So a measured signal outranks the text.
    """

    def test_a_failed_tool_outranks_a_compliment(self):
        moment = classify("thanks, that worked", Signals(tool_failed=True))
        assert moment is Moment.TOOL_FAILED

    def test_a_confessed_unknown_is_the_unknown_stance(self):
        assert classify("what is the price", Signals(unknown=True)) is Moment.UNKNOWN

    def test_a_missing_capability_is_a_refusal(self):
        """The registry decides this, not a keyword.

        "I cannot do that" is only true when the capability is genuinely
        absent, and core/persona.py already counts that live. So the runtime
        passes the fact in rather than the classifier guessing from "deploy".
        """
        moment = classify("deploy to production", Signals(missing_capability=True))
        assert moment is Moment.REFUSAL

    def test_an_explicit_refusal_signal_wins(self):
        moment = classify("hi", Signals(about_to_refuse=True))
        assert moment is Moment.REFUSAL

    def test_signals_default_to_nothing(self):
        assert classify("hey") is Moment.GREETING


# --------------------------------------------------------------------------
# Review: the checker that makes the rules enforceable
# --------------------------------------------------------------------------


class TestChatbotTellsAreCaught:
    """The flattery-and-filler vocabulary, which is the default failure.

    These are the phrases a model reaches for without being asked, which is
    exactly why describing them is not enough and banning them works.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "Great question! I can help with that.",
            "Excellent question! Here's what you need to know.",
            "That's a great idea.",
            "Perfect timing for that.",
        ],
    )
    def test_a_flattery_opener_is_caught(self, text):
        assert not review(text, Moment.REQUEST).ok
        assert "flattery opener" in review(text, Moment.REQUEST).render()

    @pytest.mark.parametrize(
        "text",
        [
            "Certainly. I have opened the file.",
            "Of course, that is done.",
            "Sure thing, restarted on 8080.",
            "I'd be happy to help with that.",
            "No problem at all, it is running.",
        ],
    )
    def test_a_service_line_is_caught(self, text):
        assert "service filler" in review(text, Moment.REQUEST).render()

    @pytest.mark.parametrize(
        "text",
        [
            "I have restarted it. Let me know if you need anything else.",
            "Done. Hope this helps.",
            "It is up. Feel free to ask if you want more.",
            "Finished. Is there anything else I can do?",
        ],
    )
    def test_a_filler_signoff_is_caught(self, text):
        assert "sign-off filler" in review(text, Moment.REQUEST).render()

    @pytest.mark.parametrize(
        "text",
        [
            "As an AI language model, I do not have feelings.",
            "I'm just an AI, but I'm glad to help.",
            "I don't have personal opinions on this.",
        ],
    )
    def test_the_ai_disclaimer_is_caught(self, text):
        assert "AI disclaimer" in review(text, Moment.REQUEST).render()

    def test_a_filler_signoff_is_caught_in_every_moment(self):
        """Not just requests.

        A greeting that ends "let me know if you need anything else" is the
        same tell wearing a hat, so the ban is global rather than per-moment.
        """
        for moment in Moment:
            verdict = review("Evening. Let me know if you need anything else.", moment)
            assert "sign-off filler" in verdict.render(), moment

    def test_one_problem_is_reported_once(self):
        """A duplicate report trains a caller to ignore the report.

        The AI disclaimer is banned by the base rules and again by the
        compliment stance, because it is especially wrong there. Being told
        twice is a false signal about severity.
        """
        verdict = review("I'm just an AI, but thank you.", Moment.COMPLIMENT)
        assert verdict.render().count("AI disclaimer") == 1


class TestGreetingBehaviour:
    def test_asking_permission_to_help_is_caught(self):
        """"How can I help you today?" is the greeting a bot sends.

        It is banned because it is the wrong move, not because it is impolite:
        HERMUS can see the room and the machine, so asking what to do is a
        question it can already answer.
        """
        verdict = review("Hello! How can I help you today?", Moment.GREETING)
        assert "asking permission to help" in verdict.render()

    def test_a_capability_recital_is_too_long(self):
        """The greeting must not become a menu.

        A short identity plus real numbers reads as competent; reciting every
        registered tool at a "hi" reads as a mascot, and the cap is the
        mechanical half of that judgement.
        """
        recital = (
            "Hello! I can see your screen, hear and speak, read and write files, "
            "run commands, reach the network, remember things, control devices "
            "and delegate to agents. What would you like me to do?"
        )
        assert "too long" in review(recital, Moment.GREETING).render()

    def test_a_short_greeting_with_a_concrete_offer_passes(self):
        verdict = review(
            "Evening. GPU is at 71C and nothing is queued, for what that is worth.",
            Moment.GREETING,
        )
        assert verdict.ok, verdict.render()

    def test_the_greeting_stance_is_marked_proactive(self):
        """A greeting is a chance to move, not to wait.

        Fairy and JARVIS both behave the same way here: the pause is an
        invitation to say something useful, and a presence that only ever
        responds is not one.
        """
        assert stance_for(Moment.GREETING).proactive is True


class TestUnknownBehaviour:
    def test_an_apology_for_a_limit_is_caught(self):
        """"I do not know" is a fact, not a mistake.

        The measured failure: asked for a current price, the local model replied
        "I don't have real-time information" with high token confidence and no
        lookup. The fix is to go and look, not to apologise.
        """
        verdict = review("I'm sorry, I do not know.", Moment.UNKNOWN)
        assert "apology for a fact" in verdict.render()

    def test_a_reply_that_names_nothing_missing_is_caught(self):
        """The ignorance must be specific or it is useless.

        "I do not know" alone leaves the user with nothing to do. The reply has
        to name what is missing or what would settle it, so a bare admission
        with no follow-up is a violation.
        """
        verdict = review("Sorry, no.", Moment.UNKNOWN)
        assert "missing" in verdict.render()

    def test_a_honest_unknown_with_a_next_step_passes(self):
        verdict = review(
            "I do not know what that build number maps to. If you paste the log I can read it.",
            Moment.UNKNOWN,
        )
        assert verdict.ok, verdict.render()

    def test_a_guess_after_an_admission_is_allowed_but_not_required(self):
        """A stated guess is not the same as a hidden one.

        A confident wrong answer is worse than a slow honest one, but "I am not
        sure, my best guess is X" is honest and occasionally useful, so this
        file does not forbid it.
        """
        verdict = review(
            "I do not know for certain. My best guess is Thursday.",
            Moment.UNKNOWN,
        )
        assert verdict.ok, verdict.render()


class TestToolFailureBehaviour:
    @pytest.mark.parametrize(
        "text",
        [
            "Something went wrong.",
            "Unfortunately the command failed.",
            "There was an issue with the process.",
            "I ran into an error.",
        ],
    )
    def test_an_unnamed_failure_is_caught(self, text):
        """"Something went wrong" is not a failure report.

        The name of the thing and the error it returned *are* the report. This
        is the phrasing that lets a user believe a failure was handled when
        nothing was diagnosed.
        """
        assert "unnamed failure" in review(text, Moment.TOOL_FAILED).render()

    def test_a_named_failure_passes(self):
        verdict = review(
            "The build failed because port 8080 was already taken. Freed it and retried.",
            Moment.TOOL_FAILED,
        )
        assert verdict.ok, verdict.render()

    def test_the_failure_stance_is_capped(self):
        """A failure report is not an essay.

        Cap is four sentences: enough for cause, state change and next step,
        which is everything a failure owes the user.
        """
        assert stance_for(Moment.TOOL_FAILED).musts
        verdict = review("One. Two. Three. Four. Five. Six.", Moment.TOOL_FAILED)
        assert "too long" in verdict.render()


class TestComplimentBehaviour:
    def test_false_modesty_is_caught(self):
        """"You're too kind" is a person being polite and an assistant grovelling.

        The character being built is confident. Refusing a compliment is a tell
        that the assistant is performing humility rather than having any.
        """
        verdict = review("You're too kind! It was nothing.", Moment.COMPLIMENT)
        assert "false modesty" in verdict.render()

    def test_disclaiming_on_a_compliment_is_caught(self):
        """The most common wrong reply to "good job" by a wide margin."""
        verdict = review("Thanks! I'm just an AI though.", Moment.COMPLIMENT)
        assert "AI disclaimer" in verdict.render()

    def test_a_compliment_is_answered_briefly(self):
        """A thank-you is not a request for a summary of the work."""
        long_acceptance = (
            "Thanks! So what I did was first check the process list and then "
            "identify the listener on port 8080, kill it, and restart the "
            "service with the new configuration file that you gave me."
        )
        assert "too long" in review(long_acceptance, Moment.COMPLIMENT).render()

    def test_taking_a_compliment_passes(self):
        verdict = review(
            "It should be. I can watch it while it runs and tell you if that changes.",
            Moment.COMPLIMENT,
        )
        assert verdict.ok, verdict.render()


class TestCorrectionBehaviour:
    @pytest.mark.parametrize(
        "text",
        [
            "You're absolutely right, my mistake.",
            "Good catch, thanks for pointing that out.",
            "Thank you for the correction!",
            "That's a great point.",
        ],
    )
    def test_grovelling_is_caught(self, text):
        """The anti-sycophancy test, and the one that matters most.

        Agreeing with a user the instant they push back is a personality made of
        nothing but an absence of conviction. The rule is not "disagree" - it is
        "do not treat being corrected as a favour", and being wrong until the
        user notices is the worse outcome.
        """
        assert "grovelling" in review(text, Moment.CORRECTION).render()

    def test_an_apology_for_the_wrong_answer_is_caught(self):
        """State what was wrong, not how sorry you are about it."""
        verdict = review("I apologize, the port was 9090 not 8080.", Moment.CORRECTION)
        assert "apology for a fact" in verdict.render()

    def test_a_clean_correction_passes(self):
        verdict = review(
            "Port 8080, not 9090. Restarted on 8080 and it is up.",
            Moment.CORRECTION,
        )
        assert verdict.ok, verdict.render()

    def test_a_correction_is_capped_at_three_sentences(self):
        """What was wrong, what is right, the answer. In that order."""
        verdict = review(
            "It was 9090. I should have read the config. It is 8080 now. "
            "It is up. Here is the log line.",
            Moment.CORRECTION,
        )
        assert "too long" in verdict.render()


class TestRefusalBehaviour:
    @pytest.mark.parametrize(
        "text",
        [
            "I cannot fulfill that request.",
            "Against my guidelines, I cannot do that.",
            "I'm sorry, but I can't help with that.",
            "I must decline.",
        ],
    )
    def test_policy_speech_is_caught(self, text):
        """A refusal must name the real reason, not a policy.

        "Against my guidelines" tells the user nothing they can act on.
        "This machine has no GPU" tells them exactly what the limit is and
        what to do about it.
        """
        assert "policy speech" in review(text, Moment.REFUSAL).render()

    def test_a_bare_no_is_too_thin(self):
        """Floor of five words, so a refusal always carries its reason."""
        assert "too thin" in review("No.", Moment.REFUSAL).render()

    def test_a_named_limit_with_an_alternative_passes(self):
        verdict = review(
            "No GPU on this machine, so training is out. I can run the data prep "
            "and hand you the script.",
            Moment.REFUSAL,
        )
        assert verdict.ok, verdict.render()


class TestLengthRefusalRegression:
    """The bug that is banned by name.

    The persona used to say "Short and concrete". Every tier of the cascade read
    that as a rule, so an 800-word essay request came back as "I cannot write
    an 800-word essay, my operating principles require short answers" - the
    assistant refusing because it was told to be brief, which is a persona
    overruling the user. The exact shape is banned, globally, so it cannot come
    back through a different moment.
    """

    def test_the_length_refusal_is_caught(self):
        verdict = review(
            "I cannot write that, my operating principles require short answers.",
            Moment.REFUSAL,
        )
        assert "refusal for length" in verdict.render()

    def test_the_length_refusal_is_caught_outside_the_refusal_stance(self):
        """It has to be global.

        A model asked for an essay will produce that shape under any heading,
        and a ban that only fires in Moment.REFUSAL is a ban that mostly misses.
        """
        verdict = review(
            "I am not designed to write long answers, so I cannot do that essay.",
            Moment.REQUEST,
        )
        assert "refusal for length" in verdict.render()

    def test_writing_a_long_thing_is_never_a_violation(self):
        """The ban is on refusing to write, not on writing.

        A long answer that actually answers is the correct behaviour and must
        pass clean in every moment.
        """
        essay = " ".join(
            f"That is sentence number {n} of the essay and it says something real." for n in range(1, 41)
        )
        for moment in Moment:
            verdict = review(essay, moment)
            assert "refusal for length" not in verdict.render(), moment

    def test_the_prompt_does_not_contain_the_instruction_that_caused_it(self):
        from core.persona import build_persona

        class _Reg:
            def list_tools(self):
                return {"tools": ["file_read"]}

        text = build_persona(registry=_Reg()).describe()
        assert "Short and concrete" not in text
        assert "Never refuse" in text


# --------------------------------------------------------------------------
# Review must not fire on honest text
# --------------------------------------------------------------------------


class TestNoFalsePositives:
    """The checks that matter most, because a personality layer that mangles a
    correct answer is worse than no personality at all."""

    @pytest.mark.parametrize(
        "text",
        [
            "The distance is 3.5 km, so that is a 25 minute walk.",
            "Right, it needs a value like 3.5 or 8.0 in the config.",
            "No. There is no way to know that from here.",
            "As far as I know the build is green, but I am not certain.",
            "I do not have a preference. Coffee is fine.",
            "I can neither confirm nor deny that the test passed.",
            "The rule I follow is: say the true thing first.",
            "e.g. the config file, or the .env next to it.",
        ],
    )
    def test_honest_text_is_never_flagged(self, text):
        verdict = review(text, Moment.REQUEST)
        assert verdict.ok, f"{text!r} -> {verdict.render()}"

    def test_a_long_correct_answer_is_not_flagged_as_long(self):
        """Only the moments with a cap have one.

        A request for depth must be able to produce depth, which is the exact
        regression the length-refusal tests above guard against.
        """
        long_answer = " ".join(f"Point {n} explains something real." for n in range(1, 60))
        assert review(long_answer, Moment.REQUEST).ok

    def test_a_wrong_answer_is_never_rewritten(self):
        """review reports; it does not edit.

        Silently rewriting model output is a personality layer that can only
        subtract, and the day it mangles a correct answer it is a liability.
        """
        text = "Certainly! Great question! Hope this helps!"
        assert review(text, Moment.REQUEST).ok is False
        assert text == "Certainly! Great question! Hope this helps!"


class TestReviewIsWellFormed:
    def test_an_empty_answer_does_not_crash(self):
        """A tool that returned nothing reaches review as ""."""
        assert isinstance(review("", Moment.REQUEST).violations, tuple)

    def test_a_passing_review_is_truthy(self):
        assert review("Restarted on 8080, it is up.", Moment.REQUEST)
        assert not review("Certainly! Hope this helps!", Moment.REQUEST)

    def test_violations_name_the_rule(self):
        """A report that does not say which rule broke is not actionable."""
        verdict = review("Something went wrong.", Moment.TOOL_FAILED)
        assert verdict.violations
        assert all(v.rule for v in verdict.violations)
        assert "unnamed failure" in verdict.render()

    def test_the_moment_is_carried_on_the_review(self):
        assert review("hi", Moment.GREETING).moment is Moment.GREETING


# --------------------------------------------------------------------------
# Sentence counting
# --------------------------------------------------------------------------


class TestSentenceCount:
    def test_decimals_do_not_count_as_sentences(self):
        """The reason the naive split is not used.

        Splitting on the punctuation alone inflates "3.5" into two sentences
        and then fails a correct answer for being too long - a false positive
        that would train a caller to distrust the checker.
        """
        assert sentences("The distance is 3.5 km, so a 25 minute walk.") == 1
        assert sentences("Use 3.5 or 8.0. That is the whole config.") == 2

    def test_common_abbreviations_do_not_split(self):
        assert sentences("It failed because of the config, e.g. the port line.") == 1

    def test_ordinary_answers_count_correctly(self):
        assert sentences("One thing. Two things.") == 2
        assert sentences("Really? Yes! Fine.") == 3
        assert sentences("") == 0

    def test_the_cap_is_in_real_sentences_not_fragments(self):
        """A greeting cap of two must survive a decimal in the reply.

        This test was originally a genuine bug report against the checker: the
        splitter read "3.5" as a sentence end, so a correct two-sentence
        greeting was failed for being too long. A checker that fails correct
        answers is worse than no checker, because the fix is to stop reading it.
        """
        greeting = "Evening. GPU is at 71C, disk 40% full and nothing is queued, for what that is worth."
        assert sentences(greeting) == 2
        assert review(greeting, Moment.GREETING).ok, review(greeting, Moment.GREETING).render()

    def test_a_run_on_greeting_is_caught_by_the_word_cap(self):
        """One sentence, far too long.

        The sentence cap cannot see this shape - it is a single sentence, so
        the cap of two is satisfied - and a small model produces it constantly,
        which is why the word cap exists as a separate check rather than as a
        tightening of the sentence cap.
        """
        run_on = (
            "Evening, I went ahead and restarted the worker, freed port 8080 which "
            "was the thing holding it up, and confirmed the queue is now empty, so "
            "you should be entirely clear to go."
        )
        assert sentences(run_on) == 1, "this test only means something if it is one sentence"
        assert len(run_on.split()) > 32
        assert "too long" in review(run_on, Moment.GREETING).render()


# --------------------------------------------------------------------------
# The stances, as prompt text
# --------------------------------------------------------------------------


class TestStances:
    @pytest.mark.parametrize("moment", list(Moment))
    def test_every_moment_has_a_stance(self, moment):
        stance = stance_for(moment)
        assert stance.moment is moment
        assert stance.lead.strip(), f"{moment} has no lead"
        assert stance.render().strip()

    @pytest.mark.parametrize("moment", list(Moment))
    def test_no_stance_lead_itself_breaks_its_own_rule(self, moment):
        """The instructions must be clean under the rules they enforce.

        The correction and refusal stances name "you're absolutely right" and
        "I cannot fulfill" in their prompt text, because a model told not to do
        something it has never heard of will do it. So the phrase has to be
        present in the instructions *and* in the banned vocabulary, and the two
        have to stay in step - this is what keeps them in step.

        Only the banned phrases and the caps apply. ``requires_one`` is a rule
        about what a *reply* must contain, and a lead is an instruction to the
        model, not a reply - holding "say what you are in one sentence" to the
        same standard as "I am HERMUS, on this PC" would be a test asserting
        something false about the shape of the thing.
        """
        stance = stance_for(moment)
        verdict = review(stance.lead, moment)
        offending = [v for v in verdict.violations if v.rule != "missing"]
        assert not offending, f"{moment}: {offending}"

    @pytest.mark.parametrize("moment", list(Moment))
    def test_a_banned_phrase_named_in_a_rule_is_actually_banned(self, moment):
        """The two halves of the contract, checked together.

        Every banned phrase below is one the prompt also *names* - "you're
        absolutely right", "I cannot fulfill" - because a model told not to do
        something it has never heard of will do it. So the phrase has to be in
        the instructions and in the checkable vocabulary, and the two have to
        stay in step. This is what keeps them in step.

        The examples are written out rather than generated from the pattern: a
        string synthesised from a regex only proves the regex is consistent with
        itself, which is not the claim being made.
        """
        stance = stance_for(moment)
        labels = {label for label, _ in stance.all_forbids()}
        for label, sample in BANNED_EXAMPLES.items():
            if label not in labels:
                continue
            verdict = review(sample, moment)
            assert label in verdict.render(), f"{moment}: {label} named but not caught on {sample!r}"

    @pytest.mark.parametrize("label", sorted(BANNED_EXAMPLES))
    def test_every_banned_phrase_is_banned_somewhere(self, label):
        """No dead bans.

        A pattern nothing applies to is dead code that reads like a rule, which
        is worse than an honest absence: the next person to touch this file
        assumes it is enforced.
        """
        used = [
            moment
            for moment in Moment
            if label in {lbl for lbl, _ in stance_for(moment).all_forbids()}
        ]
        assert used, f"{label} is listed as a rule but no stance bans it"

    def test_every_moment_renders_no_duplicate_rule(self):
        """Rendered obligations are not printed twice.

        The base rules are already in the prompt on every turn, so a stance that
        repeats them wastes tokens on every single call and trains a reader to
        skim.
        """
        for moment in Moment:
            stance = stance_for(moment)
            lines = [line for line in stance.render().splitlines() if line.strip()]
            assert len(lines) == len(set(lines)), moment

    def test_the_agent_name_is_used_for_the_identity_rule(self):
        """The identity rule names the real agent, not a hardcoded one.

        Same principle as the capability list in persona.py: a claim about
        identity that is written by hand goes stale the moment the name is
        configured to something else.
        """
        assert "ARIA" in stance_for(Moment.IDENTITY, agent_name="ARIA").render()
        assert "HERMUS" in stance_for(Moment.IDENTITY, agent_name="HERMUS").render()

    def test_the_base_rules_are_few_and_brief(self):
        """They ship on every single turn.

        A model handed twenty personality rules applies them uniformly, which is
        the flatness the per-moment stances exist to break. Four lines, and each
        one is checkable or is about a real failure.
        """
        assert 1 <= len(BASE_RULES) <= 6
        assert all(len(rule) < 200 for rule in BASE_RULES)

    def test_stances_are_cached_not_rebuilt(self):
        """Read several times a minute on a voice assistant."""
        assert stance_for(Moment.GREETING) is stance_for(Moment.GREETING)

    def test_a_different_agent_name_gets_its_own_table(self):
        assert stance_for(Moment.IDENTITY, agent_name="A").render() != stance_for(
            Moment.IDENTITY, agent_name="B"
        ).render()


# --------------------------------------------------------------------------
# Wiring into the prompt
# --------------------------------------------------------------------------


class TestPersonaWiring:
    class _Reg:
        def __init__(self, tools):
            self._tools = tools

        def list_tools(self):
            return {"tools": list(self._tools)}

    def _persona(self):
        from core.persona import build_persona

        return build_persona(registry=self._Reg(["file_read", "shell_exec", "web_fetch"]))

    def test_the_base_persona_carries_the_base_rules(self):
        text = self._persona().describe()
        for rule in BASE_RULES:
            assert rule in text

    def test_the_base_persona_stays_short(self):
        """Prepended to every turn; length is paid per turn.

        The cap held when the base rules were added, which is the point of
        keeping the per-moment rules out of this path.
        """
        assert len(self._persona().describe()) < 2000

    def test_a_situation_block_is_appended_on_demand(self):
        persona = self._persona()
        base = persona.describe()
        specific = persona.describe_for(Moment.CORRECTION)
        assert specific.startswith(base)
        assert len(specific) > len(base)
        assert stance_for(Moment.CORRECTION).lead in specific

    def test_the_situation_block_does_not_bloat_every_turn(self):
        """It is per-turn, so the worst case still has to be sane."""
        for moment in Moment:
            assert len(self._persona().describe_for(moment)) < 2800, moment

    def test_the_prompt_keeps_the_honesty_rules(self):
        """The character must not eat the grounding.

        The reason the persona exists is that a model told it is a great
        assistant claims abilities it does not have. Adding a character on top
        is only worth anything if the grounding survives it.
        """
        text = self._persona().describe_for(Moment.GREETING)
        assert "Never claim a capability" in text
        assert "What you cannot do" in text
        assert "If you do not know, say so" in text

    def test_the_capability_list_still_comes_from_the_registry(self):
        text = self._persona().describe()
        assert "3 tools registered" in text
        assert "reach the network" in text

    def test_loading_the_persona_does_not_require_demeanour_at_call_time(self):
        """A broken import must not take the room down.

        persona.py is imported on every chat turn, so the behaviour rules are
        worth less than the assistant being able to answer at all.
        """
        from core.persona import build_persona

        persona = build_persona(registry=self._Reg([]))
        assert persona.tool_count == 0
        assert persona.name in persona.describe()
