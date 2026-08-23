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
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

FRENCH_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3,
    "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
    "août": 8, "aout": 8, "septembre": 9, "octobre": 10,
    "novembre": 11, "décembre": 12, "decembre": 12,
}


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
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def parse_french_date(value):
    if not value:
        return None

    text = clean_text(value).lower()

    match = re.search(
        r"\b(\d{1,2})\s+"
        r"(janvier|février|fevrier|mars|avril|mai|juin|juillet|"
        r"août|aout|septembre|octobre|novembre|décembre|decembre)"
        r"\s+(\d{4})"
        r"(?:\s+(?:à|a|at)\s+(\d{1,2})(?::|h)(\d{2}))?",
        text,
        flags=re.IGNORECASE,
    )

    if match:
        try:
            return datetime(
                int(match.group(3)),
                FRENCH_MONTHS[match.group(2)],
                int(match.group(1)),
                int(match.group(4) or 0),
                int(match.group(5) or 0),
                tzinfo=timezone.utc,
            )
        except ValueError:
            return None

    match = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", text)
    if match:
        try:
            return datetime(
                int(match.group(3)),
                int(match.group(2)),
                int(match.group(1)),
                tzinfo=timezone.utc,
            )
        except ValueError:
            return None

    return None


def format_pubdate(dt):
    return formatdate(dt.timestamp(), usegmt=True)


def load_cache():
    if not os.path.exists(CACHE_FILE):
        print("Cache Actualités Dofus chargé : 0 articles.")
        return {}

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        print(f"Cache Actualités Dofus chargé : {len(data)} articles.")
        return data
    except Exception as exc:
        print(f"⚠️ Erreur lecture cache : {exc}")
        return {}


def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def is_valid_news_url(url):
    value = url.lower()
    return (
        "dofus.com" in value
        and "/fr/mmorpg/actualites/news/" in value
        and value.rstrip("/") != SOURCE_URL.rstrip("/")
    )
def collect_news_urls_google_news():
    """
    Fallback lorsque Dofus bloque le rendu de la page des actualités.
    Utilise Google News RSS pour retrouver les dernières URLs
    officielles Dofus.
    """
    google_rss = (
        "https://news.google.com/rss/search"
        "?q=site%3Adofus.com%2Ffr%2Fmmorpg%2Factualites%2Fnews%2F"
        "&hl=fr&gl=FR&ceid=FR%3Afr"
    )

    urls = set()

    try:
        print("")
        print("🔎 Fallback Google News RSS...")
        print(google_rss)

        response = requests.get(
            google_rss,
            headers=HEADERS,
            timeout=30,
        )
        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "xml",
        )

        items = soup.find_all("item")

        print(
            f"📰 Google News : {len(items)} résultats trouvés."
        )

        for item in items:
            link_node = item.find("link")

            if not link_node:
                continue

            google_url = clean_text(
                link_node.get_text()
            )

            if not google_url:
                continue

            try:
                article_response = requests.get(
                    google_url,
                    headers=HEADERS,
                    timeout=20,
                    allow_redirects=True,
                )

                final_url = (
                    article_response.url
                    .split("#", 1)[0]
                    .rstrip("/")
                )

                if is_valid_news_url(final_url):
                    urls.add(final_url)

            except Exception as e:
                print(
                    f"⚠️ Impossible de résoudre "
                    f"le lien Google News : {e}"
                )

            if len(urls) >= MAX_ARTICLES:
                break

        print(
            f"🟢 Google News : "
            f"{len(urls)} URLs Dofus récupérées."
        )

    except Exception as e:
        print(
            f"❌ Google News RSS indisponible : {e}"
        )

    return list(urls)

def collect_news_urls():
    print("")
    print("========================================")
    print("Ouverture avec Playwright :")
    print(SOURCE_URL)
    print("========================================")

    # URL -> date trouvée directement sur la page de listing.
    news_data = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            locale="fr-FR",
            user_agent=HEADERS["User-Agent"],
        )

        try:
            page.goto(
                SOURCE_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(4000)
        except Exception as exc:
            print(f"❌ Erreur ouverture page : {exc}")
            browser.close()
            return {}

        def collect_visible_urls():
            before = len(news_data)
            links = page.locator(
                'a[href*="/fr/mmorpg/actualites/news/"]'
            )

            for i in range(links.count()):
                try:
                    link = links.nth(i)
                    href = link.get_attribute("href")

                    if not href:
                        continue

                    full_url = urljoin(BASE_URL, href)
                    full_url = (
                        full_url.split("#", 1)[0].rstrip("/")
                    )

                    if not is_valid_news_url(full_url):
                        continue

                    # La page de listing contient normalement la date
                    # dans la carte qui englobe le lien. On remonte
                    # quelques niveaux et utilise le premier texte
                    # contenant une date française exploitable.
                    listing_date = None

                    for level in range(1, 7):
                        try:
                            parent = link.locator(
                                "xpath=" + "/.." * level
                            )
                            card_text = parent.inner_text(
                                timeout=2000
                            )

                            listing_date = parse_french_date(
                                card_text
                            )

                            if listing_date is not None:
                                break

                        except Exception:
                            continue

                    # On conserve l'URL même si la date n'est pas
                    # trouvée : extract_article pourra encore tenter
                    # la page individuelle puis le cache.
                    if full_url not in news_data:
                        news_data[full_url] = listing_date
                    elif (
                        news_data[full_url] is None
                        and listing_date is not None
                    ):
                        news_data[full_url] = listing_date

                except Exception:
                    pass

            return len(news_data) - before

        collect_visible_urls()

        dated = sum(
            1
            for value in news_data.values()
            if value is not None
        )

        print(
            f"Premier lot : {len(news_data)} actualités détectées."
        )
        print(
            f"📅 Dates trouvées dans la liste : "
            f"{dated}/{len(news_data)}"
        )

        for click_number in range(
            1,
            MAX_LOAD_MORE_CLICKS + 1,
        ):
            if len(news_data) >= MAX_ARTICLES:
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

            dated = sum(
                1
                for value in news_data.values()
                if value is not None
            )

            print(
                f"Actualités actuellement trouvées : "
                f"{len(news_data)} (+{added})"
            )
            print(
                f"📅 Dates trouvées dans la liste : "
                f"{dated}/{len(news_data)}"
            )

            if added == 0:
                break

        browser.close()

    # Fallback si Dofus bloque la récupération directe.
    if len(news_data) == 0:
        print("")
        print(
            "⚠️ Aucune actualité trouvée directement."
        )
        print(
            "➡️ Activation du fallback Google News..."
        )

        fallback_urls = collect_news_urls_google_news()

        for fallback_url in fallback_urls:
            news_data[fallback_url] = None

    print(
        f"🟢 Total actualités récupérées : "
        f"{len(news_data)}"
    )

    return news_data


def extract_date_from_soup(soup):
    # 1. JSON-LD, sans syntaxe conditionnelle ambiguë.
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()

        if not raw:
            continue

        # JSON-LD valide.
        try:
            data = json.loads(raw)
        except Exception:
            data = None

        if data is not None:
            objects = data if isinstance(data, list) else [data]

            for obj in objects:
                if not isinstance(obj, dict):
                    continue

                for key in ("datePublished", "dateCreated", "dateModified"):
                    value = obj.get(key)

                    if value:
                        dt = parse_date(value)
                        if dt is None:
                            dt = parse_french_date(value)

                        if dt is not None:
                            return dt, f"JSON-LD/{key}"

    # 2. Meta tags.
    selectors = [
        ("property", "article:published_time"),
        ("property", "og:published_time"),
        ("name", "date"),
        ("name", "published"),
        ("name", "datePublished"),
        ("property", "og:date"),
    ]

    for attr, value in selectors:
        meta = soup.find("meta", attrs={attr: value})
        if meta:
            raw_value = meta.get("content")
            dt = parse_date(raw_value)
            if dt is None:
                dt = parse_french_date(raw_value)
            if dt is not None:
                return dt, f"META/{value}"

    # 3. <time>.
    for node in soup.find_all("time"):
        raw_value = node.get("datetime")
        dt = parse_date(raw_value)
        if dt is None:
            dt = parse_french_date(raw_value)

        if dt is None:
            visible = node.get_text(" ", strip=True)
            dt = parse_french_date(visible)

        if dt is not None:
            return dt, "TIME"

    # 4. Texte visible.
    visible_text = soup.get_text(" ", strip=True)

    pattern = (
        r"\b\d{1,2}\s+"
        r"(?:janvier|février|fevrier|mars|avril|mai|juin|juillet|"
        r"août|aout|septembre|octobre|novembre|décembre|decembre)"
        r"\s+\d{4}"
        r"(?:\s+(?:à|a|at)\s+\d{1,2}(?::|h)\d{2})?"
    )

    for match in re.finditer(
        pattern,
        visible_text,
        flags=re.IGNORECASE,
    ):
        dt = parse_french_date(match.group(0))
        if dt is not None:
            return dt, "VISIBLE-TEXT"

    return None, None


def extract_article(url, cache, listing_date=None):
    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as exc:
        print(f"⚠️ Impossible de charger : {exc}")
        return None

    title = ""

    h1 = soup.find("h1")
    if h1:
        title = clean_text(h1.get_text(" ", strip=True))

    if not title:
        meta = soup.find("meta", attrs={"property": "og:title"})
        if meta:
            title = clean_text(meta.get("content"))

    if not title:
        title = (
            url.rstrip("/")
            .split("/")[-1]
            .replace("-", " ")
            .strip()
            .title()
        )

    dt, date_source = extract_date_from_soup(soup)

    # Les pages individuelles peuvent parfois masquer leur date.
    # La page de listing officielle fournit déjà cette date.
    if dt is None and listing_date is not None:
        dt = listing_date
        date_source = "LISTING"

    # Cache seulement en dernier recours.
    if dt is None and url in cache:
        cached_date = cache[url].get("pubDate")
        dt = parse_date(cached_date)
        if dt is not None:
            date_source = "CACHE"

    if dt is None:
        print("⚠️ Date introuvable.")
        return None

    description = ""

    meta = soup.find("meta", attrs={"name": "description"})
    if meta:
        description = clean_text(meta.get("content"))

    if not description:
        meta = soup.find("meta", attrs={"property": "og:description"})
        if meta:
            description = clean_text(meta.get("content"))

    if not description:
        description = title

    print(
        f"   📅 Date trouvée via {date_source}: "
        f"{format_pubdate(dt)}"
    )

    return {
        "title": title,
        "url": url,
        "description": description,
        "date": dt,
    }


def create_rss(filename, title, description, articles):
    rss = Element("rss", {"version": "2.0"})
    channel = SubElement(rss, "channel")

    SubElement(channel, "title").text = title
    SubElement(channel, "link").text = SOURCE_URL
    SubElement(channel, "description").text = description

    for article in articles:
        item = SubElement(channel, "item")

        SubElement(item, "title").text = article["title"]
        SubElement(item, "link").text = article["url"]
        SubElement(
            item,
            "guid",
            {"isPermaLink": "true"},
        ).text = article["url"]
        SubElement(item, "pubDate").text = format_pubdate(article["date"])
        SubElement(item, "description").text = article["description"]

    tree = ElementTree(rss)
    indent(tree, space="  ")
    tree.write(
        filename,
        encoding="utf-8",
        xml_declaration=True,
    )


print("")
print("########################################")
print("# Tensho Dofus")
print("# ACTUALITÉS FRANÇAISES")
print("########################################")

cache = load_cache()
news_data = collect_news_urls()

print("")
print("########################################")
print(
    f"# URLs Actualités Dofus trouvées : {len(news_data)}"
)
print("########################################")

articles = []

for index, (url, listing_date) in enumerate(
    news_data.items(),
    start=1,
):
    print(f"[{index}/{len(news_data)}] {url}")

    article = extract_article(
        url,
        cache,
        listing_date=listing_date,
    )

    if article is not None:
        articles.append(article)
        print(
            f"🟢 {format_pubdate(article['date'])} "
            f"- {article['title']}"
        )

# Une URL = un article.
unique_articles = {}
for article in articles:
    unique_articles[article["url"]] = article

articles = list(unique_articles.values())
articles.sort(
    key=lambda article: article["date"],
    reverse=True,
)
articles = articles[:MAX_ARTICLES]

print("")
print("########################################")
print(f"# {len(articles)} Actualités Dofus retenues")
print("########################################")

for index, article in enumerate(articles, start=1):
    print(
        f"{index:02d}. "
        f"{format_pubdate(article['date'])} "
        f"- {article['title']}"
    )

for article in articles:
    cache[article["url"]] = {
        "title": article["title"],
        "description": article["description"],
        "pubDate": format_pubdate(article["date"]),
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
