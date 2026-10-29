"""JIFU website watcher. Collects alerts and posts to Discord; NEVER places orders."""
from __future__ import annotations

import argparse
import json
import logging
import os
import stat
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from sktrader.core import Alert, AlertError, EventStore, SnapshotError, parse_card

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
      if (/\bAUROUX\b/i.test(text) && /\bXAUUSD\b/i.test(text) &&
          /\b(?:1M|5M|15M|30M|1H|4H)\s*-\s*(?:BUY|SELL)\b/i.test(text) &&
          /Invalidation/i.test(text) && /Exit\s*1/i.test(text) &&
          /Exit\s*2/i.test(text) && /\bLevel\b/i.test(text)) {
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
    timeframe: str
    levels: frozenset[int]
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
        timeframe = os.getenv("SKTRADER_TIMEFRAME", "5M").strip().upper()
        if timeframe not in {"ALL", "1M", "5M", "15M", "30M", "1H", "4H"}:
            raise ValueError("Unsupported timeframe")
        levels = frozenset(int(p.strip()) for p in os.getenv("SKTRADER_LEVELS", "250,500").split(","))
        if not levels or any(level <= 0 for level in levels):
            raise ValueError("Invalid scanner levels")
        return cls(
            url=os.getenv("SKTRADER_JIFU_URL", DEFAULT_URL),
            profile=Path(os.getenv("SKTRADER_BROWSER_PROFILE", "~/.local/share/sktrader/jifu-profile")).expanduser(),
            database=Path(os.getenv("SKTRADER_DATABASE", "~/.local/share/sktrader/signals.db")).expanduser(),
            bot_token_file=Path(token_file).expanduser() if token_file else Path("/nonexistent/sktrader-discord-token"),
            discord_channel_id=int(channel) if channel.isascii() and channel.isdigit() else 0,
            poll_seconds=poll,
            timeframe=timeframe,
            levels=levels,
            headless=os.getenv("SKTRADER_HEADLESS", "true").strip().lower() in {"true", "1", "yes"},
        )


def _secure_profile(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(stat.S_IRWXU)


def _prepare_page(page) -> None:
    """Best-effort select the alerts panel; never click a trade action."""
    try:
        page.get_by_text("Alerts", exact=True).first.click(timeout=4000)
    except Exception:
        LOG.info("Could not select Alerts tab automatically; inspecting current panel")
    # The UI's All filter avoids missing alerts on another timeframe and
    # preserves the alert ordering for snapshot reconciliation.
    try:
        page.get_by_text("All", exact=True).last.click(timeout=2500)
    except Exception:
        LOG.info("Could not select All filter; ensure the panel is set to All manually")


def _read_alerts(page) -> list[Alert]:
    raw_cards: list[str] = page.evaluate(EXTRACT_CARDS_JS)
    status = page.evaluate(DIAG_JS)
    if not raw_cards or len(raw_cards) != status["poi_labels"]:
        # Never establish a baseline or advance the durable event cursor
        # from a partial visible alert list; that can hide future signals.
        raise SnapshotError(
            f"Incomplete Auroux scan: {len(raw_cards)} complete cards versus "
            f"{status['poi_labels']} visible labels; browser diagnostic: "
            + json.dumps(status, sort_keys=True)
        )
    alerts: list[Alert] = []
    for text in raw_cards:
        try:
            alerts.append(parse_card(text))
        except AlertError as exc:
            # Do not update the snapshot if even one visible card is malformed.
            raise SnapshotError(f"Unparseable alert card: {exc}") from exc
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


def _post_discord(config: Config, payload: dict, *, event_id: int | None = None) -> None:
    if event_id is not None:
        # Discord deduplicates the bot's identical nonces for a few minutes.
        # SQLite's durable queue remains the authoritative long-term record.
        payload = {**payload, "nonce": f"sktrader-{event_id}", "enforce_nonce": True}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{config.discord_channel_id}/messages",
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


def _alert_embed(event_id: int, observed_at: str, alert: Alert) -> dict:
    return {
        "embeds": [{
            "title": f"AUROUX | {alert.symbol} | {alert.timeframe} {alert.side}",
            "color": 0x22C55E if alert.side == "BUY" else 0xEF4444,
            "fields": [
                {"name": "Point of Interest", "value": alert.poi, "inline": True},
                {"name": "Invalidation", "value": alert.invalidation, "inline": True},
                {"name": "TP1", "value": alert.tp1, "inline": True},
                {"name": "TP2", "value": alert.tp2, "inline": True},
                {"name": "Scanner level", "value": f"{alert.level} pips", "inline": True},
                {"name": "JIFU display age", "value": alert.relative_age, "inline": True},
            ],
            "footer": {"text": f"Collector event #{event_id} | observed {observed_at} | ALERT ONLY - NO TRADE"},
        }],
        "allowed_mentions": {"parse": []},
    }


def _send_pending(store: EventStore, config: Config) -> None:
    for event_id, observed_at, alert in store.pending():
        # Record ALL alerts, but notify Discord only for configured strategies.
        if ((config.timeframe == "ALL" or alert.timeframe == config.timeframe)
                and alert.level in config.levels):
            _post_discord(config, _alert_embed(event_id, observed_at, alert), event_id=event_id)
        store.delivered(event_id)
        LOG.info("Recorded event #%s %s %s %s (Discord filter applied)", event_id, alert.side, alert.timeframe, alert.poi)


def _notify_health(config: Config, message: str) -> None:
    try:
        _post_discord(config, {
            "content": "Sktrader JIFU monitor: " + message[:1300],
            "allowed_mentions": {"parse": []},
        })
    except (OSError, urllib.error.HTTPError, RuntimeError) as exc:
        LOG.warning("Health notification failed: %s", type(exc).__name__)


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
            print("Log into JIFU in this browser, open Auroux > Alerts > All.")
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
                print("Alert capture: NOT READY.", exc)
            else:
                print(f"Alert capture: READY; {len(alerts)} complete Auroux cards")
        finally:
            context.close()


def watch(config: Config) -> None:
    from playwright.sync_api import sync_playwright

    _secure_profile(config.profile)
    store = EventStore(config.database)
    _bot_token(config.bot_token_file)  # Fail at startup if existing DK Capital credentials are unavailable.
    LOG.info("Starting WATCH-ONLY collector; Discord bot channel=%s; no broker/execution permissions exist",
             config.discord_channel_id)
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
            health_notified = False
            while True:
                started = time.monotonic()
                try:
                    alerts = _read_alerts(page)
                    count, baseline = store.observe(alerts)
                    if baseline:
                        LOG.info("Baseline saved: %s existing alerts (not replayed)", len(alerts))
                    elif count:
                        LOG.info("Detected %s newly inserted alerts", count)
                    _send_pending(store, config)
                    if health_notified:
                        _notify_health(config, "Recovered: JIFU alert parsing resumed")
                    failures = 0
                    health_notified = False
                except (Exception) as exc:
                    failures += 1
                    LOG.exception("Monitor pass failed (%s consecutive): %s", failures, type(exc).__name__)
                    if failures == 3 and not health_notified:
                        _notify_health(config, "ALERT: cannot read/forward JIFU alerts; check login or UI. Trading is NOT enabled.")
                        health_notified = True
                    if failures and failures % 6 == 0:
                        try:
                            page.reload(wait_until="domcontentloaded", timeout=45000)
                            _prepare_page(page)
                        except Exception:
                            LOG.exception("Recovery page reload failed")
                time.sleep(max(0.2, config.poll_seconds - (time.monotonic() - started)))
        finally:
            context.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sktrader JIFU collector (watch-only)")
    parser.add_argument("command", choices=["login", "diagnose", "watch"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = Config.from_env(require_discord=args.command == "watch")
    if args.command == "login":
        login(config)
    elif args.command == "diagnose":
        diagnose(config)
    else:
        watch(config)
