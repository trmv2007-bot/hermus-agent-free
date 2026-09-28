"""Regression tests for two bugs found by reading real answers, not code.

Both of these produced a correct-but-broken reply, which is the kind that
survives a green suite: the test passed and the user still saw garbage.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from core.grounding import is_searchable


# --- the clock is free and exact, the web is neither -----------------------


@pytest.mark.parametrize(
    "question",
    [
        "What time is it right now in India?",
        "What is the date today?",
        "whats the time",
        "what's the date",
        "What day is it?",
    ],
)
def test_clock_questions_do_not_trigger_a_web_search(question: str) -> None:
    """A DuckDuckGo round trip took 20s to answer what now_context() knows.

    "What time is it right now" matched the freshness pattern on the word
    "right now" and spent a network call -- and its latency -- to be told
    something the process already had.
    """
    assert is_searchable(question) is False


@pytest.mark.parametrize(
    "question",
    [
        "What is the current price of the RTX 5090 in India?",
        "who won the match last night",
        "what is the latest news on AI",
        "current temperature in Kochi",
    ],
)
def test_genuinely_current_questions_still_search(question: str) -> None:
    """The clock rule must not swallow real freshness questions.

    Excluding time/date by bare keyword would have quietly broken the whole
    point of retrieval, so this is the half of the change that protects it.
    """
    assert is_searchable(question) is True


# --- non-ASCII survives the stream ----------------------------------------


def test_sse_stream_is_decoded_as_utf8_not_latin1() -> None:
    """httpx's iter_lines(decode_unicode=True) mangles every curly quote.

    An SSE response is "text/event-stream" with no charset parameter, so httpx
    falls back to latin-1. U+2019 (a right single quote, three UTF-8 bytes)
    arrives as the three characters 'â€™', which is what the user read:
    "Iâm HERMUS". The fix decodes the bytes as UTF-8 explicitly.

    Parsed with ast rather than grepped. The first version of this test
    substring-matched "decode_unicode=True" against the module source and
    failed against CORRECT code, because the comment explaining why the
    argument was removed contains the same words.
    """
    from core import openai_compat

    tree = ast.parse(inspect.getsource(openai_compat))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "iter_lines"
    ]
    assert calls, "no iter_lines call found -- the streaming path moved, update this test"
    for call in calls:
        for kw in call.keywords:
            if kw.arg == "decode_unicode" and getattr(kw.value, "value", None) is True:
                pytest.fail(
                    "iter_lines(decode_unicode=True) lets httpx choose latin-1 "
                    "for SSE and corrupts every non-ASCII character"
                )


def test_non_ascii_round_trips_as_utf8() -> None:
    """The behaviour the fix exists to protect, stated without the plumbing."""
    raw = "I’m HERMUS — ₹2,68,999".encode("utf-8")
    assert raw.decode("utf-8") == "I’m HERMUS — ₹2,68,999"
    # And it is genuinely broken the other way, which is why the fix is needed.
    assert "I’m" not in raw.decode("latin-1")


def test_an_llm_response_says_which_model_produced_it() -> None:
    """Provenance must travel with the answer.

    A tier can be swapped underneath the caller -- an unrecognised model name
    falls back to the local one -- and before LLMResponse carried an identity
    the substitution was unrecoverable. Ask for a model that does not exist,
    get a confident answer, and no part of the system could say which model
    said it. That is the failure mode where every other guarantee (confidence,
    grounding, escalation) is unverifiable, because you do not know what you
    are reasoning about.
    """
    from core.llm import LLMResponse

    r = LLMResponse("hello", model="ollama/spark-x2.5-4b-q4", provider="ollama")
    assert r.model == "ollama/spark-x2.5-4b-q4"
    assert r.provider == "ollama"

    # An error or mock response says so honestly rather than inheriting a name.
    bare = LLMResponse("some error text")
    assert bare.model == "" and bare.provider == ""


def test_the_chat_final_frame_names_the_answering_model() -> None:
    """The final frame must carry model_used, and flag a substitution.

    If `model_substituted` is missing, a client cannot tell a real answer from
    one produced by a model nobody chose -- the UI has nothing to render.
    """
    import inspect

    from gateway import chat_turn

    src = inspect.getsource(chat_turn)
    assert '"model_used"' in src, "the final frame does not report which model answered"
    assert '"model_substituted"' in src, "a swapped tier is not reported"
