---
name: 03_pdf_qa_agent
description: Loads a PDF and answers questions about its content with conversation history - Use when user says 03 pdf qa agent or needs research agent - Framework: llamaindex - From 500 AI Agents Projects (36k stars) - 03-pdf-qa-agent
---

# pdf-qa-agent - 03_pdf_qa_agent

Loads a PDF and answers questions about its content with conversation history

**Framework:** llamaindex  
**Industry:** research  
**Original:** https://github.com/ashishpatel26/500-AI-Agents-Projects/tree/main/03-pdf-qa-agent  
**Tags:** llamaindex, research, 500-agents, free

## What it does

Loads a PDF and answers questions about its content with conversation history

Original agent from 500-AI-Agents-Projects collection - 03-pdf-qa-agent:

- Takes user query/task
- Uses llamaindex framework
- Produces structured output

## Original Setup (from 500-AI-Agents-Projects)

```bash
cd agents/03-pdf-qa-agent
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your API keys
python agent.py
```

## Free Clone Implementation

This free clone in Hermus Agent Free uses:

- Ollama local free LLMs (llama3.1:8b) instead of GPT-4o-mini OpenAI (no API key needed)
- DuckDuckGo free search instead of Tavily paid (free)
- SQLite FTS5 memory instead of Pinecone paid
- File-based skills compatible with agentskills.io

## Usage in Hermus Free

```
/03_pdf_qa_agent
```

Or:

```
You: I need 03 pdf qa agent
Hermus: [Tool] skill_use({"name": "03_pdf_qa_agent"})
```

## When to use

Use this skill when user says:
- "03 pdf qa agent"
- "research agent"
- "I need Loads a PDF and answers questions about its conten"

## Framework Comparison (from 500-AI-Agents-Projects)

| Framework | Best For | Complexity | Multi-Agent | Streaming | Local LLM |
| LangGraph | Stateful workflows, RAG pipelines | ⭐⭐⭐ | ✅ | ✅ | ✅ |
| CrewAI | Role-based teams, business automation | ⭐⭐ | ✅ | ✅ | ✅ |
| AutoGen | Code generation, research | ⭐⭐⭐ | ✅ | ✅ | ✅ |
| Agno | Lightweight single agents | ⭐ | ✅ | ✅ | ✅ |
| LlamaIndex | Document Q&A, enterprise RAG | ⭐⭐ | ⚠️ | ✅ | ✅ |

Quick decision:
- Just starting → Agno or CrewAI
- Need stateful graphs + RAG → LangGraph
- Code-writing/research → AutoGen
- Enterprise doc pipelines → LlamaIndex

## Industry Use Cases (from 500-Agents)

This agent belongs to research industry. Other industries in collection:

- Healthcare: Health Insights Agent, AI Health Assistant
- Finance: Automated Trading Bot, Agent Wallet SDK
- Education: Virtual AI Tutor
- Customer Service: 24/7 AI Chatbot
- Retail: Product Recommendation Agent
- Transportation: Self-Driving Delivery Agent
- Manufacturing: Factory Process Monitoring
- Real Estate: Property Pricing Agent
- Agriculture: Smart Farming Assistant
- Energy: Energy Demand Forecasting Agent
- Entertainment: Content Personalization Agent
- Legal: Legal Document Review Assistant
- HR: Recruitment Recommendation Agent
- Hospitality: Virtual Travel Assistant
- Gaming: AI Game Companion Agent
- Cybersecurity: Real-Time Threat Detection Agent (like Vrindha!)
- E-commerce: Personal Shopper Agent

See full 500+ use cases: https://github.com/ashishpatel26/500-AI-Agents-Projects

## Free Implementation Notes

This skill in Hermus Free is a free clone using:
- Ollama local free LLMs (no API key)
- DuckDuckGo free search (no API key)
- SQLite FTS5 free memory (no Pinecone)
- File-based skills (no paid)

You can run it fully offline on laptop/$5 VPS.

## Original Code Reference

Original agent.py from 500-AI-Agents-Projects has ~100 lines using LangGraph/CrewAI/AutoGen.

This free clone provides simplified version using Hermus tools: web_search (DuckDuckGo free), file_write, etc.

If you want original exact code, clone: https://github.com/ashishpatel26/500-AI-Agents-Projects/tree/main/03-pdf-qa-agent
