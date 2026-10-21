"""Start an EmailWorkflow for every new email, in near real time.

  uv run --env-file .env python email_monitor.py                    # local inbox folder (default)
  cp data/synthetic/emails/08-dollar-prices.eml data/inbox/         # "receive" an email

  uv run --env-file .env python email_monitor.py --source graph     # a Microsoft 365 mailbox via Graph

Graph mode fetches each new message in the configured folder as a .eml into the same inbox
folder, so dedupe and the workflow are identical for both sources. It is OFF unless
MAIL_MONITOR_ENABLED=true: CLAUDE.md does not allow reading a real mailbox until that is approved.
"""
import argparse, asyncio, hashlib, json, os, shutil, time
from datetime import datetime, timezone
from pathlib import Path
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporal_app.workflow import EmailWorkflow
from worker import TASK_QUEUE

ROOT = Path(__file__).resolve().parent
INBOX = Path(os.getenv('ALTITUDE_INBOX', ROOT / 'data/inbox'))
TEMPORAL_UI = os.getenv('TEMPORAL_UI', 'http://localhost:8233')

def _stable(path: Path, wait: float = 0.5) -> bool:
    """True once the file has stopped growing (a copy into the folder has finished)."""
    size = path.stat().st_size
    time.sleep(wait)
    return path.exists() and path.stat().st_size == size and size > 0

async def poll_once(client, inbox: Path, extractor: str) -> list[str]:
    """Start a workflow for every new .eml in inbox; return the workflow IDs started."""
    processed = inbox / 'processed'
    processed.mkdir(parents=True, exist_ok=True)
    started = []
    for eml in sorted(inbox.glob('*.eml')):
        if not _stable(eml):
            continue
        digest = hashlib.sha256(eml.read_bytes()).hexdigest()[:16]
        # Move out of the inbox first, so the file is picked up once; the workflow reads it from here.
        target = processed / f'{time.strftime("%Y%m%d-%H%M%S")}-{eml.name}'
        shutil.move(eml, target)
        workflow_id = f'email-{digest}'
        try:
            await client.start_workflow(EmailWorkflow.run, args=[str(target), extractor], id=workflow_id, task_queue=TASK_QUEUE)
        except WorkflowAlreadyStartedError:
            print(f'Skipped {eml.name}: this exact email is already being processed ({workflow_id})')
            continue
        print(f'New email {eml.name} -> workflow {workflow_id}')
        print(f'  Temporal UI: {TEMPORAL_UI}/namespaces/default/workflows/{workflow_id}')
        started.append(workflow_id)
    return started

class GraphFetcher:
    """Copies new messages from the Graph mailbox folder into the inbox folder as .eml files.

    Remembers its delta link and the message IDs it has fetched (data/state/graph_mail.json), so a
    restart neither misses mail nor fetches it twice. On the very first start it only takes mail
    received from then on, unless backfill=True.
    """
    def __init__(self, mailbox, inbox: Path, state_file: Path, backfill: bool = False):
        self.mailbox, self.inbox, self.state_file = mailbox, inbox, state_file
        self.state = json.loads(state_file.read_text()) if state_file.exists() else {}
        if not self.state:
            self.state = {'started_at': '1970-01-01T00:00:00Z' if backfill else datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                          'delta_link': None, 'seen': []}

    def fetch(self) -> dict:
        """One poll. Returns counts only; no subjects, senders or content are printed or logged."""
        from ingest.graph_mail import sender_address
        messages, delta_link = self.mailbox.changes(self.state['delta_link'])
        seen = set(self.state['seen'])
        counts = {'new': 0, 'skipped_sender': 0, 'skipped_old': 0}
        for message in messages:
            if message['id'] in seen:
                continue
            seen.add(message['id'])
            if message.get('receivedDateTime', '') < self.state['started_at']:
                counts['skipped_old'] += 1
                continue
            if not self.mailbox.config.sender_allowed(sender_address(message)):
                counts['skipped_sender'] += 1
                continue
            raw = self.mailbox.mime(message['id'])
            name = f'graph-{hashlib.sha256(raw).hexdigest()[:16]}.eml'
            partial = self.inbox / (name + '.part')
            partial.write_bytes(raw)
            partial.replace(self.inbox / name)  # appears in the inbox only once complete
            counts['new'] += 1
        self.state['delta_link'] = delta_link
        self.state['seen'] = list(seen)[-5000:]
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state))
        return counts

def graph_fetcher(inbox: Path, backfill: bool) -> 'GraphFetcher':
    from ingest.graph_mail import GraphConfig, GraphMailbox
    if os.getenv('MAIL_MONITOR_ENABLED', '').strip().lower() != 'true':
        raise SystemExit('The Graph mail monitor is off. CLAUDE.md does not allow reading a real mailbox until that is '
                         'approved; once it is, set MAIL_MONITOR_ENABLED=true in .env (test mailbox, synthetic documents only).')
    config = GraphConfig.from_env()
    if config is None:
        raise SystemExit('Set GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET and GRAPH_MAILBOX in .env (see docs/vdi-setup.md).')
    state_dir = Path(os.getenv('ALTITUDE_STATE_DIR', ROOT / 'data/state'))
    return GraphFetcher(GraphMailbox(config), inbox, state_dir / 'graph_mail.json', backfill)

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', choices=['folder', 'graph'], default='folder')
    ap.add_argument('--inbox', type=Path, default=INBOX)
    ap.add_argument('--extractor', default='current')
    ap.add_argument('--every', type=float, default=None, help='seconds between checks (folder 2, graph 15)')
    ap.add_argument('--backfill', action='store_true', help='graph: on first start, also take mail already in the folder')
    a = ap.parse_args()
    a.inbox.mkdir(parents=True, exist_ok=True)
    fetcher = graph_fetcher(a.inbox, a.backfill) if a.source == 'graph' else None
    every = a.every or (15.0 if fetcher else 2.0)
    client = await Client.connect(os.getenv('TEMPORAL_ADDRESS', 'localhost:7233'))
    if fetcher:
        print(f'Watching mailbox folder "{fetcher.mailbox.config.folder}" via Microsoft Graph (every {every:g}s). Ctrl+C to stop.')
    else:
        print(f'Watching {a.inbox} for new .eml files (every {every:g}s). Ctrl+C to stop.')
    while True:
        if fetcher:
            from ingest.graph_mail import GraphError
            try:
                counts = await asyncio.to_thread(fetcher.fetch)
                if any(counts.values()):
                    print(f"Graph: {counts['new']} new, {counts['skipped_sender']} skipped (sender not allowed), "
                          f"{counts['skipped_old']} older than the monitor")
            except GraphError as e:
                print(f'Graph: {e}; retrying in {every:g}s')
        await poll_once(client, a.inbox, a.extractor)
        await asyncio.sleep(every)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
