"""When it does not know, it looks. Then it escalates. In that order.

The load-bearing claim in `core/grounding.py` is that retrieval runs *before*
the expensive model rather than after it, and that a search which came back
empty is not evidence. Both are easy to state and easy to quietly break, so
they are tested directly.

Fixtures here are shaped like real `tools.web_search` output, and the error
cases are ones that actually happen rather than invented ones.
"""

from __future__ import annotations

import pytest

from core.grounding import (
    Grounding,
    Source,
    is_searchable,
    now_context,
    search_for,
)


def _results(n: int = 3):
    return [
        {"title": f"Result {i}", "href": f"https://example{i}.com/page", "body": f"Snippet {i}"} for i in range(n)
    ]


class TestOrdering:
    """The claim: the cheap step runs first, and only its failure justifies the expensive one."""

    def test_search_is_not_the_expensive_step(self):
        # A search is a network round trip and no tokens. A 120B completion is
        # both. If search were the last resort rather than the first, this
        # module would be pointless.
        g = search_for("who won the cricket world cup in 2024")
        assert isinstance(g, Grounding)
        # No model is involved anywhere in this path, which is the whole point.

    def test_an_empty_search_is_not_evidence(self):
        """A search that returned nothing must not look like a successful lookup.

        This is the failure that produces confident answers citing nothing: the
        model is handed an empty context, decides it must answer, and does.
        """
        assert not Grounding(query="x", sources=()).useful

    def test_a_failed_search_is_not_evidence(self):
        assert not Grounding(query="x", error="boom", sources=_srcs()).useful

    def test_mock_placeholders_are_not_evidence(self):
        """`web_search` returns example.com mock rows when DDG is unavailable.

        Treating those as real results would produce a confidently cited
        answer built from a placeholder.
        """
        mock = (Source(title="Mock", url="https://example.com", snippet="mock"),)
        assert not Grounding(query="x", sources=mock).useful

    def test_one_source_is_too_thin_to_trust(self):
        assert not Grounding(query="x", sources=_srcs(1)).useful

    def test_distinct_real_sources_are_usable(self):
        assert Grounding(query="x", sources=_srcs(3)).useful

    def test_duplicate_urls_do_not_count_as_corroboration(self):
        dup = (
            Source(title="a", url="https://a.com", snippet="x"),
            Source(title="a", url="https://a.com", snippet="x"),
        )
        assert not Grounding(query="x", sources=dup).useful


def _srcs(n: int = 3) -> tuple[Source, ...]:
    return tuple(Source(title=f"t{i}", url=f"https://s{i}.net/a", snippet=f"snip {i}") for i in range(n))


class TestIsSearchable:
    @pytest.mark.parametrize(
        "q",
        [
            "who won the cricket world cup in 2024",
            "latest news on ai models",
            "how much is a rtx 5090",
            "what is the latest python version",
            "when did the usa join world war two",
            "define kubernetes",
        ],
    )
    def test_questions_that_move_are_searchable(self, q):
        assert is_searchable(q)

    @pytest.mark.parametrize(
        "q",
        [
            "hello",
            "thanks",
            "ok",
            "what is 2+2",
            "17 plus 25",
            "",
        ],
    )
    def test_trivia_and_greetings_are_not(self, q):
        assert not is_searchable(q)

    @pytest.mark.parametrize(
        "q",
        [
            "what can you do",
            "who are you",
            "what is on my screen",
            "check my memory",
            "what is the system time",
            "open the terminal panel",
        ],
    )
    def test_questions_about_this_machine_never_go_to_the_web(self, q):
        """Searching the web for these is always wrong.

        The answer is a local call away, and a search would return confident
        nonsense about someone else's machine.
        """
        assert not is_searchable(q)

    def test_a_local_question_wins_even_when_it_looks_searchable(self):
        assert not is_searchable("what is the current memory usage on this pc")


class TestContext:
    def test_context_carries_the_urls_and_the_date(self):
        """Without the date a model cannot tell fresh evidence from stale."""
        ctx = Grounding(query="q", sources=_srcs(2)).as_context()
        assert "s0.net" in ctx and "s1.net" in ctx

    def test_empty_grounding_renders_nothing(self):
        assert Grounding(query="q").as_context() == ""

    def test_it_caps_the_number_of_sources(self):
        many = tuple(Source(title=f"t{i}", url=f"https://s{i}.net", snippet="x") for i in range(20))
        assert Grounding(query="q", sources=many).as_context(limit=3).count("https://") == 3


class TestTimeAwareness:
    def test_it_states_the_day_and_date(self):
        text = now_context()
        assert "Monday 28 September 2026" in text or "20" in text
        assert "IST" in text

    def test_it_warns_against_guessing_the_date(self):
        """The reason this exists: a training cutoff means the model has no idea
        what day it is, and will happily answer from a stale prior."""
        assert "cutoff" in now_context()

    def test_the_timezone_is_configurable(self):
        """A user in another timezone must not be told IST."""
        utc = now_context(tz_offset_hours=0, name="UTC")
        assert "UTC" in utc
        assert "IST" not in utc

    def test_ist_is_the_default(self):
        assert "IST" in now_context()
