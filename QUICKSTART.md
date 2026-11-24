# Hermus Agent Free — quickstart

A free desktop companion that runs on your own computer. This page is enough to
install it and get a real answer. The developer path is at the bottom.

---

## 1. Install

**Windows 10/11** — open PowerShell and paste this one line:

```powershell
$p = Join-Path $env:TEMP "hermus-install.ps1"; Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/trmv2007-bot/hermus-agent-free/main/install.ps1" -OutFile $p; powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p
```

**macOS or Linux** — paste this one line in Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/trmv2007-bot/hermus-agent-free/main/install.sh | bash
```

It takes a few minutes. It installs into its own folder and never touches your
documents. Running it twice is safe.

## 2. It answers before it asks

You do not have to install anything to get a first answer. With no local model
and no key, HERMUS answers through a free endpoint that needs no account —
Pollinations first, OVHcloud AI Endpoints beneath it — and tells you which one
answered and that your words went there. It also prints the one line that makes
it faster.

The installer still offers a model on your own computer, because local is faster
and nothing leaves the machine:

```
No model was found on this machine. Install Ollama (free, no account) and
download llama3.2:1b?  [Y/n]
```

Say yes. That downloads about 0.6 GB once, stays on your computer, needs no
account, and costs nothing. Press `n` and the keyless tier keeps answering;
paste a free-tier key (Groq, Mistral, OpenRouter or Gemini — all free, no card)
if you want a much larger allowance.

## 3. Open it

```bash
hermus gateway start
```

Then open **http://localhost:8000/** in your browser.

The first screen asks three things, each with an answer already chosen, so you
can press Enter three times:

- **What should answer you** — the model on your machine, a free key, or nothing.
- **What may it do** — Watch only, Ask first, or Act freely.
- **What should it be called** — `Hermus` unless you have a better idea.

The last step types a real question to the model you chose and streams the
answer back. That is the proof it works. If no model can answer, it says so once
and gives you the command that fixes it — it does not pretend.

## 4. Use it

After that, http://localhost:8000/ opens the companion: one character and one
input line. Type, press Enter, read.

Every operator panel — jobs, approvals, memory, the whole Control Room — is
behind the quiet **Systems** control in the bottom-right corner. You do not need
it to use the product.

---

## Common questions

**Does it cost anything?** No. Local models are free. The free-tier keys are
free plans with no card. There is no paid tier in this build.

**Does my data leave my computer?** Not with a local model. Only a free-tier key
sends your words to that provider, and never in the default setup.

**Where are my files?** Hermus keeps everything in `~/.hermus` (Windows:
`C:\Users\<you>\.hermus`). Deleting that folder resets it completely, and
nothing outside it is touched.

**How do I change the model or its permissions later?** Open the welcome screen
at http://localhost:8000/welcome, or the Control Room at `/control`. The choice
is written into permission overrides you can edit by hand.

**Nothing answers.** Run `hermus bootstrap model` — it prints the model that will
answer, where that choice came from, and the one command to fix it if there is
none.

---

# Developer path

Everything below assumes you are changing the code.

## From a clone

```bash
git clone https://github.com/trmv2007-bot/hermus-agent-free.git
cd hermus-agent-free
./setup.sh            # Windows: .\setup.cmd
```

`setup.sh` / `setup.ps1` own host detection and packages; `bootstrap.py` owns the
virtualenv, dependencies, data layout and the live Doctor. Useful forms:

```bash
./setup.sh --verify-only     # change nothing, just run the Doctor
./setup.sh --repair          # repair a broken .venv and retry
./setup.sh --skip-optional   # required packages only
```

## Checks

```bash
make test        # pytest tests/ (skips tests/eval), ~1 min
make lint        # ruff, whole tree, line length 130
make typecheck
make selfcheck   # offline self-test on mock/mock with a scoreboard
```

`test`, `lint` and `typecheck` must stay green. New behaviour gets a test; the
README's build-status table cites the test behind every row.

## Runtime

```bash
./hermus bootstrap              # setup + health in one command
./hermus bootstrap model        # which model will answer, and why
./hermus doctor                 # full diagnostics
./hermus gateway start          # gateway + companion UI
./hermus mission "goal"         # autonomous mission
./hermus status --live          # live capability state
```

## Surfaces

| Address | What it is |
|---|---|
| `/` | Welcome while setup is incomplete, companion once it is |
| `/welcome` | The three first-run choices and the model proof |
| `/companion` | The companion: character, one input line |
| `/control` | The Control Room — eleven operator panels |
| `/docs` | OpenAPI |
| `/readyz` | Real readiness, the same answer the boot line prints |

## Optional integrations

Optional packages degrade to an explicit `unavailable` state with a reason; they
never break import, startup or a test. `requirements-optional.txt` lists them.

## Environment

Every field is overridable with a `HERMUS_` prefix (`HERMUS_MODEL`,
`HERMUS_HOME`, `HERMUS_GATEWAY_PORT`, …). `HERMUS_MOCK_UI=1` enables the
dashboard's mock fixture layer, which is a design tool and is disabled otherwise.
