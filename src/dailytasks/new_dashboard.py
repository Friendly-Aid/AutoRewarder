"""
New (Next.js) Microsoft Rewards dashboard — Daily Set handler.

Microsoft's redesigned dashboard is a React/Next.js app: the legacy
`mee-rewards-*` DOM is gone and its Tailwind class names are obfuscated. But the
daily-set data is streamed into the page as an RSC payload (`window.__next_f`)
that contains, for each activity, its `destination` (the Bing search URL the
card links to), `isCompleted`, `points`, `title` and `date`.

Rather than scrape fragile markup, we read that JSON and then visit each
incomplete activity's `destination` — the exact URL a real click would open,
which is what credits the daily-set offer server-side. Completion tracking
(status.json) is handled by the caller (`DailySet`), so this handler only
returns whether it's reasonable to mark today as done.
"""

import random
import time

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait

DASHBOARD_URL = "https://rewards.bing.com/dashboard"

# Concatenate every streamed RSC chunk (`window.__next_f` is a list of
# `[1, "<chunk>"]` entries) and pull out each `dailySetItems` array, returning
# the parsed items to Python. A balanced-bracket scan that respects string
# literals extracts each array so JSON.parse gets a well-formed slice. The
# payload may repeat the array across chunks; the Python side dedupes by offerId.
_EXTRACT_DAILY_SET_JS = r"""
try {
  var raw = window.__next_f || [];
  var parts = [];
  for (var n = 0; n < raw.length; n++) {
    var e = raw[n];
    if (Array.isArray(e)) { if (typeof e[1] === 'string') parts.push(e[1]); }
    else if (typeof e === 'string') { parts.push(e); }
  }
  var blob = parts.join('');
  var out = [];
  var key = '"dailySetItems"';
  var idx = 0;
  while ((idx = blob.indexOf(key, idx)) !== -1) {
    var i = blob.indexOf('[', idx + key.length);
    if (i === -1) break;
    var depth = 0, inStr = false, esc = false, start = i;
    for (; i < blob.length; i++) {
      var c = blob[i];
      if (inStr) {
        if (esc) esc = false;
        else if (c === '\\') esc = true;
        else if (c === '"') inStr = false;
      } else {
        if (c === '"') inStr = true;
        else if (c === '[') depth++;
        else if (c === ']') { depth--; if (depth === 0) { i++; break; } }
      }
    }
    try {
      var arr = JSON.parse(blob.slice(start, i));
      if (Array.isArray(arr)) { for (var j = 0; j < arr.length; j++) out.push(arr[j]); }
    } catch (err) { /* skip malformed slice */ }
    idx = i;
  }
  return out;
} catch (e) { return []; }
"""

# DOM fallback: read today's daily-set activities straight from the rendered
# `#dailyset` section when the RSC JSON isn't available. The section holds only
# today's cards, each an <a> pointing at the Bing search that credits it. We
# best-effort detect completion from a small set of localized "done" words; when
# unsure we treat the card as incomplete (re-opening a completed daily search is
# harmless).
_DOM_DAILY_SET_JS = r"""
try {
  var out = [];
  var root = document.getElementById('dailyset');
  if (!root) return out;
  var doneWords = ['terminé','termine','completed','complete','done','erledigt',
    'abgeschlossen','completado','completada','completato','concluído','voltooid',
    'terminado','fait'];
  var links = root.querySelectorAll('a[href]');
  for (var i = 0; i < links.length; i++) {
    var a = links[i];
    var href = a.href || a.getAttribute('href') || '';
    // Only real daily-set activities point at a Bing search. This also excludes
    // the section header's "Gagner plus" / "Earn more" link (→ /earn), whose
    // absolute href still contains "bing.com".
    if (href.indexOf('bing.com/search') < 0) continue;
    // Title only: the card's bold title node, not the whole card text (which
    // also holds the description, points and status).
    var tEl = a.querySelector('.text-globalBody2Strong') || a.querySelector('p');
    var title = tEl ? (tEl.textContent || '').replace(/\s+/g, ' ').trim() : '';
    var low = (a.textContent || '').toLowerCase();
    var done = false;
    for (var d = 0; d < doneWords.length; d++) {
      if (low.indexOf(doneWords[d]) >= 0) { done = true; break; }
    }
    out.push({ destination: href, title: title.slice(0, 80), isCompleted: done, date: null });
  }
  return out;
} catch (e) { return []; }
"""

# Diagnostic snapshot logged when no activities are found, so a failure can be
# understood from the logs (did the RSC chunk stream in? is the section there?).
_DIAG_JS = r"""
try {
  var chunks = window.__next_f || [];
  var parts = [];
  for (var n = 0; n < chunks.length; n++) {
    var e = chunks[n];
    if (Array.isArray(e)) { if (typeof e[1] === 'string') parts.push(e[1]); }
    else if (typeof e === 'string') { parts.push(e); }
  }
  var blob = parts.join('');
  return {
    chunks: chunks.length,
    blobLen: blob.length,
    hasKey: blob.indexOf('"dailySetItems"') >= 0,
    hasDailyset: !!document.getElementById('dailyset'),
    url: location.href,
    title: document.title
  };
} catch (e) { return { error: String(e).slice(0, 120) }; }
"""


class NewDashboardDailySet:
    """Daily Set handler for the new Next.js Microsoft Rewards dashboard."""

    def __init__(self, logger=None):
        """
        Args:
            logger (callable, optional): A function to log messages.
        """
        self.logger = logger

    def _log(self, message):
        if self.logger:
            self.logger(message)

    # -- Data extraction -------------------------------------------------------

    def _read_items(self, driver):
        """Read and dedupe the daily-set items embedded in the current page."""
        try:
            raw = driver.execute_script(_EXTRACT_DAILY_SET_JS)
        except Exception as e:
            self._log(f"[WARNING] Could not read new-dashboard data: {e}")
            return []

        if not isinstance(raw, list):
            return []

        # Dedupe by offerId; prefer the record that reports completion so a
        # stale "incomplete" copy in another chunk can't re-trigger a visit.
        by_id = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = item.get("offerId") or item.get("hash") or item.get("destination")
            if key is None:
                continue
            prev = by_id.get(key)
            if prev is None or (
                item.get("isCompleted") and not prev.get("isCompleted")
            ):
                by_id[key] = item
        return list(by_id.values())

    def _read_items_polling(self, driver, attempts=8, delay=1.5):
        """
        Poll `_read_items` until items appear. The dashboard streams the daily-set
        RSC chunk progressively, so it can land a beat after the page's load
        event; a single read often races ahead of it.
        """
        for _ in range(max(1, attempts)):
            items = self._read_items(driver)
            if items:
                return items
            time.sleep(delay)
        return []

    def _read_items_dom(self, driver):
        """Fallback: read today's daily-set activities from the rendered DOM."""
        try:
            raw = driver.execute_script(_DOM_DAILY_SET_JS)
        except Exception as e:
            self._log(f"[WARNING] Could not read new-dashboard DOM: {e}")
            return []
        if not isinstance(raw, list):
            return []
        return [it for it in raw if isinstance(it, dict) and it.get("destination")]

    def _diagnostics(self, driver):
        """Return a small diagnostic dict about the current page (for logging)."""
        try:
            info = driver.execute_script(_DIAG_JS)
            return info if isinstance(info, dict) else {}
        except Exception as e:
            return {"error": str(e)[:120]}

    @staticmethod
    def _date_key(item):
        """Parse an item's MM/DD/YYYY date into a comparable (Y, M, D) tuple."""
        raw = item.get("date")
        if not isinstance(raw, str):
            return None
        parts = raw.split("/")
        if len(parts) != 3:
            return None
        try:
            month, day, year = (int(p) for p in parts)
        except ValueError:
            return None
        return (year, month, day)

    def _todays_items(self, items):
        """
        Return the subset of items for "today".

        The dashboard returns today's set plus a few upcoming days, all of which
        are `isCompleted: false` until unlocked. Past days are never returned, so
        the smallest date present is today — using it avoids crediting (locked)
        future-day activities and sidesteps any client/server timezone mismatch.
        """
        keyed = [(self._date_key(it), it) for it in items]
        dated = [(k, it) for k, it in keyed if k is not None]
        if not dated:
            # No parseable dates — fall back to treating everything as today.
            return items
        today = min(k for k, _ in dated)
        return [it for k, it in dated if k == today]

    # -- Navigation helpers ----------------------------------------------------

    def _wait_ready(self, driver, timeout=15):
        """Wait until the new dashboard has streamed its data / rendered."""

        def _ready(d):
            try:
                return bool(
                    d.execute_script(
                        "return !!(window.__next_f || document.getElementById('dailyset'));"
                    )
                )
            except Exception:
                return False

        try:
            WebDriverWait(driver, timeout).until(_ready)
        except TimeoutException:
            pass

    # -- Top-level entry point -------------------------------------------------

    def perform(self, driver, human, stop_event=None):
        """
        Open the new dashboard, visit each incomplete daily-set activity for
        today, then re-read to confirm progress.

        Args:
            driver: Selenium WebDriver instance.
            human: HumanBehavior instance (used for human-like dwell/scroll).
            stop_event (threading.Event, optional): When set, aborts cleanly.

        Returns:
            bool: True if it's reasonable to mark today as done, False if we made
                  no progress (so the next run can retry).
        """
        self._log("Performing daily Rewards tasks (new dashboard)")

        try:
            driver.get(DASHBOARD_URL)
            self._wait_ready(driver)
            # Brief settle so late RSC chunks finish streaming in.
            time.sleep(random.uniform(2, 3))

            # Primary: poll the embedded RSC JSON (streams in progressively). It
            # is authoritative — it carries the exact destination + isCompleted.
            items = self._read_items_polling(driver)
            source = "json"
            # Fallback: read today's cards from the rendered #dailyset section.
            if not items:
                # Always log why the JSON path came up empty, even when the DOM
                # fallback succeeds, so we can tell "chunk not streamed yet" from
                # "extraction bug" without another round-trip.
                diag = self._diagnostics(driver)
                self._log(
                    "[INFO] New-dashboard JSON had no daily-set items — "
                    f"chunks={diag.get('chunks')} blobLen={diag.get('blobLen')} "
                    f"hasDailySetItems={diag.get('hasKey')} "
                    f"hasDailysetSection={diag.get('hasDailyset')} "
                    f"url={diag.get('url')!r}"
                )
                items = self._read_items_dom(driver)
                source = "dom"

            todays = self._todays_items(items)
            if not todays:
                diag = self._diagnostics(driver)
                self._log(
                    "[WARNING] No daily-set activities found in the new dashboard — "
                    f"url={diag.get('url')!r} title={diag.get('title')!r} "
                    f"chunks={diag.get('chunks')} blobLen={diag.get('blobLen')} "
                    f"hasDailySetItems={diag.get('hasKey')} "
                    f"hasDailysetSection={diag.get('hasDailyset')}"
                )
                return False

            self._log(
                f"New dashboard daily set: read {len(todays)} item(s) via {source}."
            )

            incomplete = [it for it in todays if not it.get("isCompleted")]
            self._log(
                f"New dashboard daily set: "
                f"{len(todays) - len(incomplete)}/{len(todays)} already complete."
            )

            if not incomplete:
                return True

            attempted = 0
            for item in incomplete:
                if stop_event is not None and stop_event.is_set():
                    self._log("Stop requested — halting new-dashboard daily set.")
                    break

                destination = item.get("destination")
                title = item.get("title") or item.get("offerId") or "activity"
                if not isinstance(destination, str) or not destination.startswith(
                    "http"
                ):
                    self._log(f"[WARNING] Skipping '{title}': no valid destination.")
                    continue

                self._log(f"Opening daily-set activity: {title}")
                try:
                    driver.get(destination)
                    attempted += 1
                    # Dwell like a human reading the results page.
                    time.sleep(random.uniform(2, 4))
                    try:
                        human.scroll_page()
                    except Exception:
                        pass
                    time.sleep(random.uniform(2, 5))
                except Exception as e:
                    if stop_event is not None and stop_event.is_set():
                        break
                    self._log(f"[WARNING] Failed to open '{title}': {e}")

            if attempted == 0:
                self._log("[WARNING] No daily-set activities could be opened.")
                return False

            if stop_event is not None and stop_event.is_set():
                return False

            # Re-read the dashboard to measure how many activities actually
            # flipped to complete. window.__next_f is drained after hydration, so
            # the re-read relies on the same JSON-then-DOM strategy as the initial
            # read — reading JSON only here would always look "all done" (empty)
            # and falsely report success.
            newly = 0
            verified = False
            try:
                driver.get(DASHBOARD_URL)
                self._wait_ready(driver)
                time.sleep(random.uniform(1.5, 2.5))
                after_items = self._read_items(driver) or self._read_items_dom(driver)
                after = self._todays_items(after_items)
                if after:
                    verified = True
                    still_incomplete = sum(
                        1 for it in after if not it.get("isCompleted")
                    )
                    newly = max(0, len(incomplete) - still_incomplete)
            except Exception:
                pass

            if newly > 0:
                self._log(f"New dashboard daily set: +{newly} completed this run.")
                return True

            if verified:
                # We could re-read the cards and they are still incomplete: the
                # visits did not credit (common in headless — Rewards often won't
                # credit a headless session) or the items are quizzes needing
                # manual answers. Report honestly and don't mark today done, so a
                # later (visible) run can retry.
                self._log(
                    "[WARNING] Daily-set activities were opened but none are marked "
                    "complete on the dashboard. Not marking today done — if this is "
                    "a headless run, try again with the browser visible."
                )
            else:
                self._log(
                    "[WARNING] Could not re-read the dashboard to confirm daily-set "
                    "completion. Not marking today done."
                )
            return False

        except Exception as e:
            if stop_event is not None and stop_event.is_set():
                self._log("New-dashboard daily set halted by Stop.")
                return False
            self._log(f"[ERROR] New-dashboard daily set failed: {e}")
            return False
