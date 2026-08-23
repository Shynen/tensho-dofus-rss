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


########################################
# CONFIGURATION
########################################

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


# Textes qui ne sont JAMAIS de vrais titres d'articles.
GENERIC_TITLES = {
    "actualités",
    "actualités récentes",
    "actualites",
    "actualites recentes",
    "news",
    "en savoir+",
    "en savoir +",
    "en savoir plus",
    "voir plus",
    "lire la suite",
    "lire plus",
}


########################################
# OUTILS TEXTE / DATES
########################################

def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def is_valid_title(value):
    """
    Vérifie qu'un texte ressemble réellement à un titre d'actualité.
    """
    value = clean_text(value)

    if not value:
        return False

    normalized = value.lower().strip()

    if normalized in GENERIC_TITLES:
        return False

    if len(value) < 5 or len(value) > 250:
        return False

    return True


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

    match = re.search(
        r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b",
        text,
    )

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


########################################
# CACHE
########################################

def load_cache():
    if not os.path.exists(CACHE_FILE):
        print("Cache Actualités Dofus chargé : 0 articles.")
        return {}

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return {}

        print(
            f"Cache Actualités Dofus chargé : {len(data)} articles."
        )

        return data

    except Exception as exc:
        print(f"⚠️ Erreur lecture cache : {exc}")
        return {}


def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            cache,
            f,
            ensure_ascii=False,
            indent=2,
        )


########################################
# VALIDATION URL
########################################

def is_valid_news_url(url):
    value = url.lower()

    return (
        "dofus.com" in value
        and "/fr/mmorpg/actualites/news/" in value
        and value.rstrip("/") != SOURCE_URL.rstrip("/")
    )


########################################
# FALLBACK GOOGLE NEWS
########################################

def collect_news_urls_google_news():

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

            except Exception as exc:
                print(
                    "⚠️ Impossible de résoudre "
                    f"le lien Google News : {exc}"
                )

            if len(urls) >= MAX_ARTICLES:
                break

        print(
            f"🟢 Google News : "
            f"{len(urls)} URLs Dofus récupérées."
        )

    except Exception as exc:
        print(
            f"❌ Google News RSS indisponible : {exc}"
        )

    return list(urls)


########################################
# COLLECTE DES URLS DOFUS
########################################

def collect_news_urls():

    print("")
    print("========================================")
    print("Ouverture avec Playwright :")
    print(SOURCE_URL)
    print("========================================")

    # URL -> date trouvée sur le listing
    news_data = {}

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

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
            print(
                f"❌ Erreur ouverture page : {exc}"
            )

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

                    full_url = urljoin(
                        BASE_URL,
                        href,
                    )

                    full_url = (
                        full_url
                        .split("#", 1)[0]
                        .rstrip("/")
                    )

                    if not is_valid_news_url(full_url):
                        continue

                    # Recherche de la date dans la carte.
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

        # Premier passage
        collect_visible_urls()

        dated = sum(
            1
            for value in news_data.values()
            if value is not None
        )

        print(
            f"Premier lot : {len(news_data)} "
            "actualités détectées."
        )

        print(
            f"📅 Dates trouvées dans la liste : "
            f"{dated}/{len(news_data)}"
        )

        # Chargement supplémentaire
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

                    button.click(
                        timeout=10000
                    )

                    clicked = True

                    print(
                        "🟢 VOIR PLUS cliqué."
                    )

                    break

                except Exception:
                    pass

            if not clicked:

                print(
                    "ℹ️ Plus de bouton VOIR PLUS."
                )

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

    # Fallback Google News
    if len(news_data) == 0:

        print("")
        print(
            "⚠️ Aucune actualité trouvée directement."
        )

        print(
            "➡️ Activation du fallback Google News..."
        )

        fallback_urls = (
            collect_news_urls_google_news()
        )

        for fallback_url in fallback_urls:
            news_data[fallback_url] = None

    print(
        f"🟢 Total actualités récupérées : "
        f"{len(news_data)}"
    )

    return news_data


########################################
# EXTRACTION DATE PAGE ARTICLE
########################################

def extract_date_from_soup(soup):

    # 1. JSON-LD
    for script in soup.find_all(
        "script",
        type="application/ld+json",
    ):

        raw = (
            script.string
            or script.get_text()
        )

        if not raw:
            continue

        try:
            data = json.loads(raw)
        except Exception:
            data = None

        if data is not None:

            objects = (
                data
                if isinstance(data, list)
                else [data]
            )

            for obj in objects:

                if not isinstance(obj, dict):
                    continue

                for key in (
                    "datePublished",
                    "dateCreated",
                    "dateModified",
                ):

                    value = obj.get(key)

                    if value:

                        dt = parse_date(value)

                        if dt is None:
                            dt = parse_french_date(
                                value
                            )

                        if dt is not None:
                            return (
                                dt,
                                f"JSON-LD/{key}",
                            )

    # 2. Meta
    selectors = [
        ("property", "article:published_time"),
        ("property", "og:published_time"),
        ("name", "date"),
        ("name", "published"),
        ("name", "datePublished"),
        ("property", "og:date"),
    ]

    for attr, value in selectors:

        meta = soup.find(
            "meta",
            attrs={attr: value},
        )

        if meta:

            raw_value = meta.get(
                "content"
            )

            dt = parse_date(raw_value)

            if dt is None:
                dt = parse_french_date(
                    raw_value
                )

            if dt is not None:
                return (
                    dt,
                    f"META/{value}",
                )

    # 3. <time>
    for node in soup.find_all("time"):

        raw_value = node.get("datetime")

        dt = parse_date(raw_value)

        if dt is None:
            dt = parse_french_date(
                raw_value
            )

        if dt is None:

            visible = node.get_text(
                " ",
                strip=True,
            )

            dt = parse_french_date(
                visible
            )

        if dt is not None:
            return dt, "TIME"

    # 4. Texte visible
    visible_text = soup.get_text(
        " ",
        strip=True,
    )

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

        dt = parse_french_date(
            match.group(0)
        )

        if dt is not None:
            return (
                dt,
                "VISIBLE-TEXT",
            )

    return None, None


########################################
# EXTRACTION DU VRAI TITRE
########################################

def extract_title_from_soup(soup):

    # ------------------------------------------------
    # 1. H1
    # ------------------------------------------------
    # C'est normalement le titre éditorial réel
    # de l'article.
    # ------------------------------------------------

    for h1 in soup.find_all("h1"):

        candidate = clean_text(
            h1.get_text(
                " ",
                strip=True,
            )
        )

        if is_valid_title(candidate):
            return (
                candidate,
                "H1",
            )

    # ------------------------------------------------
    # 2. OpenGraph
    # ------------------------------------------------

    meta = soup.find(
        "meta",
        attrs={"property": "og:title"},
    )

    if meta:

        candidate = clean_text(
            meta.get("content")
        )

        if is_valid_title(candidate):

            # Certains sites ajoutent " | DOFUS".
            candidate = re.sub(
                r"\s*\|\s*DOFUS.*$",
                "",
                candidate,
                flags=re.IGNORECASE,
            )

            candidate = clean_text(
                candidate
            )

            if is_valid_title(candidate):
                return (
                    candidate,
                    "OG/TITLE",
                )

    # ------------------------------------------------
    # 3. <title>
    # ------------------------------------------------

    if soup.title:

        candidate = clean_text(
            soup.title.get_text(
                " ",
                strip=True,
            )
        )

        candidate = re.sub(
            r"\s*\|\s*DOFUS.*$",
            "",
            candidate,
            flags=re.IGNORECASE,
        )

        candidate = clean_text(
            candidate
        )

        if is_valid_title(candidate):
            return (
                candidate,
                "HTML/TITLE",
            )

    # ------------------------------------------------
    # 4. JSON-LD
    # ------------------------------------------------

    for script in soup.find_all(
        "script",
        type="application/ld+json",
    ):

        raw = (
            script.string
            or script.get_text()
        )

        if not raw:
            continue

        try:
            data = json.loads(raw)
        except Exception:
            continue

        objects = (
            data
            if isinstance(data, list)
            else [data]
        )

        for obj in objects:

            if not isinstance(obj, dict):
                continue

            candidate = clean_text(
                obj.get("headline")
                or obj.get("name")
                or ""
            )

            if is_valid_title(candidate):
                return (
                    candidate,
                    "JSON-LD",
                )

    return None, None


########################################
# EXTRACTION ARTICLE
########################################

def extract_article(
    url,
    cache,
    listing_date=None,
):

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    try:

        response = session.get(
            url,
            timeout=30,
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

    except Exception as exc:

        print(
            f"⚠️ Impossible de charger : "
            f"{exc}"
        )

        return None

    # ------------------------------------------------
    # TITRE
    # ------------------------------------------------
    #
    # IMPORTANT :
    # On ne récupère PLUS le titre depuis le
    # listing Playwright.
    #
    # Le listing peut contenir "En savoir+" ou
    # "Actualités récentes".
    #
    # On récupère donc le vrai titre depuis la
    # page individuelle.
    # ------------------------------------------------

    title, title_source = (
        extract_title_from_soup(soup)
    )

    # ------------------------------------------------
    # Fallback cache
    # ------------------------------------------------

    if not title and url in cache:

        cached_title = clean_text(
            cache[url].get("title")
        )

        if is_valid_title(
            cached_title
        ):

            title = cached_title
            title_source = "CACHE"

    # ------------------------------------------------
    # Dernier fallback : slug URL
    # ------------------------------------------------

    if not title:

        title = (
            url.rstrip("/")
            .split("/")[-1]
            .replace("-", " ")
            .strip()
            .title()
        )

        title_source = "URL"

    # ------------------------------------------------
    # DATE
    # ------------------------------------------------

    dt, date_source = (
        extract_date_from_soup(soup)
    )

    # Si la page individuelle ne fournit pas
    # la date, on utilise celle du listing.
    if dt is None and listing_date is not None:

        dt = listing_date
        date_source = "LISTING"

    # Dernier recours : cache
    if dt is None and url in cache:

        cached_date = cache[url].get(
            "pubDate"
        )

        dt = parse_date(
            cached_date
        )

        if dt is not None:
            date_source = "CACHE"

    if dt is None:

        print(
            "⚠️ Date introuvable."
        )

        return None

    # ------------------------------------------------
    # DESCRIPTION
    # ------------------------------------------------

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
            attrs={
                "property": "og:description"
            },
        )

        if meta:

            description = clean_text(
                meta.get("content")
            )

    if not description:
        description = title

    print(
        f"   🏷️ Titre trouvé via "
        f"{title_source}: {title}"
    )

    print(
        f"   📅 Date trouvée via "
        f"{date_source}: "
        f"{format_pubdate(dt)}"
    )

    return {
        "title": title,
        "url": url,
        "description": description,
        "date": dt,
    }


########################################
# CREATION RSS
########################################

def create_rss(
    filename,
    title,
    description,
    articles,
):

    rss = Element(
        "rss",
        {
            "version": "2.0"
        },
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
            {
                "isPermaLink": "true"
            },
        ).text = article["url"]

        # IMPORTANT POUR DISCORD :
        # chaque item possède toujours un pubDate valide.
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


########################################
# PROGRAMME PRINCIPAL
########################################

print("")
print("########################################")
print("# Tensho Dofus")
print("# ACTUALITÉS FRANÇAISES")
print("########################################")


# ------------------------------------------------
# CACHE
# ------------------------------------------------

cache = load_cache()


# ------------------------------------------------
# RECUPERATION DES URLS
# ------------------------------------------------

news_data = collect_news_urls()


print("")
print("########################################")
print(
    f"# URLs Actualités Dofus trouvées : "
    f"{len(news_data)}"
)
print("########################################")


# ------------------------------------------------
# EXTRACTION DES ARTICLES
# ------------------------------------------------

articles = []


for index, (
    url,
    listing_date,
) in enumerate(
    news_data.items(),
    start=1,
):

    print(
        f"[{index}/{len(news_data)}] "
        f"{url}"
    )

    article = extract_article(
        url,
        cache,
        listing_date=listing_date,
    )

    if article is not None:

        articles.append(
            article
        )

        print(
            f"🟢 "
            f"{format_pubdate(article['date'])} "
            f"- {article['title']}"
        )


# ------------------------------------------------
# SUPPRESSION DES DOUBLONS
# ------------------------------------------------

unique_articles = {}

for article in articles:

    unique_articles[
        article["url"]
    ] = article


articles = list(
    unique_articles.values()
)


# ------------------------------------------------
# TRI PAR DATE
# ------------------------------------------------

articles.sort(
    key=lambda article: article["date"],
    reverse=True,
)


# ------------------------------------------------
# LIMITATION A 20 ARTICLES
# ------------------------------------------------

articles = articles[
    :MAX_ARTICLES
]


# ------------------------------------------------
# AFFICHAGE FINAL
# ------------------------------------------------

print("")
print("########################################")
print(
    f"# {len(articles)} "
    "Actualités Dofus retenues"
)
print("########################################")


for index, article in enumerate(
    articles,
    start=1,
):

    print(
        f"{index:02d}. "
        f"{format_pubdate(article['date'])} "
        f"- {article['title']}"
    )


# ------------------------------------------------
# MISE A JOUR CACHE
# ------------------------------------------------

for article in articles:

    cache[
        article["url"]
    ] = {
        "title": article["title"],
        "description": article["description"],
        "pubDate": format_pubdate(
            article["date"]
        ),
    }


save_cache(cache)


# ------------------------------------------------
# RSS COMPLET
# ------------------------------------------------

print("")
print(
    "Génération de dofus-news.xml..."
)


create_rss(
    OUTPUT,
    "DOFUS — Actualités",
    "Actualités officielles françaises de DOFUS.",
    articles,
)


print(
    "🟢 dofus-news.xml généré."
)


# ------------------------------------------------
# RSS DISCORD
# ------------------------------------------------
#
# Un seul article.
# RSS 2.0 minimal.
# pubDate toujours présent.
# ------------------------------------------------

print("")
print(
    "Génération de dofus-news-discord.xml..."
)


create_rss(
    DISCORD_OUTPUT,
    "DOFUS — Actualités",
    "Dernière actualité officielle française de DOFUS.",
    articles[:1],
)


print(
    "🟢 dofus-news-discord.xml généré."
)


# ------------------------------------------------
# FIN
# ------------------------------------------------

print("")
print("########################################")
print("# DOFUS ACTUALITÉS RSS TERMINÉ")
print("########################################")
