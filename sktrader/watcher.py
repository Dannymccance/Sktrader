"""JIFU website watcher. Collects alerts and posts to Discord; NEVER places orders."""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import stat
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sktrader.core import Alert, AlertError, EventStore, SnapshotError, auroux_xauusd_card, merge_history_page, parse_card

from sktrader.multi_scanners import (
    SCANNER_CHANNELS, ScannerStore, discord_card, is_xauusd_card,
    load_channels, setup_channels,
)

LOG = logging.getLogger("sktrader")
DEFAULT_URL = "https://charts.jifulive.com/jifu/dashboard"

# Geometry is configurable so Sktrader can run on a dedicated 1080p VNC display.
# Defaults remain safe for the original 1280x800 MT5 desktop.
def browser_geometry() -> tuple[dict[str, int], list[str]]:
    width = int(os.getenv("SKTRADER_VIEWPORT_WIDTH", "1160"))
    height = int(os.getenv("SKTRADER_VIEWPORT_HEIGHT", "640"))
    if not 400 <= width <= 3840 or not 300 <= height <= 2160:
        raise ValueError("Browser viewport dimensions outside supported range")
    return (
        {"width": width, "height": height},
        [f"--window-size={width + 80},{height + 80}", "--window-position=15,15"],
    )

# Read only the scanner selector in the Alerts sidebar header, not the
# separate chart-scanner selector at the top left or the AUROUX alert cards.
# This returns a status only and never clicks or changes the user's UI.
SIDEBAR_SCANNER_STATE_JS = r"""() => {
  const tree = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let foundAuroux = false;
  let foundUnselected = false;
  let node;
  while ((node = tree.nextNode())) {
    const t = (node.nodeValue || '').trim().replace(/\s+/g, ' ');
    if (!/^(?:AUROUX(?:\s+SCANNER)?|Select Scanner(?:\s+Choose an option)?)$/i.test(t)) continue;
    const r = node.parentElement?.getBoundingClientRect();
    if (!r || r.width <= 0 || r.height <= 0 ||
        r.left < window.innerWidth * 0.60 ||
        r.left >= window.innerWidth ||
        r.top < window.innerHeight * 0.09 ||
        r.top > window.innerHeight * 0.35) continue;
    if (/^Select Scanner/i.test(t)) foundUnselected = true;
    else foundAuroux = true;
  }
  // Select Scanner takes priority if a stale Auroux label is also present.
  return foundUnselected ? 'unselected' : (foundAuroux ? 'auroux' : 'unknown');
}"""


def _sidebar_scanner_state(page) -> str:
    """Return AUROUX, unselected or unknown, never infer from alert content."""
    status = page.evaluate(SIDEBAR_SCANNER_STATE_JS)
    if status not in {"auroux", "unselected", "unknown"}:
        raise SnapshotError("Unexpected scanner selector state")
    return status


# The JIFU dashboard has a large chart and a narrow Alerts sidebar. Chart
# annotations can themselves contain "Point of Interest", so do not treat
# every matching document text node as an alert. Only inspect the right sidebar.
# The threshold is generous enough for the JIFU responsive split layout.
SIDEBAR_THRESHOLD = 0.60
EXTRACT_CARDS_JS = r"""() => {
  const isSidebar = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 &&
      r.left >= window.innerWidth * 0.60 &&
      r.left < window.innerWidth && r.right <= window.innerWidth + 25;
  };
  const tree = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const cards = [];
  const seen = new Set();
  let node;
  while ((node = tree.nextNode())) {
    if ((node.nodeValue || '').trim().toLowerCase() !== 'point of interest') continue;
    let el = node.parentElement;
    if (!el || !isSidebar(el)) continue;
    for (let depth = 0; depth < 22 && el; depth++, el = el.parentElement) {
      const text = (el.innerText || '').trim();
      if (text.length > 4500) break;
      const fields = (text.match(/point of interest/gi) || []).length;
      if (fields > 1) break;
      if (fields !== 1) continue;
      // This is the MIXED JIFU Alerts feed, not an Auroux-only feed.
      // Other scanners may have fewer exits or no invalidation.
      if (/\b(?:1M|5M|15M|30M|1H|4H)\s*-\s*(?:BUY|SELL)\b/i.test(text) &&
          /Point of Interest\s*[0-9]/i.test(text)) {
        if (!seen.has(el)) {
          cards.push(text);
          seen.add(el);
        }
        break;
      }
    }
  }
  return cards;
}"""

# Used ONLY by manual replay-visible. Scroll the actual Alerts sidebar; never
# scroll the chart/page or press any trading buttons. The sidebar can render
# only ten most recent cards until its inner scroll container advances.
SCROLL_ALERTS_JS = r"""(operation) => {
  const tree = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let label;
  while ((label = tree.nextNode())) {
    if ((label.nodeValue || '').trim().toLowerCase() === 'point of interest') {
      const rect = label.parentElement?.getBoundingClientRect();
      if (!rect || rect.left < window.innerWidth * 0.60 ||
          rect.left >= window.innerWidth) continue;
      let el = label.parentElement;
      for (let depth = 0; depth < 18 && el; depth++, el = el.parentElement) {
        const r = el.getBoundingClientRect();
        const overflow = getComputedStyle(el).overflowY;
        if (r.left < window.innerWidth * 0.60) break;
        if (el.scrollHeight > el.clientHeight + 30 && /auto|scroll/.test(overflow)) {
          const before = el.scrollTop;
          if (operation === 'top') {
            el.scrollTop = 0;
          } else {
            // Move under one viewport to preserve consecutive-card overlap
            // in virtualised lists. Do not jump directly to the bottom.
            el.scrollTop = Math.min(
              el.scrollHeight - el.clientHeight,
              el.scrollTop + Math.max(90, Math.floor(el.clientHeight * 0.45))
            );
          }
          return {
            found: true, moved: Math.abs(el.scrollTop - before) > 1,
            bottom: el.scrollTop >= el.scrollHeight - el.clientHeight - 3
          };
        }
      }
    }
  }
  return { found: false, moved: false, bottom: true };
}"""


# Counts and booleans only, no signal prices, HTML, cookies, credentials or URLs.
# The diagnostic distinguishes chart annotations from sidebar alert cards.
DIAG_JS = r"""() => {
  const isSidebar = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 &&
      r.left >= window.innerWidth * 0.60 &&
      r.left < window.innerWidth && r.right <= window.innerWidth + 25;
  };
  const body = document.body?.innerText || '';
  const tree = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let allPoi = 0;
  let sidebarPoi = 0;
  const coverage = {
    auroux: 0, xauusd: 0, direction: 0, invalidation: 0,
    exit1: 0, exit2: 0, level: 0
  };
  let node;
  while ((node = tree.nextNode())) {
    if ((node.nodeValue || '').trim().toLowerCase() !== 'point of interest'
        || !node.parentElement?.getClientRects().length) continue;
    allPoi++;
    if (!isSidebar(node.parentElement)) continue;
    sidebarPoi++;
    const found = Object.fromEntries(Object.keys(coverage).map(k => [k, false]));
    let el = node.parentElement;
    for (let depth = 0; depth < 22 && el; depth++, el = el.parentElement) {
      const text = (el.innerText || '').trim();
      if (text.length > 4500 || (text.match(/point of interest/gi) || []).length > 1) break;
      found.auroux ||= /\bAUROUX\b/i.test(text);
      found.xauusd ||= /\bXAUUSD\b/i.test(text);
      found.direction ||= /\b(?:1M|5M|15M|30M|1H|4H)\s*-\s*(?:BUY|SELL)\b/i.test(text);
      found.invalidation ||= /Invalidation/i.test(text);
      found.exit1 ||= /Exit\s*1/i.test(text);
      found.exit2 ||= /Exit\s*2/i.test(text);
      found.level ||= /\bLevel\b/i.test(text);
    }
    for (const k of Object.keys(coverage)) if (found[k]) coverage[k]++;
  }
  return {
    document_poi_labels: allPoi,
    excluded_chart_or_other_labels: allPoi - sidebarPoi,
    poi_labels: sidebarPoi,
    field_coverage: coverage,
    path: location.pathname,
    ready_state: document.readyState,
    body_characters: body.length,
    has_auroux: /\bAUROUX\b/i.test(body),
    has_xauusd: /\bXAUUSD\b/i.test(body),
    has_invalidation: /Invalidation/i.test(body),
    has_exit1: /Exit\s*1/i.test(body),
    has_exit2: /Exit\s*2/i.test(body),
    has_alerts_tab: /\bAlerts\b/i.test(body),
    possible_login: /Log\s*in|Sign\s*in/i.test(body) && /Password/i.test(body)
  };
}"""


# One-off, manually invoked diagnostic for reviewing alert text boundaries.
# Returns only small excerpts from single-card DOM nodes inside the Alerts
# sidebar. Not used by the continuously running watcher.
PROBE_CARDS_JS = r"""() => {
  const rightSide = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 &&
      r.left >= window.innerWidth * 0.60 &&
      r.left < window.innerWidth && r.right <= window.innerWidth + 25;
  };
  const tree = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const out = [];
  let node;
  while ((node = tree.nextNode()) && out.length < 15) {
    if ((node.nodeValue || '').trim().toLowerCase() !== 'point of interest') continue;
    if (!node.parentElement || !rightSide(node.parentElement)) continue;
    let selected = null;
    let el = node.parentElement;
    for (let depth = 0; depth < 22 && el; depth++, el = el.parentElement) {
      const content = (el.innerText || '').trim();
      const matches = (content.match(/point of interest/gi) || []).length;
      if (matches > 1 || content.length > 4500) break;
      if (matches !== 1) continue;
      const fields = {
        auroux: /\bAUROUX\b/i.test(content),
        xauusd: /\bXAUUSD\b/i.test(content),
        direction: /\b(?:1M|5M|15M|30M|1H|4H)\s*-\s*(?:BUY|SELL)\b/i.test(content),
        invalidation: /Invalidation/i.test(content),
        exit1: /Exit\s*1/i.test(content),
        exit2: /Exit\s*2/i.test(content),
        level: /\bLevel\b/i.test(content)
      };
      // Last single-POI ancestor is the largest available independent card.
      // Do not report full HTML, attributes, page title, URLs, cookies or storage.
      let excerpt = content.replace(/\s+/g, ' ').slice(0, 650);
      excerpt = excerpt.replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, '[email]');
      excerpt = excerpt.replace(/\b(?:Bearer|Bot)\s+[A-Za-z0-9._-]{15,}\b/gi, '[token]');
      selected = {
        depth,
        characters: content.length,
        fields,
        excerpt
      };
    }
    out.push(selected || {error: 'No single-POI ancestor found'});
  }
  return out;
}"""

@dataclass(frozen=True)
class Config:
    url: str
    profile: Path
    database: Path
    bot_token_file: Path
    discord_channel_id: int
    poll_seconds: float
    headless: bool

    @classmethod
    def from_env(cls, *, require_discord: bool) -> "Config":
        channel = os.getenv("SKTRADER_DISCORD_CHANNEL_ID", "").strip()
        token_file = os.getenv("SKTRADER_DISCORD_BOT_TOKEN_FILE", "").strip()
        if require_discord and (not channel.isascii() or not channel.isdigit() or not 15 <= len(channel) <= 22):
            raise ValueError("SKTRADER_DISCORD_CHANNEL_ID must be a Discord channel snowflake")
        if require_discord and not token_file:
            raise ValueError("SKTRADER_DISCORD_BOT_TOKEN_FILE is required for watch mode")
        poll = float(os.getenv("SKTRADER_POLL_SECONDS", "5"))
        if not 1 <= poll <= 60:
            raise ValueError("Poll interval must be 1-60 seconds")
        return cls(
            url=os.getenv("SKTRADER_JIFU_URL", DEFAULT_URL),
            profile=Path(os.getenv("SKTRADER_BROWSER_PROFILE", "~/.local/share/sktrader/jifu-profile")).expanduser(),
            database=Path(os.getenv("SKTRADER_DATABASE", "~/.local/share/sktrader/signals.db")).expanduser(),
            bot_token_file=Path(token_file).expanduser() if token_file else Path("/nonexistent/sktrader-discord-token"),
            discord_channel_id=int(channel) if channel.isascii() and channel.isdigit() else 0,
            poll_seconds=poll,
            headless=os.getenv("SKTRADER_HEADLESS", "true").strip().lower() in {"true", "1", "yes"},
        )


def _secure_profile(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(stat.S_IRWXU)


@contextmanager
def _one_browser_at_a_time(profile: Path):
    """Prevent login, watch, diagnose and replay sharing one Chromium profile."""
    profile.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = profile.parent / ".sktrader-browser.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                "Another Sktrader browser process is active. Stop the watcher "
                "with Ctrl+C before running replay-visible or diagnose."
            ) from exc
        yield
    finally:
        os.close(fd)


def _prepare_page(page) -> None:
    """Preserve the unfiltered right-hand JIFU Alerts > Select Scanner > All.

    Do not click the unrelated chart scanner, tabs or All buttons; JIFU can
    change the feed when its React controls are manipulated or reloaded.
    """
    page.wait_for_timeout(1250)


def _read_feed_cards(page) -> list[str]:
    """Read complete single-card DOM segments across all JIFU scanners."""
    raw_cards: list[str] = page.evaluate(EXTRACT_CARDS_JS)
    status = page.evaluate(DIAG_JS)
    if not raw_cards or len(raw_cards) != status["poi_labels"]:
        # A partial feed cannot be used for overlap/dedup or baselining.
        raise SnapshotError(
            f"Incomplete mixed JIFU feed: {len(raw_cards)} cards versus "
            f"{status['poi_labels']} visible labels; browser diagnostic: "
            + json.dumps(status, sort_keys=True)
        )
    return raw_cards


def _read_alerts(page) -> list[Alert]:
    """Return only AUROUX XAUUSD from a complete mixed-scanner feed."""
    raw_cards = _read_feed_cards(page)
    alerts: list[Alert] = []
    for text in raw_cards:
        if not auroux_xauusd_card(text):
            continue
        try:
            alerts.append(parse_card(text))
        except AlertError as exc:
            raise SnapshotError(f"Unparseable AUROUX XAUUSD card: {exc}") from exc
    return alerts


def _bot_token(path: Path) -> str:
    """Read the existing DK Capital bot's token from a private local file.

    Do not log or copy the value into .env/GitHub. The file must be owned
    by the running collector user and inaccessible to group and others.
    """
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or (info.st_mode & 0o077):
        raise PermissionError("Discord bot token file must be owned by this user with mode 0600 or 0400")
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 20 or any(c.isspace() for c in token):
        raise ValueError("Discord bot token file is empty or invalid")
    return token


def _post_discord(config: Config, payload: dict, *, event_id: int | None = None,
                  channel_id: int | None = None) -> None:
    if event_id is not None:
        # Discord deduplicates the bot's identical nonces for a few minutes.
        # SQLite's durable queue remains the authoritative long-term record.
        prefix = "sktrader-scanners" if channel_id is not None else "sktrader"
        payload = {**payload, "nonce": f"{prefix}-{event_id}", "enforce_nonce": True}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel_id or config.discord_channel_id}/messages",
        data=body,
        headers={
            "Authorization": "Bot " + _bot_token(config.bot_token_file),
            "Content-Type": "application/json",
            "User-Agent": "sktrader-collector/0.1",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        if response.status != 200:
            raise RuntimeError(f"Discord returned HTTP {response.status}")


def _alert_embed(event_id: int, observed_at: str, alert: Alert, *, historical: bool = False) -> dict:
    """Mirror the JIFU Auroux card without commentary, metrics or extra notices.

    event_id/observed_at/historical are retained for outbox compatibility but
    deliberately not shown in Discord: the user requested only the signal.
    The visible JIFU age is kept as part of the source card, not a send-time.
    """
    return {
        "embeds": [{
            "title": "AUROUX",
            "description": f"{alert.relative_age}\n\n**{alert.symbol}**\n**{alert.timeframe} - {alert.side}**",
            "color": 0x22C55E if alert.side == "BUY" else 0xEF4444,
            "fields": [
                {"name": "Point of Interest", "value": alert.poi, "inline": False},
                {"name": "Invalidation", "value": alert.invalidation, "inline": False},
                {"name": "Exit1", "value": alert.tp1, "inline": False},
                {"name": "Exit2", "value": alert.tp2, "inline": False},
                {"name": "Level", "value": f"{alert.level} pips", "inline": False},
            ],
        }],
        "allowed_mentions": {"parse": []},
    }


def _scanner_channel_map(config: Config) -> dict[str, int]:
    """Require explicit mapping for all eight scanners before monitoring."""
    return load_channels(config.profile.parent / "scanner-channels.json",
                         config.discord_channel_id)


def _send_scanner_pending(store: ScannerStore, config: Config, channels: dict[str, int]) -> None:
    for event_id, signal in store.pending():
        # Never route a non-XAUUSD signal, even if an unexpected DB row exists.
        if signal.symbol != "XAUUSD" or signal.scanner not in channels:
            raise SnapshotError("Non-XAUUSD or unmapped scanner signal in outbox")
        _post_discord(config, discord_card(signal), event_id=event_id,
                      channel_id=channels[signal.scanner])
        store.delivered(event_id)
        LOG.info("Delivered scanner signal #%s from %s to mapped Discord channel",
                 event_id, signal.scanner)


def _send_pending(store: EventStore, config: Config) -> None:
    for event_id, observed_at, alert in store.pending():
        # Every valid AUROUX XAUUSD card is delivered. No timeframe or
        # range filters. Outbox already excludes all other scanners.
        _post_discord(
            config,
            _alert_embed(
                event_id, observed_at, alert,
                historical=store.is_visible_replay_event(event_id),
            ),
            event_id=event_id,
        )
        store.delivered(event_id)
        LOG.info("Delivered Auroux signal #%s: %s %s %s", event_id, alert.side, alert.timeframe, alert.poi)


def _notify_health(config: Config, message: str) -> None:
    """Diagnostics stay in local logs: the target Discord channel is signals only."""
    LOG.error("JIFU monitor health: %s", message[:1300])


def login(config: Config) -> None:
    """One-time interactive login via the existing VNC desktop."""
    from playwright.sync_api import sync_playwright

    _secure_profile(config.profile)
    with sync_playwright() as playwright:
        viewport, args = browser_geometry()
        context = playwright.chromium.launch_persistent_context(
            str(config.profile), headless=False, viewport=viewport, args=args,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(config.url, wait_until="domcontentloaded", timeout=45000)
            print("Log into JIFU, open Alerts, leave right-hand scanner at Select Scanner and timeframe at All.")
            input("Once the alerts are visible, press Enter to save the browser session and close: ")
        finally:
            context.close()


def diagnose(config: Config) -> None:
    """One-off DOM diagnostic without revealing any website content or credentials."""
    from playwright.sync_api import sync_playwright

    _secure_profile(config.profile)
    with sync_playwright() as playwright:
        viewport, args = browser_geometry()
        context = playwright.chromium.launch_persistent_context(
            str(config.profile), headless=config.headless,
            viewport=viewport, args=args,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(config.url, wait_until="domcontentloaded", timeout=45000)
            _prepare_page(page)
            # Give JIFU's client-side app a chance to populate asynchronously.
            page.wait_for_timeout(5000)
            status = page.evaluate(DIAG_JS)
            print("Safe JIFU diagnostic:", json.dumps(status, sort_keys=True))
            card_probe = page.evaluate(PROBE_CARDS_JS)
            print("JIFU single-card structure samples:", json.dumps(card_probe, ensure_ascii=False))
            try:
                alerts = _read_alerts(page)
            except SnapshotError as exc:
                print("Feed capture: NOT READY.", exc)
            else:
                visible = page.evaluate(EXTRACT_CARDS_JS)
                gold_count = sum(is_xauusd_card(card) for card in visible)
                print(f"Feed capture: READY; {status['poi_labels']} mixed-scanner cards; "
                      f"{gold_count} XAUUSD cards across all scanners; "
                      f"{len(alerts)} from AUROUX")
        finally:
            context.close()


def _collect_replay_history(page, *, max_scrolls: int = 60) -> tuple[list[str], list[str], int, bool]:
    """Collect bounded older sidebar pages with strict overlap verification.

    Return (historical cards, newest feed cards, number of scrolls, cap hit).
    Any missing overlap aborts before the one-time replay is consumed.
    """
    if not 1 <= max_scrolls <= 200:
        raise ValueError("SKTRADER_REPLAY_MAX_SCROLLS must be between 1 and 200")
    scroll_info = page.evaluate(SCROLL_ALERTS_JS, "top")
    page.wait_for_timeout(350)
    latest = _read_feed_cards(page)
    archive = list(latest)
    if not scroll_info["found"]:
        LOG.warning("No inner JIFU sidebar scrollbar identified; replaying latest cards only")
        return archive, latest, 0, False

    last_moved = False
    for step in range(max_scrolls):
        moved = page.evaluate(SCROLL_ALERTS_JS, "down")
        if not moved["found"]:
            raise SnapshotError("Lost JIFU sidebar scroll container during replay")
        if not moved["moved"]:
            # Give asynchronous pagination an opportunity to add older cards.
            page.wait_for_timeout(900)
            moved = page.evaluate(SCROLL_ALERTS_JS, "down")
            if not moved["moved"]:
                return archive, latest, step, False
        last_moved = True
        page.wait_for_timeout(700)
        older_page = _read_feed_cards(page)
        archive = merge_history_page(archive, older_page)
        if (step + 1) % 10 == 0:
            LOG.info("History scan: %s scrolls, %s unique ordered card positions",
                     step + 1, len(archive))
    return archive, latest, max_scrolls, last_moved


def replay_visible(config: Config) -> None:
    """Manually queue one historical batch; never run under the live watcher."""
    from playwright.sync_api import sync_playwright

    _secure_profile(config.profile)
    _bot_token(config.bot_token_file)
    store = ScannerStore(config.database)
    channels = _scanner_channel_map(config)
    with sync_playwright() as playwright:
        viewport, args = browser_geometry()
        context = playwright.chromium.launch_persistent_context(
            str(config.profile), headless=config.headless, viewport=viewport, args=args,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(config.url, wait_until="domcontentloaded", timeout=45000)
            _prepare_page(page)
            page.wait_for_timeout(5000)
            max_scrolls = int(os.getenv("SKTRADER_REPLAY_MAX_SCROLLS", "60"))
            cards, latest, scrolls, capped = _collect_replay_history(
                page, max_scrolls=max_scrolls,
            )
            LOG.info("Historical sidebar scan: %s cards across %s scrolls%s",
                     len(cards), scrolls, " (configured limit reached)" if capped else "")
            # If the history exceeds the scroll cap, avoid using up the
            # one-time batch on a potentially truncated archive.
            if capped:
                raise SnapshotError(
                    f"Historical scan reached SKTRADER_REPLAY_MAX_SCROLLS={max_scrolls} "
                    "before reaching the end. Increase it and rerun; "
                    "no historical replay batch has been queued."
                )
            count, already_run = store.replay_once(cards, newest=latest)
            if already_run:
                print("Historical XAUUSD scanner replay already queued; no duplicate batch created.")
            elif count == 0:
                print(
                    f"Found {len(cards)} JIFU cards, but zero XAUUSD cards. "
                    "No historical signals sent; replay remains available."
                )
            else:
                print(
                    f"Queued {count} historical XAUUSD scanner cards to their "
                    "individual Discord channels."
                )
            _send_scanner_pending(store, config, channels)
            print("Queued XAUUSD scanner messages processed.")
        finally:
            context.close()
            store.conn.close()


def watch(config: Config) -> None:
    """Monitor all mixed JIFU Alerts; restore lost sessions without resetting dedupe."""
    from playwright.sync_api import sync_playwright
    from sktrader.recovery import HealthMonitor, login_state, try_login

    _secure_profile(config.profile)
    _bot_token(config.bot_token_file)
    channels = _scanner_channel_map(config)
    store = ScannerStore(config.database)
    health = HealthMonitor(config)
    login_file_raw = os.getenv("SKTRADER_JIFU_CREDENTIALS_FILE", "").strip()
    login_file = Path(login_file_raw) if login_file_raw else None
    LOG.info("Starting XAUUSD-only scanner monitor for %s sources; WATCH ONLY", len(channels))
    with sync_playwright() as playwright:
        viewport, args = browser_geometry()
        context = playwright.chromium.launch_persistent_context(
            str(config.profile), headless=config.headless, viewport=viewport, args=args,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(config.url, wait_until="domcontentloaded", timeout=45000)
            _prepare_page(page)
            failures = 0
            last_selector = None
            last_reload = 0.0
            last_login = 0.0
            while True:
                started = time.monotonic()
                try:
                    diagnostic = page.evaluate(DIAG_JS)
                    state = login_state(diagnostic)
                    if state == "login":
                        if login_file and started - last_login >= 120:
                            last_login = started
                            LOG.warning("JIFU session expired; attempting guarded credential reauthentication")
                            if try_login(page, login_file):
                                _prepare_page(page)
                            else:
                                raise SnapshotError("JIFU authentication requires interactive verification")
                        else:
                            raise SnapshotError("JIFU logged out; credential file absent or login retry cooling down")
                    selector = _sidebar_scanner_state(page)
                    if selector != last_selector:
                        if selector == "unselected":
                            LOG.info("JIFU Alerts sidebar unfiltered: monitoring all scanners, XAUUSD only")
                        else:
                            LOG.warning("Alerts scanner dropdown=%s; monitoring paused until full feed is restored", selector)
                        last_selector = selector
                    if selector != "unselected":
                        raise SnapshotError("Mixed-scanner Alerts feed not selected")
                    cards = _read_feed_cards(page)
                    new_count, baseline = store.observe(cards)
                    if baseline:
                        LOG.info("Mixed feed baseline: %s existing cards, %s XAUUSD cards; history not replayed",
                                 len(cards), sum(is_xauusd_card(card) for card in cards))
                    elif new_count:
                        LOG.info("Queued %s new XAUUSD signals for source-specific channels", new_count)
                    _send_scanner_pending(store, config, channels)
                    failures = 0
                    health.healthy()
                except Exception as exc:
                    failures += 1
                    reason = f"{type(exc).__name__}: {str(exc)[:190]}"
                    health.degraded(reason)
                    if failures == 1 or failures % 12 == 0:
                        LOG.warning("Monitor paused (%s failures): %s", failures, reason)
                    # A transient blank React view often recovers itself. After
                    # 12 consecutive failed reads, reload at most once per 2
                    # minutes; preserve the SQLite snapshot for overlap checks.
                    # Do not overwrite a valid snapshot with an empty page.
                    if failures >= 12 and started - last_reload >= 120:
                        last_reload = started
                        try:
                            LOG.warning("Reloading stalled JIFU dashboard after sustained feed failures")
                            page.goto(config.url, wait_until="domcontentloaded", timeout=45000)
                            _prepare_page(page)
                            last_selector = None
                        except Exception as reload_exc:
                            LOG.warning("JIFU recovery reload failed: %s", type(reload_exc).__name__)
                time.sleep(max(0.2, config.poll_seconds - (time.monotonic() - started)))
        finally:
            context.close()
            store.conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sktrader JIFU collector (watch-only)")
    parser.add_argument("command", choices=["login", "diagnose", "watch", "replay-visible", "setup-channels"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = Config.from_env(require_discord=args.command in {"watch", "replay-visible", "setup-channels"})
    with _one_browser_at_a_time(config.profile):
        if args.command == "setup-channels":
            channels = setup_channels(
                _bot_token(config.bot_token_file),
                config.discord_channel_id,
                config.profile.parent / "scanner-channels.json",
            )
            print(f"Mapped {len(channels)} EXISTING Discord signal channels. "
                  "No channels created or deleted; no alerts posted.")
            for scanner, channel_id in channels.items():
                print(f"  {scanner}: {channel_id}")
        elif args.command == "login":
            login(config)
        elif args.command == "diagnose":
            diagnose(config)
        elif args.command == "replay-visible":
            replay_visible(config)
        else:
            watch(config)
