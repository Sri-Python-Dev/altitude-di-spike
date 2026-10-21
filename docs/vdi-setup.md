# Setting up on the Windows VDI

Everything below runs in **PowerShell**. Commands are the same on macOS/Linux unless noted.
Synthetic and cleared golden documents only (CLAUDE.md).

## 1. Install the tools (once)

| Tool | How |
|---|---|
| Git | Company software centre, or git-scm.com |
| uv (Python manager) | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` then open a new terminal |
| Python 3.12 | `uv python install 3.12` (uv manages it; no system Python needed) |
| PostgreSQL | Either the Windows installer from postgresql.org, **or** Docker: `docker compose up -d` in the repo (creates both databases) |
| Temporal CLI | Download the Windows build from Temporal's docs (docs.temporal.io → CLI → install), put `temporal.exe` on PATH |

## 2. Get the project

```powershell
git clone <your company repo URL> altitude-di-spike
cd altitude-di-spike
uv sync --python 3.12
Copy-Item .env.example .env
notepad .env
```

Fill in `.env`:

- `OPENAI_API_KEY`
- `DATABASE_URL` and `ALTITUDE_TEST_DATABASE_URL`:
  - Docker: `postgresql://altitude:altitude-local-only@localhost:5432/altitude_di` and `…/altitude_di_test`
  - Installer: `postgresql://postgres:<your password>@localhost:5432/altitude_di` (create both databases first:
    `psql -U postgres -c "create database altitude_di"` and `… altitude_di_test`)
- Keep `PYTHONUTF8=1` (Windows console output).

## 3. Check the machine

```powershell
uv run --env-file .env python tools/check_env.py
```

It lists what works and what is blocked (packages, Hugging Face, OpenAI, Microsoft Graph,
Postgres, Temporal) with the fix for each line. Then:

```powershell
uv run --env-file .env pytest                 # all tests
uv run tools/validate_golden.py data/golden/quotes-v1
```

The Temporal workflow tests download a Temporal test server on their first run; without internet
access they fail and the rest still pass.

## 4. Run it (four terminals)

```powershell
temporal server start-dev                                     # 1  Temporal UI: http://localhost:8233
uv run --env-file .env python worker.py                       # 2
uv run --env-file .env python email_monitor.py                # 3  local inbox folder (data\inbox)
uv run --env-file .env python -m ui.server                    # 4  http://127.0.0.1:8765
```

Restart terminals 2 and 4 after pulling code changes.

Windows has no `make`; the demo targets are:

```powershell
uv run --env-file .env python -m tools.demo reset       # make demo-reset
uv run --env-file .env python -m tools.demo check       # make demo-check
uv run --env-file .env python -m tools.demo add-rule    # make demo-rule
```

To "receive" a test email: `Copy-Item data\synthetic\emails\08-dollar-prices.eml data\inbox\`.

## 5. Corporate network

| Symptom from check_env | Fix |
|---|---|
| PyPI / Hugging Face / OpenAI **blocked** | Ask for the proxy address; add `HTTPS_PROXY=http://proxy:port` to `.env` and set it in the terminal before `uv sync` (`$env:HTTPS_PROXY="…"`) |
| **TLS certificate rejected** | The proxy re-signs traffic: get the company root CA as a `.pem`, set `SSL_CERT_FILE=C:\path\ca.pem` in `.env`, and use `uv sync --native-tls` |
| Hugging Face blocked, everything else fine | Copy the Docling models from a machine that has them: `~/.cache/huggingface/hub/models--*` on the Mac → `%USERPROFILE%\.cache\huggingface\hub\` on the VDI |
| OpenAI blocked | Expected on many corporate networks. Extraction needs a model endpoint: use the Azure AI Foundry route (`llm/config.yaml` → `backend: azure_foundry`, keys in `.env`) once it exists |

Before calling the public OpenAI API from a company machine, confirm it is allowed: the spike sends
the text of synthetic documents only (`docs/spikes/data-path.md`).

## 6. Microsoft Graph mail monitor (optional, off by default)

The monitor reads one Microsoft 365 mailbox folder in near real time (every 15 s) and feeds each
new email into the same workflow as the inbox folder. **It stays off until reading a real mailbox
is approved** (CLAUDE.md: "Do not fetch anything from a real mailbox").

When it is approved, your Microsoft 365 admin creates:

1. **An app registration** (Entra ID → App registrations → New): note the tenant ID and client ID;
   create a client secret.
2. **API permission:** Microsoft Graph → *Application* permission **Mail.Read** → grant admin consent.
3. **Restrict it to the test mailbox only** (otherwise Mail.Read covers every mailbox in the
   tenant): an Exchange Online application access policy or RBAC for Applications, scoped to that one
   mailbox.
4. **A test mailbox** (shared mailbox is fine), ideally with a dedicated folder such as
   "Altitude Test" that only synthetic quotes are moved into.

Then in `.env`:

```
MAIL_MONITOR_ENABLED=true
GRAPH_TENANT_ID=…
GRAPH_CLIENT_ID=…
GRAPH_CLIENT_SECRET=…
GRAPH_MAILBOX=rfq-test@company.com
GRAPH_FOLDER=Altitude Test
GRAPH_ALLOWED_SENDERS=@supplier-test.example     # optional; others are skipped
```

and run terminal 3 as:

```powershell
uv run --env-file .env python email_monitor.py --source graph
```

On its first start it only takes mail that arrives from then on (`--backfill` to include mail
already in the folder). It remembers what it fetched (`data\state\graph_mail.json`), so a restart
neither misses nor repeats mail. It is read-only: nothing is marked, moved or deleted in the
mailbox. Only counts are printed; no subjects or senders.
