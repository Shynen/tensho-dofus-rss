import json
import re
import html
from pathlib import Path
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


BASE_URL = "https://www.dofus.com"
LISTING_URL = f"{BASE_URL}/fr/mmorpg/actualites/maj"

CACHE_FILE = Path("dofus-changelog-cache.json")
DISCORD_STATE_FILE = Path("dofus-changelog-discord-state.json")

RSS_FILE = Path("dofus-changelog.xml")
DISCORD_RSS_FILE = Path("dofus-changelog-discord.xml")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)


def clean_text(value):
    if not value:
        return ""
    value = html.unescape(str(value))
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_url(url):
    if not url:
        return ""
    url = urljoin(BASE_URL, url)
    return url.split("#", 1)[0].rstrip("/")


def parse_date(value):
    if not value:
        return None

    value = clean_text(value)

    # ISO / RFC / common formats
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    for fmt in (
        "%d/%m/%Y",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass

    return None


def date_from_url(url):
    """
    DOFUS changelog URLs frequently contain the publication date:
      ...patch-notes-3-6-10-10-18-08-2026
      ...patch-notes-3-6-9-9-04-08-2026

    This is deliberately dynamic: it is not tied to any specific article.
    """
    slug = normalize_url(url).rsplit("/", 1)[-1]

    matches = re.findall(r"(?<!\d)(\d{1,2})-(\d{1,2})-(\d{4})(?!\d)", slug)
    if not matches:
        return None

    # Use the last date present in the slug.
    day, month, year = matches[-1]
    try:
        return datetime(
            int(year), int(month), int(day), tzinfo=timezone.utc
        )
    except ValueError:
        return None


def clean_title(title):
    title = clean_text(title)

    # UI label sometimes gets concatenated with the real title.
    title = re.sub(
        r"^\s*Découvrir\s+"
        r"(?=\d{1,2}\s+(?:Janvier|Février|Mars|Avril|Mai|Juin|Juillet|Août|Septembre|Octobre|Novembre|Décembre)\b)",
        "",
        title,
        flags=re.IGNORECASE,
    )

    title = re.sub(r"^\s*Découvrir\s*[:\-|]?\s*$", "", title, flags=re.IGNORECASE)
    return title.strip()


def load_json(path, default):
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data
    except Exception:
        pass
    return default


def save_json(path, data):
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def extract_jsonld_date(soup):
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        if not raw:
            continue

        try:
            data = json.loads(raw)
        except Exception:
            continue

        objects = data if isinstance(data, list) else [data]
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            for key in ("datePublished", "dateCreated", "dateModified"):
                dt = parse_date(obj.get(key))
                if dt:
                    return dt
    return None


def extract_meta_date(soup):
    for attrs in (
        {"property": "article:published_time"},
        {"name": "article:published_time"},
        {"property": "og:published_time"},
        {"name": "date"},
        {"name": "publishdate"},
        {"name": "datePublished"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag:
            dt = parse_date(tag.get("content"))
            if dt:
                return dt

    for tag in soup.find_all("time"):
        dt = parse_date(tag.get("datetime") or tag.get_text(" ", strip=True))
        if dt:
            return dt

    return None


def extract_title_from_soup(soup):
    selectors = [
        "h1",
        '[data-testid="article-title"]',
        ".article-title",
        ".news-title",
    ]

    for selector in selectors:
        tag = soup.select_one(selector)
        if tag:
            title = clean_title(tag.get_text(" ", strip=True))
            if title:
                return title

    if soup.title:
        title = clean_title(soup.title.get_text(" ", strip=True))
        title = re.sub(r"\s*\|\s*DOFUS.*$", "", title, flags=re.IGNORECASE)
        if title:
            return title

    return ""


def extract_listing_items(page):
    """
    Extract links from the currently rendered /maj listing.
    The selectors are intentionally generic because Ankama's markup changes.
    """
    items = []
    seen = set()

    links = page.locator('a[href*="/fr/mmorpg/actualites/maj/"]').all()

    for link in links:
        try:
            href = normalize_url(link.get_attribute("href"))
            if not href or href in seen:
                continue

            # Do not treat the generic category page as an article.
            if href == normalize_url(LISTING_URL):
                continue

            text = clean_text(link.inner_text())
            title = clean_title(text)

            # Look around the card for a date.
            card = link.locator("xpath=ancestor::*[self::article or contains(@class,'card') or contains(@class,'article')][1]")
            date_text = ""
            if card.count():
                try:
                    date_text = clean_text(card.inner_text())
                except Exception:
                    pass

            dt = None

            # First use explicit time elements in the card.
            if card.count():
                try:
                    times = card.locator("time").all()
                    for t in times:
                        dt = parse_date(t.get_attribute("datetime") or t.inner_text())
                        if dt:
                            break
                except Exception:
                    pass

            if not dt:
                # Generic date patterns in visible card text.
                m = re.search(
                    r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b",
                    date_text,
                )
                if m:
                    dt = parse_date(f"{m.group(1)}/{m.group(2)}/{m.group(3)}")

            items.append({
                "url": href,
                "title": title,
                "date": dt.isoformat() if dt else None,
            })
            seen.add(href)

        except Exception:
            continue

    return items


def collect_listing(page):
    page.goto(LISTING_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    items = extract_listing_items(page)
    print(f"Premier lot : {len(items)} mises à jour détectées.")

    for i in range(8):
        print(f"🔄 Recherche du bouton VOIR PLUS ({i + 1}/8)...")
        buttons = page.get_by_text(re.compile(r"voir plus", re.I))
        if not buttons.count():
            print("ℹ️ Plus de bouton VOIR PLUS.")
            break

        try:
            buttons.last.click(timeout=3000)
            page.wait_for_timeout(1800)
        except Exception:
            print("ℹ️ Plus de bouton VOIR PLUS.")
            break

        new_items = extract_listing_items(page)
        if len(new_items) <= len(items):
            print("ℹ️ Plus de contenu détecté.")
            break
        items = new_items

    print(f"🟢 Total mises à jour récupérées : {len(items)}")
    return items


def enrich_item(page, item, cache):
    url = item["url"]

    cached = cache.get(url, {})
    listing_title = clean_title(item.get("title"))
    listing_date = parse_date(item.get("date"))

    # 1) Date from URL is the strongest fallback for patch-note URLs.
    url_date = date_from_url(url)

    title = listing_title
    dt = url_date or listing_date

    print(f"\n🔎 Ouverture article avec Playwright...")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1200)

        soup = BeautifulSoup(page.content(), "html.parser")

        article_title = extract_title_from_soup(soup)
        article_date = extract_jsonld_date(soup) or extract_meta_date(soup)

        if article_title and not article_title.lower().startswith("découvrir"):
            title = article_title

        # Priority:
        # URL date > article structured date > listing date > cache.
        if url_date:
            dt = url_date
            date_source = "URL"
        elif article_date:
            dt = article_date
            date_source = "ARTICLE"
        elif listing_date:
            dt = listing_date
            date_source = "LISTING"
        else:
            dt = parse_date(cached.get("date"))
            date_source = "CACHE" if dt else None

    except Exception as exc:
        print(f"⚠️ Erreur ouverture article : {exc}")
        date_source = None

    if not dt:
        # Final dynamic fallback: cached value.
        dt = parse_date(cached.get("date"))
        if dt:
            date_source = "CACHE"

    if not title:
        title = clean_title(cached.get("title", ""))

    if not dt:
        print("⚠️ Date introuvable.")
        return None

    if not title:
        print("⚠️ Titre introuvable.")
        return None

    title = clean_title(title)

    print(f"   🏷️ Titre : {title}")
    print(f"   📅 Date trouvée via {date_source or 'FALLBACK'}: {format_datetime(dt)}")
    print(f"🟢 {format_datetime(dt)} - {title}")

    result = {
        "url": url,
        "title": title,
        "date": dt.isoformat(),
    }

    cache[url] = result
    return result


def build_rss(items, path, title="DOFUS Changelogs"):
    now = format_datetime(datetime.now(timezone.utc), usegmt=True)

    chunks = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "<channel>",
        f"<title>{html.escape(title)}</title>",
        f"<link>{BASE_URL}/fr/mmorpg/actualites/maj</link>",
        "<description>Changelogs DOFUS</description>",
        f"<lastBuildDate>{now}</lastBuildDate>",
    ]

    for item in items:
        dt = parse_date(item["date"])
        pub = format_datetime(dt, usegmt=True) if dt else now
        chunks.extend([
            "<item>",
            f"<title>{html.escape(item['title'])}</title>",
            f"<link>{html.escape(item['url'])}</link>",
            f"<guid isPermaLink=\"true\">{html.escape(item['url'])}</guid>",
            f"<description>{html.escape(item['title'])}</description>",
            f"<pubDate>{pub}</pubDate>",
            "</item>",
        ])

    chunks.extend(["</channel>", "</rss>"])
    path.write_text("\n".join(chunks) + "\n", encoding="utf-8")


def build_discord_rss(item):
    """
    Minimal RSS 2.0 feed for Discord/Readybot:
    one item only, with a valid pubDate.
    """
    if not item:
        build_rss([], DISCORD_RSS_FILE, "DOFUS Changelog Discord")
        return

    dt = parse_date(item["date"])
    pub = format_datetime(dt, usegmt=True)

    xml = "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "<channel>",
        "<title>DOFUS Changelog Discord</title>",
        f"<link>{html.escape(item['url'])}</link>",
        "<description>Dernier changelog DOFUS</description>",
        "<item>",
        f"<title>{html.escape(item['title'])}</title>",
        f"<link>{html.escape(item['url'])}</link>",
        f"<guid isPermaLink=\"true\">{html.escape(item['url'])}</guid>",
        f"<description>{html.escape(item['title'])}</description>",
        f"<pubDate>{pub}</pubDate>",
        "</item>",
        "</channel>",
        "</rss>",
    ])

    DISCORD_RSS_FILE.write_text(xml + "\n", encoding="utf-8")


def main():
    print("""
########################################
# Tensho Dofus
# CHANGELOGS / MISES À JOUR
########################################
""")

    cache = load_json(CACHE_FILE, {})
    print(f"Cache Changelog Dofus chargé : {len(cache)} articles.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=USER_AGENT, locale="fr-FR")

        print("\n========================================")
        print("Ouverture avec Playwright :")
        print(LISTING_URL)
        print("========================================")

        listing = collect_listing(page)

        print("""
########################################
# URLs Changelogs Dofus trouvées
########################################
""")

        results = []

        for index, item in enumerate(listing, 1):
            print(f"[{index}/{len(listing)}] {item['url']}")

            # If listing has no date, the URL parser can still recover patch dates.
            enriched = enrich_item(page, item, cache)
            if enriched:
                results.append(enriched)

        browser.close()

    # Deduplicate by URL.
    unique = {}
    for item in results:
        unique[item["url"]] = item
    results = list(unique.values())

    # Newest first, always dynamically.
    results.sort(
        key=lambda x: parse_date(x["date"]) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    # Keep the latest 20 for the complete RSS.
    results = results[:20]

    print("\n########################################")
    print(f"# {len(results)} Changelogs Dofus retenus")
    print("########################################")

    for i, item in enumerate(results, 1):
        print(f"{i:02d}. {format_datetime(parse_date(item['date']))} - {item['title']}")
        print(f"    {item['url']}")

    save_json(CACHE_FILE, {item["url"]: item for item in results})

    print("\nGénération de dofus-changelog.xml...")
    build_rss(results, RSS_FILE)
    print("🟢 dofus-changelog.xml généré.")

    print("\nGénération de dofus-changelog-discord.xml...")

    if not results:
        print("⚠️ Aucun changelog disponible.")
        build_discord_rss(None)
        print("🟢 dofus-changelog-discord.xml généré sans nouvel envoi.")
        return

    latest = results[0]

    print("\n🔎 Dernier changelog actuellement publié sur DOFUS :")
    print(f"   {latest['title']}")
    print(f"   {latest['url']}")
    print(f"   {format_datetime(parse_date(latest['date']))}")

    state = load_json(DISCORD_STATE_FILE, {})
    last_url = normalize_url(state.get("url"))
    last_date = parse_date(state.get("date"))

    latest_date = parse_date(latest["date"])

    is_new = (
        not last_url
        or latest["url"] != last_url
        or (latest_date and last_date and latest_date > last_date)
    )

    if is_new:
        print("🆕 Nouveau changelog à envoyer sur Discord.")
        build_discord_rss(latest)
        save_json(DISCORD_STATE_FILE, {
            "url": latest["url"],
            "title": latest["title"],
            "date": latest["date"],
        })
        print("🟢 État Discord sauvegardé.")
        print("🟢 dofus-changelog-discord.xml généré avec 1 nouveau changelog.")
    else:
        print("ℹ️ Le dernier changelog DOFUS a déjà été envoyé.")
        build_discord_rss(None)
        print("ℹ️ Aucun nouvel envoi Discord.")
        print("🟢 dofus-changelog-discord.xml généré sans nouvel envoi.")

    print("""
########################################
# DOFUS CHANGELOG RSS TERMINÉ
########################################
""")


if __name__ == "__main__":
    main()
