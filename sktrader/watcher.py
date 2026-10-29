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

# The authenticated JIFU layout may wrap field-label text in nested spans.
# Read text nodes and climb only to the nearest COMPLETE single-alert card.
# Never fall back to the entire sidebar (that would mix multiple signals).
EXTRACT_CARDS_JS = r"""() => {
  const tree = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const cards = [];
  const seen = new Set();
  let node;
  while ((node = tree.nextNode())) {
    if ((node.nodeValue || '').trim().toLowerCase() !== 'point of interest') continue;
    let el = node.parentElement;
    if (!el || !el.getClientRects().length) continue;
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

# Only counts and booleans. Never log cookies, screenshots, page HTML,
# password inputs, login form contents, or the full website URL/query string.
DIAG_JS = r"""() => {
  const body = document.body?.innerText || '';
  const tree = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let poiLabels = 0;
  let node;
  while ((node = tree.nextNode())) {
    if ((node.nodeValue || '').trim().toLowerCase() === 'point of interest'
        && node.parentElement?.getClientRects().length) poiLabels++;
  }
  return {
    path: location.pathname,
    ready_state: document.readyState,
    body_characters: body.length,
    poi_labels: poiLabels,
    has_auroux: /\bAUROUX\b/i.test(body),
    has_xauusd: /\bXAUUSD\b/i.test(body),
    has_invalidation: /Invalidation/i.test(body),
    has_exit1: /Exit\s*1/i.test(body),
    has_exit2: /Exit\s*2/i.test(body),
    has_alerts_tab: /\bAlerts\b/i.test(body),
    possible_login: /Log\s*in|Sign\s*in/i.test(body) && /Password/i.test(body)
  };
}"""

@dataclass(frozen=True)
class Config:
    url: str
    profile: Path
    database: Path
    webhook: str
    poll_seconds: float
    timeframe: str
    levels: frozenset[int]
    headless: bool

    @classmethod
    def from_env(cls, *, require_webhook: bool) -> "Config":
        webhook = os.getenv("SKTRADER_DISCORD_WEBHOOK", "").strip()
        if require_webhook and not webhook.startswith("https://discord.com/api/webhooks/"):
            raise ValueError("SKTRADER_DISCORD_WEBHOOK must be a Discord webhook URL")
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
            webhook=webhook,
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
    if not raw_cards:
        safe_status = page.evaluate(DIAG_JS)
        raise SnapshotError(
            "No complete Auroux alert cards detected; browser diagnostic: "
            + json.dumps(safe_status, sort_keys=True)
        )
    alerts: list[Alert] = []
    for text in raw_cards:
        try:
            alerts.append(parse_card(text))
        except AlertError as exc:
            # Do not update the snapshot if even one visible card is malformed.
            raise SnapshotError(f"Unparseable alert card: {exc}") from exc
    return alerts


def _post_discord(webhook: str, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        webhook, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "sktrader-collector/0.1"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        if response.status not in (200, 204):
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
            _post_discord(config.webhook, _alert_embed(event_id, observed_at, alert))
        store.delivered(event_id)
        LOG.info("Recorded event #%s %s %s %s (Discord filter applied)", event_id, alert.side, alert.timeframe, alert.poi)


def _notify_health(config: Config, message: str) -> None:
    try:
        _post_discord(config.webhook, {
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
    LOG.info("Starting WATCH-ONLY collector; no broker/execution permissions exist")
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
    config = Config.from_env(require_webhook=args.command == "watch")
    if args.command == "login":
        login(config)
    elif args.command == "diagnose":
        diagnose(config)
    else:
        watch(config)
