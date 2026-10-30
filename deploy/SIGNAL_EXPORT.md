# Export all recorded signals from VPS

This is a read-only export of events already captured, not a downloader of
missing JIFU or Telegram messages. It does not query Discord history or
execute trades.

The source stores continue accumulating records independently of exports:
- Sktrader SQLite scanner_events: captured JIFU XAUUSD signals with source,
  card fields, detection and delivery timestamps.
- DK Capital append-only telegram-events.jsonl: source Telegram message
  events and edits/deletions, including original text and metadata.
- DK Capital signal-state.json: current parsed signal records and available
  amendment history.

The GWS raw event archive includes ALL chats present in the existing DK
Capital Telegram event log, not only GWS. Filter by chat title or chat ID
when using the CSV. Not all original source posts are trading signals.

## One command on VPS

First install both merged repositories:

    cd /opt/dkcapital && git pull --ff-only origin main
    docker compose build discord-bot
    cd /opt/sktrader && git pull --ff-only origin main

Run a one-time export without restarting a running service:

    sudo bash /opt/sktrader/deploy/export-all-signals.sh

The script writes six files (three CSV and three standalone HTML) inside
a timestamped private directory under /opt/sktrader/exports and packages
them into a private .tar.gz archive. It prints an exact scp command to
download from your own computer, for example:

    scp root@YOUR_VPS:/opt/sktrader/exports/20261030T120000Z.tar.gz .

Do not share the archives publicly: original Telegram text, usernames,
group identities and other source data may be confidential. Only download
through SSH. This export reads the current event log and parsed snapshot
without taking a coordinated atomic checkpoint; if live updates occur
during the export, the two DK Capital files might represent slightly
different instants. Re-run the command when a synchronized snapshot is
needed.

JIFU's on-screen mixed feed is not an authoritative complete historical
source: only observed and durably recorded events can be exported.
