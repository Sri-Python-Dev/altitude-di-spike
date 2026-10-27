# Data path — spike (as built, 15 Oct 2026)

**No real supplier document has been processed. Every document handled by this spike is
synthetic or in `data/golden/` (cleared).** No real mailbox is read.

## Where documents come from

| Route | What | Code |
|---|---|---|
| Local inbox folder | `.eml` files saved into `data/inbox/` (synthetic, from `data/synthetic/emails/`). Stands in for a real mailbox: CLAUDE.md forbids reading one until approved. | `email_monitor.py` |
| Microsoft Graph mailbox | **Built, off.** Reads one folder of one test mailbox every 15 s (read-only, Mail.Read limited to that mailbox) and drops each new email into the inbox folder. Runs only with `MAIL_MONITOR_ENABLED=true`. | `email_monitor.py --source graph`, `ingest/graph_mail.py` |
| Upload | A PDF uploaded in the app (`/upload`). | `ui/server.py` |
| Command line | `start_workflow.py <file>` | |
| Evaluation | Golden and synthetic folders read directly by the harness. | `eval/run.py` |

## Where things are stored (all on the spike machine)

| Location | Contents |
|---|---|
| `data/state/blobs/<sha256>/` | Email attachments, by content hash; email bodies with a quote |
| `data/state/uploads/<sha256>/` | Uploaded PDFs |
| `data/state/parsed/`, `data/state/extractions/` | Parsed text per quote; model output before and after grounding (JSON) |
| `data/state/dedupe.sqlite` | Seen Message-IDs and attachment SHA-256s |
| Postgres `altitude_di` (local) | `documents`, `quotes`, `quote_fields` (value, page, bbox, verbatim span, grounded, reason), `quote_flag_results` |
| `eval/cache/parsed/` | Parsed text per document, by SHA-256, so harness re-runs skip parsing |
| `eval/reports/` | Harness reports; `runs/<run_id>/` holds each scored extraction |

`data/state/`, `data/inbox/`, `eval/cache/` and `eval/reports/runs/` are git-ignored.

## What is sent to which service

| Sent | To | When |
|---|---|---|
| Parsed text of one quote (one segment of a document) + the extraction instruction | **Public OpenAI API**: `gpt-5.4-mini` (default) or `gpt-4.1` (frontier), via `llm/router.py` only | Every extraction (workflow, harness, `tools.where` on a PDF) |
| PDF bytes | Azure Document Intelligence **dev** resource (`prebuilt-layout`) | Never so far: `parse/azure_di.py` is written but no credentials exist. It refuses any file outside `data/golden/` and `data/synthetic/`. |
| Mailbox read requests (sign-in, folder delta, message download) | Microsoft Graph | Only when the Graph monitor is enabled |
| Nothing | Docling, OCR (RapidOCR), zen-engine, Postgres, Temporal | Run locally |

The app's pages load the IBM Plex fonts from Google Fonts (font files only, no document data).
The GoRules editor is bundled locally (`ui/static/`).

## What is logged

- **Router:** task, model, input and output token counts, latency. Never the prompt, the
  document text or the completion.
- **Temporal history:** document IDs, file paths, page ranges, counts, field names, fixed reason
  codes and flag codes. No document text, values or spans.
- **Exceptions** passed to Temporal or the app carry a fixed message and an error type only.
- **App:** request logging is switched off.

Stored data (Postgres, state files, harness runs) does contain document text, values and spans:
that is the evidence the system shows reviewers. It is local to the spike machine.

## Migration to the tenant

| Spike | Tenant |
|---|---|
| Local inbox folder | Microsoft Graph: a shared mailbox, mail-read permission scoped to it, change notifications or delta polling |
| Public OpenAI API | Azure AI Foundry deployment behind a **private endpoint** (the router already has an `azure_foundry` backend; only `llm/config.yaml` and the endpoint/key change) |
| `data/state/` on disk | Azure Storage container (private endpoint, managed identity, encryption at rest) |
| Local Postgres | Managed Postgres in the tenant |
| Supplier currency table in `rules/quote_flags.json` | `erp_staging` |
| Keys in `.env` | Managed identities / Key Vault |

Nobody uses this outside the spike until that move is done and grounding is 100% on the golden set.
