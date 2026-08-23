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


print("")
print("########################################")
print("# Tensho Dofus")
print("# ACTUALITÉS FRANÇAISES")
print("########################################")
print("")


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_date(value):
    if not value:
        return None

    try:
        value = clean_text(value)

        if value.endswith("Z"):
            value = value[:-1] + "+00:00"

        dt = datetime.fromisoformat(value)

        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt

    except Exception:
        return None


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

    # Exclut la page d'index elle-même
    if value.rstrip("/") == SOURCE_URL.rstrip("/"):
        return False

    return True


def collect_news_urls():

    print("")
    print("========================================")
    print("Ouverture avec Playwright :")
    print(SOURCE_URL)
    print("========================================")

    urls = set()

    with sync_playwright() as p:

        browser = p.chromium.launch(headless=True)

        page = browser.new_page(
            locale="fr-FR",
            user_agent=HEADERS["User-Agent"]
        )

        try:
            page.goto(
                SOURCE_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )

            page.wait_for_timeout(4000)

        except Exception as e:
            print(f"❌ Erreur ouverture page : {e}")
            browser.close()
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

                    full_url = urljoin(
                        BASE_URL,
                        href
                        ).split("#", 1)[0].rstrip("/")

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
            MAX_LOAD_MORE_CLICKS + 1
        ):

            if len(urls) >= MAX_ARTICLES:
                break

            print(
                f"🔄 Recherche du bouton VOIR PLUS "
                f"({click_number}/{MAX_LOAD_MORE_CLICKS})..."
            )

            buttons = page.get_by_text(
                "VOIR PLUS",
                exact=True
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
                    timeout=10000
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

        browser.close()

    print(
        f"🟢 Total actualités récupérées : {len(urls)}"
    )

    return list(urls)


def extract_article(url, cache):
    session = requests.Session()
    session.headers.update(HEADERS)

    soup = None

    # -------------------------------------------------
    # 1. Tentative classique avec requests
    # -------------------------------------------------

    try:
        response = session.get(
            url,
            timeout=30
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

    except Exception as e:
        print(f"⚠️ Requests impossible : {e}")

    # -------------------------------------------------
    # 2. Extraction du titre
    # -------------------------------------------------

    title = ""

    if soup:
        h1 = soup.find("h1")

        if h1:
            title = clean_text(
                h1.get_text(" ", strip=True)
            )

        if not title:
            meta = soup.find(
                "meta",
                attrs={"property": "og:title"}
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

    # -------------------------------------------------
    # 3. Recherche de la date dans le HTML
    # -------------------------------------------------

    dt = None

    if soup:

        # JSON-LD
        for script in soup.find_all(
            "script",
            type="application/ld+json"
        ):
            raw = script.string or script.get_text()

            if not raw:
                continue

            # Recherche large de plusieurs noms possibles
            for field in [
                "datePublished",
                "dateCreated",
                "dateModified",
                "published",
                "publicationDate",
                "publishDate"
            ]:

                pattern = (
                    rf'"{field}"\s*:\s*"([^"]+)"'
                )

                for value in re.findall(
                    pattern,
                    raw,
                    flags=re.IGNORECASE
                ):
                    dt = parse_date(value)

                    if dt:
                        break

                if dt:
                    break

            if dt:
                break

        # Meta tags
        if not dt:

            meta_selectors = [
                {"property": "article:published_time"},
                {"property": "og:published_time"},
                {"property": "article:modified_time"},
                {"name": "date"},
                {"name": "publishdate"},
                {"name": "publication_date"},
                {"name": "published"},
            ]

            for attrs in meta_selectors:

                meta = soup.find(
                    "meta",
                    attrs=attrs
                )

                if not meta:
                    continue

                for attr in [
                    "content",
                    "value"
                ]:
                    value = meta.get(attr)

                    if not value:
                        continue

                    dt = parse_date(value)

                    if dt:
                        break

                if dt:
                    break

        # Balises <time>
        if not dt:

            for node in soup.find_all("time"):

                candidates = [
                    node.get("datetime"),
                    node.get("data-datetime"),
                    node.get("data-date"),
                    node.get("data-time"),
                ]

                for value in candidates:

                    dt = parse_date(value)

                    if dt:
                        break

                if dt:
                    break

    # -------------------------------------------------
    # 4. FALLBACK PLAYWRIGHT
    # -------------------------------------------------

    if not dt:

        print("🔄 Date absente avec requests → Playwright...")

        try:

            with sync_playwright() as p:

                browser = p.chromium.launch(
                    headless=True
                )

                page = browser.new_page(
                    locale="fr-FR",
                    user_agent=HEADERS["User-Agent"]
                )

                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=60000
                )

                page.wait_for_timeout(2500)

                html = page.content()

                browser.close()

            soup_pw = BeautifulSoup(
                html,
                "html.parser"
            )

            # -----------------------------
            # JSON-LD Playwright
            # -----------------------------

            for script in soup_pw.find_all(
                "script",
                type="application/ld+json"
            ):

                raw = script.string or script.get_text()

                if not raw:
                    continue

                for field in [
                    "datePublished",
                    "dateCreated",
                    "dateModified",
                    "published",
                    "publicationDate",
                    "publishDate"
                ]:

                    pattern = (
                        rf'"{field}"\s*:\s*"([^"]+)"'
                    )

                    for value in re.findall(
                        pattern,
                        raw,
                        flags=re.IGNORECASE
                    ):

                        dt = parse_date(value)

                        if dt:
                            break

                    if dt:
                        break

                if dt:
                    break

            # -----------------------------
            # Meta Playwright
            # -----------------------------

            if not dt:

                for attrs in [
                    {"property": "article:published_time"},
                    {"property": "og:published_time"},
                    {"property": "article:modified_time"},
                    {"name": "date"},
                    {"name": "publishdate"},
                    {"name": "publication_date"},
                ]:

                    meta = soup_pw.find(
                        "meta",
                        attrs=attrs
                    )

                    if not meta:
                        continue

                    for attr in [
                        "content",
                        "value"
                    ]:

                        value = meta.get(attr)

                        if not value:
                            continue

                        dt = parse_date(value)

                        if dt:
                            break

                    if dt:
                        break

            # -----------------------------
            # <time> Playwright
            # -----------------------------

            if not dt:

                for node in soup_pw.find_all("time"):

                    for value in [
                        node.get("datetime"),
                        node.get("data-datetime"),
                        node.get("data-date"),
                        node.get("data-time"),
                    ]:

                        dt = parse_date(value)

                        if dt:
                            break

                    if dt:
                        break

            # Titre rendu par Playwright
            if not title:

                h1 = soup_pw.find("h1")

                if h1:
                    title = clean_text(
                        h1.get_text(
                            " ",
                            strip=True
                        )
                    )

        except Exception as e:

            print(
                f"⚠️ Playwright article impossible : {e}"
            )

    # -------------------------------------------------
    # 5. Cache
    # -------------------------------------------------

    if not dt and url in cache:

        dt = parse_date(
            cache[url].get("pubDate")
        )

    # -------------------------------------------------
    # 6. Impossible de trouver la date
    # -------------------------------------------------

    if not dt:

        print(
            "❌ Date introuvable après toutes les méthodes."
        )

        return None

    # -------------------------------------------------
    # 7. Description
    # -------------------------------------------------

    description = ""

    if soup:

        meta = soup.find(
            "meta",
            attrs={"name": "description"}
        )

        if meta:

            description = clean_text(
                meta.get("content")
            )

        if not description:

            meta = soup.find(
                "meta",
                attrs={
                    "property": "og:description"
                }
            )

            if meta:

                description = clean_text(
                    meta.get("content")
                )

    if not description:

        description = title

    # -------------------------------------------------
    # 8. Article final
    # -------------------------------------------------

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
    articles
):

    now = formatdate(
        datetime.now(timezone.utc).timestamp(),
        usegmt=True
    )

    rss = Element(
        "rss",
        {"version": "2.0"}
    )

    channel = SubElement(
        rss,
        "channel"
    )

    SubElement(
        channel,
        "title"
    ).text = title

    SubElement(
        channel,
        "link"
    ).text = SOURCE_URL

    SubElement(
        channel,
        "description"
    ).text = description

    SubElement(
        channel,
        "lastBuildDate"
    ).text = now

    for article in articles:

        item = SubElement(
            channel,
            "item"
        )

        SubElement(
            item,
            "title"
        ).text = article["title"]

        SubElement(
            item,
            "link"
        ).text = article["url"]

        SubElement(
            item,
            "guid",
            {"isPermaLink": "true"}
        ).text = article["url"]

        SubElement(
            item,
            "pubDate"
        ).text = format_pubdate(
            article["date"]
        )

        SubElement(
            item,
            "description"
        ).text = article["description"]

    tree = ElementTree(rss)

    indent(
        tree,
        space="  "
    )

    tree.write(
        filename,
        encoding="utf-8",
        xml_declaration=True
    )


cache = load_cache()

all_urls = set(
    collect_news_urls()
)

print("")
print("########################################")
print(
    f"# URLs Actualités Dofus trouvées : "
    f"{len(all_urls)}"
)
print("########################################")

articles = []

for index, url in enumerate(
    all_urls,
    start=1
):

    print("")
    print(
        f"[{index}/{len(all_urls)}] {url}"
    )

    article = extract_article(
        url,
        cache
    )

    if not article:
        continue

    articles.append(article)

    print(
        f"🟢 {format_pubdate(article['date'])} "
        f"- {article['title']}"
    )


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
    reverse=True
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
    start=1
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
    articles
)

print("🟢 dofus-news.xml généré.")

print("")
print(
    "Génération de dofus-news-discord.xml..."
)

create_rss(
    DISCORD_OUTPUT,
    "DOFUS — Actualités",
    "Dernière actualité officielle française de DOFUS.",
    articles[:1]
)

print("🟢 dofus-news-discord.xml généré.")

print("")
print("########################################")
print("# DOFUS ACTUALITÉS RSS TERMINÉ")
print("########################################")
print("")
