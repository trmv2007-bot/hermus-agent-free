"""Shared implementation behind the twenty-one free-clone skills.

``skills/01_web_research_agent`` … ``skills/21_pii_sanitization_agent`` are free
re-implementations of agents from the `500-AI-Agents-Projects`_ collection. They
were twenty-one byte-identical 65-line files differing only in six strings, so
the procedure lives here once and each skill keeps a thin ``skill.py`` that
declares its identity and delegates.

What the implementation is: search DuckDuckGo (free, no key) for the query,
synthesise a structured report with the local free LLM (``core.llm.free_llm``),
and return the same result shape the original files returned — with the same
mock fallback when either free tool is unavailable, so a skill that cannot reach
the network degrades honestly instead of raising.

.. _500-AI-Agents-Projects: https://github.com/ashishpatel26/500-AI-Agents-Projects
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FreeClone:
    """Identity of one free-clone skill (the only thing that ever differed)."""

    name: str
    slug: str
    framework: str
    industry: str
    task: str

    @property
    def original(self) -> str:
        return f"https://github.com/ashishpatel26/500-AI-Agents-Projects/tree/main/{self.slug}"

    @property
    def note(self) -> str:
        return (
            f"Free clone - original uses {self.framework} + GPT-4o-mini + Tavily paid, "
            "this uses Ollama local free + DuckDuckGo free + SQLite FTS5 free"
        )


FREE_CLONES: tuple[FreeClone, ...] = (
    FreeClone(
        name="01_web_research_agent",
        slug="01-web-research-agent",
        framework="langgraph",
        industry="general",
        task="Searches the web for a topic and synthesizes a structured research report",
    ),
    FreeClone(
        name="02_code_review_agent",
        slug="02-code-review-agent",
        framework="langchain",
        industry="software-development",
        task="Reviews code for bugs, security issues, performance, and style violations",
    ),
    FreeClone(
        name="03_pdf_qa_agent",
        slug="03-pdf-qa-agent",
        framework="llamaindex",
        industry="research",
        task="Loads a PDF and answers questions about its content with conversation history",
    ),
    FreeClone(
        name="04_sql_query_agent",
        slug="04-sql-query-agent",
        framework="langchain",
        industry="data-analytics",
        task="Answers natural language questions about SQL databases by generating and executing queries",
    ),
    FreeClone(
        name="05_email_drafting_agent",
        slug="05-email-drafting-agent",
        framework="crewai",
        industry="communication",
        task="Two-agent CrewAI system that drafts professional emails from context",
    ),
    FreeClone(
        name="06_news_summarizer_agent",
        slug="06-news-summarizer-agent",
        framework="langchain",
        industry="media",
        task="Fetches news on any topic and produces a structured briefing with key themes",
    ),
    FreeClone(
        name="07_github_issue_triager",
        slug="07-github-issue-triager",
        framework="langchain",
        industry="software-development",
        task="Automatically triages GitHub issues with severity, category, and routing recommendations",
    ),
    FreeClone(
        name="08_data_analysis_agent",
        slug="08-data-analysis-agent",
        framework="langchain",
        industry="analytics",
        task="Chat with CSV/Excel data using natural language queries powered by pandas",
    ),
    FreeClone(
        name="09_resume_parser_agent",
        slug="09-resume-parser-agent",
        framework="langchain",
        industry="human-resources",
        task="Parses resumes to structured JSON and scores candidate fit against job descriptions",
    ),
    FreeClone(
        name="10_meeting_notes_agent",
        slug="10-meeting-notes-agent",
        framework="langchain",
        industry="productivity",
        task="Converts meeting transcripts into structured notes with action items and decisions",
    ),
    FreeClone(
        name="11_stock_research_agent",
        slug="11-stock-research-agent",
        framework="langchain",
        industry="finance",
        task="Real-time stock fundamentals with AI-powered investment analysis",
    ),
    FreeClone(
        name="12_travel_planner_agent",
        slug="12-travel-planner-agent",
        framework="crewai",
        industry="travel",
        task="Multi-agent CrewAI system creating personalized travel itineraries with budget planning",
    ),
    FreeClone(
        name="13_customer_support_agent",
        slug="13-customer-support-agent",
        framework="langgraph",
        industry="customer-service",
        task="RAG-powered customer support agent with escalation routing using LangGraph",
    ),
    FreeClone(
        name="14_social_media_agent",
        slug="14-social-media-agent",
        framework="crewai",
        industry="marketing",
        task="Generates platform-optimized content for Twitter, LinkedIn, and Instagram",
    ),
    FreeClone(
        name="15_unit_test_generator",
        slug="15-unit-test-generator",
        framework="langchain",
        industry="software-development",
        task="Generates comprehensive pytest test suites from Python code",
    ),
    FreeClone(
        name="16_documentation_writer",
        slug="16-documentation-writer",
        framework="langchain",
        industry="software-development",
        task="Generates README and docstrings for Python modules",
    ),
    FreeClone(
        name="17_recipe_agent",
        slug="17-recipe-agent",
        framework="langchain",
        industry="food",
        task="Suggests recipes from available ingredients with instructions and nutrition info",
    ),
    FreeClone(
        name="18_job_application_agent",
        slug="18-job-application-agent",
        framework="crewai",
        industry="human-resources",
        task="Generates cover letter, interview prep, and salary range from job description + candidate profile",
    ),
    FreeClone(
        name="19_competitive_analysis_agent",
        slug="19-competitive-analysis-agent",
        framework="langgraph",
        industry="business",
        task="Multi-step LangGraph agent for comprehensive competitive landscape analysis",
    ),
    FreeClone(
        name="20_multi_agent_debate",
        slug="20-multi-agent-debate",
        framework="langchain",
        industry="research",
        task="Two AI agents debate any topic with an AI judge scoring the outcome",
    ),
    FreeClone(
        name="21_pii_sanitization_agent",
        slug="21-pii-sanitization-agent",
        framework="other",
        industry="general",
        task="Sanitizes PII from text before it reaches LLMs or external APIs, using the TrustBoost API — fail-closed, multilingual, on-chain proof",
    ),
)

CLONES: dict[str, FreeClone] = {spec.name: spec for spec in FREE_CLONES}


def run_free_clone(spec: FreeClone, query: str = "default query", **kwargs: Any) -> dict[str, Any]:
    """Run one free clone: free search + free local LLM, with an honest fallback."""
    _ = kwargs  # skill_use passes task context; the free path only needs the query
    try:
        from core.llm import free_llm
        from tools.web_search import web_search

        search_results = web_search(query, max_results=3)
        prompt = (
            f"Task: {spec.task}\nQuery: {query}\nSearch results: {str(search_results)[:1000]}\n\n"
            f"Provide structured report like original {spec.slug} would."
        )
        messages = [
            {
                "role": "system",
                "content": f"You are {spec.name} - {spec.task}. Provide structured report.",
            },
            {"role": "user", "content": prompt},
        ]
        resp = free_llm.chat(messages)
        return {
            "skill": spec.name,
            "original": spec.original,
            "framework": spec.framework,
            "industry": spec.industry,
            "query": query,
            "search_results": search_results[:2],
            "report": resp.content[:2000],
            "method": "free_clone_using_ollama_duckduckgo_sqlite",
            "note": spec.note,
        }
    except Exception as exc:
        # Fallback mock if the free tools are not available.
        return {
            "skill": spec.name,
            "original": spec.original,
            "framework": spec.framework,
            "query": query,
            "report": (
                f"Mock report for {query} - {spec.task}. This is free clone of {spec.slug} "
                f"from 500-AI-Agents-Projects (36k stars). Original uses {spec.framework}. "
                "Free version uses Ollama local + DuckDuckGo free."
            ),
            "error_fallback": str(exc)[:200],
            "method": "mock_free_fallback",
        }


__all__ = ["CLONES", "FREE_CLONES", "FreeClone", "run_free_clone"]
