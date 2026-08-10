# ☤ Hermus — Quick Start (from scratch)

## One command (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/trmv2007-bot/hermus-agent-free/main/install.sh | bash
```

That will:

1. Clone the repo to `~/hermus-agent-free` (if needed)
2. Install system basics when possible (git, python3, venv, ffmpeg)
3. Create `.venv` and install **all** Python dependencies
4. Install Playwright Chromium (browser tools)
5. Create launchers: `./hermus`, `./bin/hermus-gateway`
6. Verify tools/agent/gateway import
7. Print exactly how to chat + open the dashboard

---

## One command + extras

```bash
# Full setup + local Ollama model
curl -fsSL https://raw.githubusercontent.com/trmv2007-bot/hermus-agent-free/main/install.sh | bash -s -- --with-ollama

# Full setup + save Groq key + start dashboard
curl -fsSL https://raw.githubusercontent.com/trmv2007-bot/hermus-agent-free/main/install.sh | bash -s -- \
  --groq-key gsk_YOUR_KEY \
  --start

# Custom OpenAI-compatible gateway URL + key
curl -fsSL https://raw.githubusercontent.com/trmv2007-bot/hermus-agent-free/main/install.sh | bash -s -- \
  --custom-key sk_YOUR_KEY \
  --custom-base-url https://your-gateway.example.com/v1 \
  --custom-model my-model
```

---

## Already cloned?

```bash
git clone https://github.com/trmv2007-bot/hermus-agent-free.git
cd hermus-agent-free
bash setup.sh
```

Flags:

| Flag | Meaning |
|------|---------|
| `--yes` | Non-interactive |
| `--minimal` | Lighter deps (no browser/whisper) |
| `--with-ollama` | Install Ollama + pull `llama3.1:8b` |
| `--no-browser` | Skip Playwright |
| `--groq-key KEY` | Register Groq during setup |
| `--openrouter-key KEY` | Register OpenRouter |
| `--openai-key KEY` | Register OpenAI |
| `--custom-key` + `--custom-base-url` | Any OpenAI-compatible API |
| `--start` | Start gateway/dashboard when done |
| `--port 8000` | Gateway port |
| `--dir ~/hermus-agent-free` | Install location |

---

## After setup — every day

```bash
cd ~/hermus-agent-free
source activate.sh

# Chat
./hermus --model groq/llama-3.1-8b-instant
# or local:
./hermus --model ollama/llama3.1:8b

# Dashboard
./bin/hermus-gateway
# → http://localhost:8000/dashboard
```

### Add any API key later

```bash
./hermus multikey add --provider groq --key gsk_...
./hermus multikey add --provider custom --key sk_... \
  --base-url https://your-gateway.example.com/v1 --model my-model
./hermus multikey health
```

### Multi-model fleet

```bash
./hermus fleet run "Research X and recommend Y" --strategy auto
```

---

## Requirements (short)

| Mode | Specs |
|------|--------|
| Cloud keys only | Python 3.10+, ~2 GB RAM, internet |
| Ollama `llama3.1:8b` | ~8–16 GB RAM, ~15 GB disk |
| Full (browser+voice) | +1–2 GB RAM, Playwright Chromium |

---

## Troubleshooting

```bash
# Re-run setup anytime (safe)
cd ~/hermus-agent-free && bash setup.sh --yes

# Fix browser
source activate.sh && python -m playwright install chromium

# Check health
./hermus multikey health
./hermus tools
```
