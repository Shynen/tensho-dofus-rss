import json
import os
import re
import requests

from bs4 import BeautifulSoup
from datetime import datetime, timezone
from email.utils import formatdate
from urllib.parse import urljoin
from xml.etree.ElementTree import Element, SubElement, ElementTree, indent

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


BASE_URL = "https://www.dofus.com"
SOURCE_URL = "https://www.dofus.com/fr/mmorpg/actualites/news"

OUTPUT = "dofus-news.xml"
DISCORD_OUTPUT = "dofus-news-discord.xml"
CACHE_FILE = "dofus_news_cache.json"

MAX_ARTICLES = 20
MAX_LOAD_MORE_CLICKS = 8

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/149.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

FRENCH_MONTHS = {
    "janvier": 1,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
    "decembre": 12,
}


print("")
print("########################################")
print("# Tensho Dofus")
print("# ACTUALITÉS FRANÇAISES")
print("########################################")
print("")


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_date(value):
    """Parse ISO/RFC dates used by the site."""
    if not value:
        return None

    value = clean_text(value)

    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"

        dt = datetime.fromisoformat(value)

        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except Exception:
        pass

    # RFC 2822 / RSS-style dates
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(value)

        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except Exception:
        return None


def parse_french_date(value):
    """Parse dates displayed by Dofus, e.g. '21 août 2026'."""
    if not value:
        return None

    text = clean_text(value).lower()
    text = text.replace(",", " ")

    # 21 août 2026 / 21 août 2026 à 10:30
    match = re.search(
        r"\b(\d{1,2})\s+"
        r"(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|"
        r"septembre|octobre|novembre|décembre|decembre)"
        r"\s+(\d{4})"
        r"(?:\s+(?:à|a)\s+(\d{1,2})(?::(\d{2}))?)?",
        text,
        re.IGNORECASE,
    )

    if match:
        day = int(match.group(1))
        month = FRENCH_MONTHS[match.group(2).lower()]
        year = int(match.group(3))
        hour = int(match.group(4) or 0)
        minute = int(match.group(5) or 0)

        try:
            return datetime(
                year,
                month,
                day,
                hour,
                minute,
                tzinfo=timezone.utc,
            )
        except ValueError:
            return None

    # 21/08/2026 or 21-08-2026
    match = re.search(
        r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})"
        r"(?:[ T](\d{1,2}):(\d{2}))?",
        text,
    )

    if match:
        try:
            return datetime(
                int(match.group(3)),
                int(match.group(2)),
                int(match.group(1)),
                int(match.group(4) or 0),
                int(match.group(5) or 0),
                tzinfo=timezone.utc,
            )
        except ValueError:
            return None

    return None


def extract_date_from_html(html):
    """
    Date extraction with several fallbacks.
    The Dofus website now relies heavily on JavaScript, so we inspect
    rendered HTML as well as JSON-LD/meta/time elements.
    """
    if not html:
        return None, "aucune donnée"

    soup = BeautifulSoup(html, "html.parser")

    # 1. JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()

        if not raw:
            continue

        # Direct regex first: robust against malformed JSON-LD
            for key in ("datePublished", "dateCreated", "dateModified"):
                    dt = (
                        parse_date(obj.get(key))
                        or parse_french_date(obj.get(key))
                    ) if obj.get(key) else None
            
                if dt:
                    return dt, f"JSON-LD/{key}"

        # Then attempt actual JSON parsing
        try:
            data = json.loads(raw)

            objects = data if isinstance(data, list) else [data]

            for obj in objects:
                if not isinstance(obj, dict):
                    continue

                for key in ("datePublished", "dateCreated", "dateModified"):
                        dt = (
                        parse_date(obj.get(key))
                        or parse_french_date(obj.get(key))
                    ) if obj.get(key) else None
            

                    if dt:
                        return dt, f"JSON-LD/{key}"

        except Exception:
            pass

    # 2. Meta tags
    meta_selectors = [
        ("property", "article:published_time"),
        ("property", "og:published_time"),
        ("name", "date"),
        ("name", "published"),
        ("name", "datePublished"),
    ]

    for attr, value in meta_selectors:
        meta = soup.find("meta", attrs={attr: value})

        if meta:
            raw = meta.get("content")
            dt = parse_date(raw) or parse_french_date(raw)

            if dt:
                return dt, f"meta/{attr}={value}"

    # 3. <time datetime="...">
    for node in soup.find_all("time"):
        raw = node.get("datetime")

        dt = parse_date(raw) or parse_french_date(raw)

        if dt:
            return dt, "time/datetime"

        visible = node.get_text(" ", strip=True)
        dt = parse_french_date(visible) or parse_date(visible)

        if dt:
            return dt, "time/text"

    # 4. Search visible text for a French date.
    # This is important for the current Dofus layout.
    body = soup.find("body")

    if body:
        visible_text = clean_text(body.get_text(" ", strip=True))

        # Prefer dates near words that normally identify publication.
        publication_patterns = [
            r"(?:publié|publication|mis à jour|actualit[ée])[^.]{0,120}",
            r"(?:le|du)\s+\d{1,2}\s+"
            r"(?:janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|"
            r"septembre|octobre|novembre|décembre|decembre)"
            r"\s+\d{4}[^.]{0,80}",
        ]

        for pattern in publication_patterns:
            for chunk in re.findall(
                pattern,
                visible_text,
                flags=re.IGNORECASE,
            ):
                dt = parse_french_date(chunk)

                if dt:
                    return dt, "texte publication"

        # Generic French date fallback
        dt = parse_french_date(visible_text)

        if dt:
            return dt, "texte visible"

    # 5. Raw HTML fallback.
    dt = parse_french_date(html)

    if dt:
        return dt, "HTML brut"

    return None, "introuvable"


def format_pubdate(dt):
    return formatdate(dt.timestamp(), usegmt=True)


def load_cache():
    if not os.path.exists(CACHE_FILE):
        print("Aucun cache Actualités Dofus trouvé.")
        return {}

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return {}

        print(f"Cache Actualités Dofus chargé : {len(data)} articles.")
        return data

    except Exception as e:
        print(f"⚠️ Erreur lecture cache : {e}")
        return {}


def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def is_valid_news_url(url):
    value = url.lower()

    if "dofus.com" not in value:
        return False

    if "/fr/mmorpg/actualites/news/" not in value:
        return False

    if value.rstrip("/") == SOURCE_URL.rstrip("/"):
        return False

    return True


def collect_news_urls(page):
    urls = set()

    try:
        page.goto(
            SOURCE_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        page.wait_for_timeout(4000)

    except Exception as e:
        print(f"❌ Erreur ouverture page : {e}")
        return []

    def collect_visible_urls():
        before = len(urls)

        links = page.locator(
            'a[href*="/fr/mmorpg/actualites/news/"]'
        )

        for i in range(links.count()):
            try:
                href = links.nth(i).get_attribute("href")

                if not href:
                    continue

                full_url = (
                    urljoin(BASE_URL, href)
                    .split("#", 1)[0]
                    .rstrip("/")
                )

                if is_valid_news_url(full_url):
                    urls.add(full_url)

            except Exception:
                pass

        return len(urls) - before

    collect_visible_urls()

    print(
        f"Premier lot : {len(urls)} actualités détectées."
    )

    for click_number in range(
        1,
        MAX_LOAD_MORE_CLICKS + 1,
    ):
        if len(urls) >= MAX_ARTICLES:
            break

        print(
            f"🔄 Recherche du bouton VOIR PLUS "
            f"({click_number}/{MAX_LOAD_MORE_CLICKS})..."
        )

        buttons = page.get_by_text(
            "VOIR PLUS",
            exact=True,
        )

        clicked = False

        for i in range(buttons.count()):
            try:
                button = buttons.nth(i)

                if not button.is_visible():
                    continue

                button.scroll_into_view_if_needed()
                page.wait_for_timeout(500)
                button.click(timeout=10000)

                clicked = True
                print("🟢 VOIR PLUS cliqué.")
                break

            except Exception:
                pass

        if not clicked:
            print("ℹ️ Plus de bouton VOIR PLUS.")
            break

        page.wait_for_timeout(2500)

        try:
            page.wait_for_load_state(
                "networkidle",
                timeout=10000,
            )
        except PlaywrightTimeoutError:
            pass

        added = collect_visible_urls()

        print(
            f"Actualités actuellement trouvées : "
            f"{len(urls)} (+{added})"
        )

        if added == 0:
            break

    print(
        f"🟢 Total actualités récupérées : {len(urls)}"
    )

    return list(urls)


def extract_article(url, cache, page):
    """
    Extract an article using the rendered Dofus page.
    This replaces the old requests-only extraction which was blocked
    by the site's JavaScript/anti-bot layer.
    """

    html = ""

    try:
        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        page.wait_for_timeout(2500)

        # Give client-side rendering a little time.
        try:
            page.wait_for_load_state(
                "networkidle",
                timeout=10000,
            )
        except PlaywrightTimeoutError:
            pass

        html = page.content()

    except Exception as e:
        print(f"⚠️ Playwright impossible à charger : {e}")

    # If browser extraction failed, keep a requests fallback.
    if not html:
        try:
            session = requests.Session()
            session.headers.update(HEADERS)

            response = session.get(
                url,
                timeout=30,
            )

            response.raise_for_status()
            html = response.text

        except Exception as e:
            print(f"⚠️ Impossible de charger : {e}")
            return None

    soup = BeautifulSoup(html, "html.parser")

    # Title
    title = ""

    h1 = soup.find("h1")

    if h1:
        title = clean_text(
            h1.get_text(" ", strip=True)
        )

    if not title:
        meta = soup.find(
            "meta",
            attrs={"property": "og:title"},
        )

        if meta:
            title = clean_text(
                meta.get("content")
            )

    if not title:
        title = (
            url.rstrip("/")
            .split("/")[-1]
            .replace("-", " ")
            .strip()
            .title()
        )

    # Date
    dt, date_source = extract_date_from_html(html)

    if not dt and url in cache:
        dt = parse_date(
            cache[url].get("pubDate")
        )

        if dt:
            date_source = "cache"

    if not dt:
        print("⚠️ Date introuvable.")
        return None

    # Description
    description = ""

    meta = soup.find(
        "meta",
        attrs={"name": "description"},
    )

    if meta:
        description = clean_text(
            meta.get("content")
        )

    if not description:
        meta = soup.find(
            "meta",
            attrs={"property": "og:description"},
        )

        if meta:
            description = clean_text(
                meta.get("content")
            )

    if not description:
        description = title

    print(f"   📅 Date trouvée via : {date_source}")

    return {
        "title": title,
        "url": url,
        "description": description,
        "date": dt,
    }


def create_rss(
    filename,
    title,
    description,
    articles,
):
    now = formatdate(
        datetime.now(timezone.utc).timestamp(),
        usegmt=True,
    )

    rss = Element(
        "rss",
        {"version": "2.0"},
    )

    channel = SubElement(
        rss,
        "channel",
    )

    SubElement(
        channel,
        "title",
    ).text = title

    SubElement(
        channel,
        "link",
    ).text = SOURCE_URL

    SubElement(
        channel,
        "description",
    ).text = description

    SubElement(
        channel,
        "lastBuildDate",
    ).text = now

    for article in articles:
        item = SubElement(
            channel,
            "item",
        )

        SubElement(
            item,
            "title",
        ).text = article["title"]

        SubElement(
            item,
            "link",
        ).text = article["url"]

        SubElement(
            item,
            "guid",
            {"isPermaLink": "true"},
        ).text = article["url"]

        SubElement(
            item,
            "pubDate",
        ).text = format_pubdate(
            article["date"]
        )

        SubElement(
            item,
            "description",
        ).text = article["description"]

    tree = ElementTree(rss)

    indent(
        tree,
        space="  ",
    )

    tree.write(
        filename,
        encoding="utf-8",
        xml_declaration=True,
    )


cache = load_cache()

print("")
print("========================================")
print("Ouverture avec Playwright :")
print(SOURCE_URL)
print("========================================")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    context = browser.new_context(
        locale="fr-FR",
        user_agent=HEADERS["User-Agent"],
        extra_http_headers={
            "Accept-Language": HEADERS["Accept-Language"],
        },
    )

    list_page = context.new_page()

    all_urls = set(
        collect_news_urls(list_page)
    )

    list_page.close()

    print("")
    print("########################################")
    print(
        f"# URLs Actualités Dofus trouvées : "
        f"{len(all_urls)}"
    )
    print("########################################")

    articles = []

    article_page = context.new_page()

    for index, url in enumerate(
        sorted(all_urls),
        start=1,
    ):
        print("")
        print(
            f"[{index}/{len(all_urls)}] {url}"
        )

        article = extract_article(
            url,
            cache,
            article_page,
        )

        if not article:
            continue

        articles.append(article)

        print(
            f"🟢 {format_pubdate(article['date'])} "
            f"- {article['title']}"
        )

    article_page.close()
    context.close()
    browser.close()


unique = {}

for article in articles:
    url = article["url"]

    if (
        url not in unique
        or article["date"] > unique[url]["date"]
    ):
        unique[url] = article

articles = list(unique.values())

articles.sort(
    key=lambda article: article["date"],
    reverse=True,
)

articles = articles[:MAX_ARTICLES]


print("")
print("########################################")
print(
    f"# {len(articles)} Actualités Dofus retenues"
)
print("########################################")
print("")

for index, article in enumerate(
    articles,
    start=1,
):
    print(
        f"{index:02d}. "
        f"{format_pubdate(article['date'])} "
        f"- {article['title']}"
    )


for article in articles:
    cache[article["url"]] = {
        "title": article["title"],
        "description": article["description"],
        "pubDate": format_pubdate(
            article["date"]
        ),
    }


save_cache(cache)


print("")
print("Génération de dofus-news.xml...")

create_rss(
    OUTPUT,
    "DOFUS — Actualités",
    "Actualités officielles françaises de DOFUS.",
    articles,
)

print("🟢 dofus-news.xml généré.")

print("")
print("Génération de dofus-news-discord.xml...")

create_rss(
    DISCORD_OUTPUT,
    "DOFUS — Actualités",
    "Dernière actualité officielle française de DOFUS.",
    articles[:1],
)

print("🟢 dofus-news-discord.xml généré.")

print("")
print("########################################")
print("# DOFUS ACTUALITÉS RSS TERMINÉ")
print("########################################")
print("")
