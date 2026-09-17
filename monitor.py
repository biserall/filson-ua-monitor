import asyncio
import hashlib
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import httpx
import yaml
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "data" / "seen.json"
SOURCES_PATH = ROOT / "sources.yaml"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# First scheduled run remembers everything already on the sites but sends no flood.
WARM_START = os.getenv("WARM_START", "1") == "1"
PAGE_TIMEOUT_MS = int(os.getenv("PAGE_TIMEOUT_MS", "45000"))

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "yclid", "ref", "referrer", "source",
}

PRICE_RE = re.compile(
    r"(?:(?:\d[\d\s.,]{0,12})\s*(?:грн|₴|uah|\$|€))",
    re.IGNORECASE
)


@dataclass
class Listing:
    source: str
    title: str
    url: str
    price: str = ""


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def canonicalize(url: str) -> str:
    p = urlparse(url)
    clean_query = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    ]
    return urlunparse(
        (
            p.scheme.lower() or "https",
            p.netloc.lower(),
            p.path.rstrip("/") or "/",
            p.params,
            urlencode(clean_query, doseq=True),
            "",
        )
    )


def listing_id(source: str, url: str) -> str:
    raw = f"{source}|{canonicalize(url)}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"version": 1, "initialized": False, "items": {}}
    try:
        data = json.loads(STATE_PATH.read_text("utf-8"))
        data.setdefault("version", 1)
        data.setdefault("initialized", False)
        data.setdefault("items", {})
        return data
    except Exception:
        return {"version": 1, "initialized": False, "items": {}}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Prevent unbounded repository growth. Keep the 7,500 most recently seen entries.
    items = state.get("items", {})
    if len(items) > 7500:
        newest = sorted(
            items.items(),
            key=lambda kv: kv[1].get("first_seen", 0),
            reverse=True,
        )[:7500]
        state["items"] = dict(newest)

    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_sources() -> list[dict]:
    data = yaml.safe_load(SOURCES_PATH.read_text("utf-8")) or {}
    return [s for s in data.get("sources", []) if s.get("enabled", True)]


def source_filter_matches(text: str, cfg: dict) -> bool:
    lowered = text.casefold()

    if "filson" not in lowered:
        return False

    include_any = [str(x).casefold() for x in cfg.get("include_any", [])]
    exclude_any = [str(x).casefold() for x in cfg.get("exclude_any", [])]

    if include_any and not any(x in lowered for x in include_any):
        return False
    if exclude_any and any(x in lowered for x in exclude_any):
        return False
    return True


def looks_like_listing_url(href: str, domain: str, search_url: str) -> bool:
    if not href:
        return False

    try:
        p = urlparse(href)
    except Exception:
        return False

    if domain.casefold() not in p.netloc.casefold():
        return False
    if canonicalize(href) == canonicalize(search_url):
        return False

    path = p.path.casefold()
    non_listing_fragments = (
        "/login", "/signin", "/register", "/help", "/terms", "/privacy",
        "/favorites", "/saved", "/cart", "/catalog", "/brands/", "/brand/",
        "/search", "/about", "/contacts",
    )
    if any(fragment in path for fragment in non_listing_fragments):
        return False

    return len(path.strip("/")) >= 3


def extract_price(text: str) -> str:
    match = PRICE_RE.search(text or "")
    return normalize_space(match.group(0)) if match else ""


async def scrape_source(page, cfg: dict) -> list[Listing]:
    name = cfg["name"]
    url = cfg["url"]
    domain = cfg["domain"]

    print(f"Scanning {name}: {url}", flush=True)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        await page.wait_for_timeout(2500)
    except PlaywrightTimeoutError:
        print(f"{name}: timeout; parsing already loaded content", flush=True)
    except Exception as exc:
        print(f"{name}: load failed: {exc}", file=sys.stderr, flush=True)
        return []

    rows = await page.evaluate(
        """
        () => {
          const anchors = Array.from(document.querySelectorAll('a[href]'));
          return anchors.map(a => {
            let node = a;
            let bestText = (a.innerText || a.textContent || '').trim();

            for (let i = 0; i < 5 && node && node.parentElement; i++) {
              node = node.parentElement;
              const txt = (node.innerText || node.textContent || '').trim();
              if (txt.length >= bestText.length && txt.length <= 1800) {
                bestText = txt;
              }
              if (node && ['ARTICLE', 'LI'].includes(node.tagName)) break;
            }

            const img =
              a.querySelector('img') ||
              (node && node.querySelector ? node.querySelector('img') : null);

            return {
              href: a.href || '',
              anchorText: (a.innerText || a.textContent || '').trim(),
              cardText: bestText,
              imgAlt: img ? (img.alt || '') : ''
            };
          });
        }
        """
    )

    found: list[Listing] = []
    unique_urls: set[str] = set()

    for row in rows:
        href = row.get("href", "")
        if not looks_like_listing_url(href, domain, url):
            continue

        anchor_text = normalize_space(row.get("anchorText", ""))
        card_text = normalize_space(row.get("cardText", ""))
        img_alt = normalize_space(row.get("imgAlt", ""))
        combined = normalize_space(" ".join((anchor_text, img_alt, card_text)))

        if not source_filter_matches(combined, cfg):
            continue

        clean_url = canonicalize(href)
        if clean_url in unique_urls:
            continue
        unique_urls.add(clean_url)

        title = normalize_space(anchor_text or img_alt or card_text[:220])
        if len(title) > 220:
            title = title[:217] + "..."

        if title.casefold() in {
            "filson", "купити", "дивитись", "переглянути", "смотреть"
        }:
            continue

        found.append(
            Listing(
                source=name,
                title=title or f"Filson — {name}",
                url=clean_url,
                price=extract_price(card_text),
            )
        )

    print(f"{name}: {len(found)} candidates", flush=True)
    return found


async def telegram_send(item: Listing) -> None:
    source = html.escape(item.source)
    title = html.escape(item.title)
    price = f"\n💰 <b>{html.escape(item.price)}</b>" if item.price else ""
    safe_url = html.escape(item.url, quote=True)

    message = (
        f"🆕 <b>FILSON — {source}</b>\n"
        f"{title}"
        f"{price}\n"
        f'🔗 <a href="{safe_url}">Открыть объявление</a>'
    )

    endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            endpoint,
            json={
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
        )
        response.raise_for_status()


async def telegram_status(message: str) -> None:
    endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            endpoint,
            json={"chat_id": CHAT_ID, "text": message},
        )
        response.raise_for_status()


async def main() -> int:
    if not BOT_TOKEN:
        print("Missing GitHub Secret: TELEGRAM_BOT_TOKEN", file=sys.stderr)
        return 2
    if not CHAT_ID:
        print("Missing GitHub Secret: TELEGRAM_CHAT_ID", file=sys.stderr)
        return 2

    state = load_state()
    sources = load_sources()

    current: list[Listing] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            locale="uk-UA",
            viewport={"width": 1440, "height": 1200},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0 Safari/537.36"
            ),
        )

        try:
            for cfg in sources:
                page = await context.new_page()
                try:
                    current.extend(await scrape_source(page, cfg))
                finally:
                    await page.close()
        finally:
            await context.close()
            await browser.close()

    unseen: list[Listing] = []
    now = int(time.time())

    for item in current:
        key = listing_id(item.source, item.url)
        if key not in state["items"]:
            unseen.append(item)

    # On the very first run, silently remember everything already listed.
    if not state.get("initialized", False) and WARM_START:
        print(
            f"Warm start: remembering {len(unseen)} current listings; no Telegram flood.",
            flush=True,
        )
        for item in unseen:
            key = listing_id(item.source, item.url)
            state["items"][key] = {
                "source": item.source,
                "title": item.title,
                "url": item.url,
                "price": item.price,
                "first_seen": now,
            }
        state["initialized"] = True
        save_state(state)
        return 0

    sent = 0

    # Reverse so that older unseen results arrive first and the newest one
    # remains the last/most visible Telegram message.
    for item in reversed(unseen):
        try:
            await telegram_send(item)
            key = listing_id(item.source, item.url)
            state["items"][key] = {
                "source": item.source,
                "title": item.title,
                "url": item.url,
                "price": item.price,
                "first_seen": now,
            }
            sent += 1
            await asyncio.sleep(0.5)
        except Exception as exc:
            # Do not mark a failed Telegram delivery as seen.
            print(
                f"Telegram send failed for {item.source} {item.url}: {exc}",
                file=sys.stderr,
                flush=True,
            )

    state["initialized"] = True
    save_state(state)
    print(f"Sent {sent} new listings", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
