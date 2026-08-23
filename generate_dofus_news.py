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
LISTING_TARGET = 24
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
        re.IGNORECASE,
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
    return formatdate(
        dt.timestamp(),
        usegmt=True,
    )


def load_cache():
    if not os.path.exists(CACHE_FILE):
        print(
            "Cache Actualités Dofus chargé : "
            "0 articles."
        )
        return {}

    try:
        with open(
            CACHE_FILE,
            "r",
            encoding="utf-8",
        ) as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return {}

        print(
            f"Cache Actualités Dofus chargé : "
            f"{len(data)} articles."
        )

        return data

    except Exception as exc:
        print(
            f"⚠️ Erreur lecture cache : {exc}"
        )
        return {}


def save_cache(cache):
    with open(
        CACHE_FILE,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            cache,
            f,
            ensure_ascii=False,
            indent=2,
        )


def is_valid_news_url(url):
    value = url.lower()

    return (
        "dofus.com" in value
        and "/fr/mmorpg/actualites/news/" in value
        and value.rstrip("/") != SOURCE_URL.rstrip("/")
    )


def collect_news_urls_google_news():
    google_rss = (
        "https://news.google.com/rss/search"
        "?q=site%3Adofus.com%2Ffr%2Fmmorpg%2Factualites%2Fnews%2F"
        "&hl=fr&gl=FR&ceid=FR%3Afr"
    )

    urls = {}

    try:
        print("")
        print(
            "🔎 Fallback Google News RSS..."
        )
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
            f"📰 Google News : "
            f"{len(items)} résultats trouvés."
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
                    urls[final_url] = None

            except Exception as exc:
                print(
                    f"⚠️ Impossible de résoudre "
                    f"le lien Google News : {exc}"
                )

            if len(urls) >= LISTING_TARGET:
                break

        print(
            f"🟢 Google News : "
            f"{len(urls)} URLs Dofus récupérées."
        )

    except Exception as exc:
        print(
            f"❌ Google News RSS indisponible : "
            f"{exc}"
        )

    return urls


def extract_date_from_listing_link(link):
    """
    Cherche la date directement dans la carte
    de l'actualité sur le listing Dofus.

    On teste plusieurs niveaux de parents car
    la structure HTML peut changer légèrement.
    """

    for level in range(1, 9):

        try:
            parent = link.locator(
                "xpath=" + "/.." * level
            )

            if parent.count() == 0:
                continue

            # -------------------------------------------------
            # 1. Recherche d'une balise <time>
            # -------------------------------------------------

            times = parent.locator("time")

            for j in range(times.count()):

                try:
                    node = times.nth(j)

                    raw = node.get_attribute(
                        "datetime"
                    )

                    dt = (
                        parse_date(raw)
                        or parse_french_date(raw)
                    )

                    if dt is None:
                        dt = parse_french_date(
                            node.inner_text(
                                timeout=1500
                            )
                        )

                    if dt is not None:
                        return dt

                except Exception:
                    pass

            # -------------------------------------------------
            # 2. Recherche dans le texte du parent
            # -------------------------------------------------

            try:
                text = clean_text(
                    parent.inner_text(
                        timeout=1500
                    )
                )

            except Exception:
                text = ""

            dt = parse_french_date(text)

            if dt is not None:
                return dt

        except Exception:
            pass

    return None


def collect_news_listing():
    """
    Récupère les URLs ET leurs dates depuis
    la page officielle du listing Dofus.

    Le dictionnaire conserve l'ordre d'apparition.
    """

    print("")
    print(
        "========================================"
    )
    print(
        "Ouverture avec Playwright :"
    )
    print(SOURCE_URL)
    print(
        "========================================"
    )

    listing = {}

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
                f"❌ Erreur ouverture page : "
                f"{exc}"
            )

            browser.close()

            return {}

        def collect_visible_listing():

            links = page.locator(
                'a[href*="/fr/mmorpg/actualites/news/"]'
            )

            for i in range(links.count()):

                try:
                    link = links.nth(i)

                    href = link.get_attribute(
                        "href"
                    )

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

                    if not is_valid_news_url(
                        full_url
                    ):
                        continue

                    if full_url in listing:
                        continue

                    listing[full_url] = (
                        extract_date_from_listing_link(
                            link
                        )
                    )

                    if len(listing) >= LISTING_TARGET:
                        break

                except Exception:
                    pass

        links = page.locator(
            'a[href*="/fr/mmorpg/actualites/news/"]'
        )

        initial_count = links.count()

        collect_visible_listing()

        print(
            f"Premier lot : "
            f"{initial_count} actualités détectées."
        )

        dates_found = sum(
            1
            for value in listing.values()
            if value is not None
        )

        print(
            f"📅 Dates trouvées dans la liste : "
            f"{dates_found}/{len(listing)}"
        )

        # -----------------------------------------------------
        # VOIR PLUS si nécessaire
        # -----------------------------------------------------

        for click_number in range(
            1,
            MAX_LOAD_MORE_CLICKS + 1,
        ):

            if len(listing) >= LISTING_TARGET:
                break

            print(
                f"🔄 Recherche du bouton VOIR PLUS "
                f"({click_number}/"
                f"{MAX_LOAD_MORE_CLICKS})..."
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

            before = len(listing)

            collect_visible_listing()

            added = len(listing) - before

            print(
                f"Actualités actuellement trouvées : "
                f"{len(listing)} (+{added})"
            )

            if added == 0:
                break

        browser.close()

    # ---------------------------------------------------------
    # Fallback Google News
    # ---------------------------------------------------------

    if len(listing) == 0:

        print("")

        print(
            "⚠️ Aucune actualité trouvée "
            "directement."
        )

        print(
            "➡️ Activation du fallback "
            "Google News..."
        )

        listing = (
            collect_news_urls_google_news()
        )

    dates_found = sum(
        1
        for value in listing.values()
        if value is not None
    )

    print(
        f"📅 Dates trouvées dans le listing : "
        f"{dates_found}/{len(listing)}"
    )

    print(
        f"🟢 Total actualités récupérées : "
        f"{len(listing)}"
    )

    return listing


def extract_date_from_soup(soup):
    # ---------------------------------------------------------
    # 1. JSON-LD
    # ---------------------------------------------------------

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

        if data is None:
            continue

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

                    dt = (
                        parse_date(value)
                        or parse_french_date(value)
                    )

                    if dt is not None:
                        return (
                            dt,
                            f"JSON-LD/{key}",
                        )

    # ---------------------------------------------------------
    # 2. META
    # ---------------------------------------------------------

    selectors = [
        (
            "property",
            "article:published_time",
        ),
        (
            "property",
            "og:published_time",
        ),
        (
            "name",
            "date",
        ),
        (
            "name",
            "published",
        ),
        (
            "name",
            "datePublished",
        ),
        (
            "property",
            "og:date",
        ),
    ]

    for attr, value in selectors:

        meta = soup.find(
            "meta",
            attrs={attr: value},
        )

        if not meta:
            continue

        raw_value = meta.get(
            "content"
        )

        dt = (
            parse_date(raw_value)
            or parse_french_date(raw_value)
        )

        if dt is not None:
            return (
                dt,
                f"META/{value}",
            )

    # ---------------------------------------------------------
    # 3. TIME
    # ---------------------------------------------------------

    for node in soup.find_all("time"):

        raw_value = node.get(
            "datetime"
        )

        dt = (
            parse_date(raw_value)
            or parse_french_date(raw_value)
        )

        if dt is None:
            dt = parse_french_date(
                node.get_text(
                    " ",
                    strip=True,
                )
            )

        if dt is not None:
            return (
                dt,
                "TIME",
            )

    # ---------------------------------------------------------
    # 4. TEXTE VISIBLE
    # ---------------------------------------------------------

    visible_text = soup.get_text(
        " ",
        strip=True,
    )

    pattern = (
        r"\b\d{1,2}\s+"
        r"(?:janvier|février|fevrier|mars|avril|mai|juin|juillet|"
        r"août|aout|septembre|octobre|novembre|décembre|decembre)"
        r"\s+\d{4}"
        r"(?:\s+(?:à|a|at)\s+\d{1,2}"
        r"(?::|h)\d{2})?"
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


def extract_article(
    url,
    cache,
    listing_date,
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

    # ---------------------------------------------------------
    # TITRE
    # ---------------------------------------------------------

    title = ""

    h1 = soup.find("h1")

    if h1:
        title = clean_text(
            h1.get_text(
                " ",
                strip=True,
            )
        )

    if not title:

        meta = soup.find(
            "meta",
            attrs={
                "property": "og:title"
            },
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

    # ---------------------------------------------------------
    # DATE DE LA PAGE ARTICLE
    # ---------------------------------------------------------

    article_date, article_date_source = (
        extract_date_from_soup(soup)
    )

    # ---------------------------------------------------------
    # PRIORITÉ ABSOLUE AU LISTING
    # ---------------------------------------------------------

    if listing_date is not None:

        if (
            article_date is not None
            and article_date.date()
            == listing_date.date()
        ):
            # Même jour :
            # on garde l'heure précise de l'article.

            dt = article_date

            date_source = (
                "LISTING + heure ARTICLE"
            )

        else:
            # Date différente :
            # le listing officiel est prioritaire.

            dt = listing_date

            date_source = "LISTING"

    # ---------------------------------------------------------
    # FALLBACK ARTICLE
    # ---------------------------------------------------------

    elif article_date is not None:

        dt = article_date

        date_source = article_date_source

    # ---------------------------------------------------------
    # FALLBACK CACHE
    # ---------------------------------------------------------

    elif url in cache:

        dt = parse_date(
            cache[url].get(
                "pubDate"
            )
        )

        if dt is not None:
            date_source = "CACHE"
        else:
            date_source = None

    else:

        dt = None
        date_source = None

    if dt is None:

        print(
            "⚠️ Date introuvable."
        )

        return None

    # ---------------------------------------------------------
    # DESCRIPTION
    # ---------------------------------------------------------

    description = ""

    meta = soup.find(
        "meta",
        attrs={
            "name": "description"
        },
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
        f"   🏷️ Titre trouvé via ARTICLE: "
        f"{title}"
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

        SubElement(
            item,
            "pubDate",
        ).text = format_pubdate(
            article["date"]
        )

        SubElement(
            item,
            "description",
        ).text = article[
            "description"
        ]

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


def main():
    print("")

    print(
        "########################################"
    )
    print(
        "# Tensho Dofus"
    )
    print(
        "# ACTUALITÉS FRANÇAISES"
    )
    print(
        "########################################"
    )

    cache = load_cache()

    listing = collect_news_listing()

    print("")

    print(
        "########################################"
    )

    print(
        f"# URLs Actualités Dofus trouvées : "
        f"{len(listing)}"
    )

    print(
        "########################################"
    )

    articles = []

    urls = list(
        listing.keys()
    )

    for index, url in enumerate(
        urls,
        start=1,
    ):

        print(
            f"[{index}/{len(urls)}] "
            f"{url}"
        )

        listing_date = listing.get(
            url
        )

        if listing_date is not None:

            print(
                f"   📅 Date trouvée via LISTING: "
                f"{format_pubdate(listing_date)}"
            )

        article = extract_article(
            url,
            cache,
            listing_date,
        )

        if article is not None:

            articles.append(
                article
            )

            print(
                f"🟢 "
                f"{format_pubdate(article['date'])} "
                f"- "
                f"{article['title']}"
            )

    # ---------------------------------------------------------
    # UNE URL = UN ARTICLE
    # ---------------------------------------------------------

    unique_articles = {
        article["url"]: article
        for article in articles
    }

    articles = list(
        unique_articles.values()
    )

    # Plus récent en premier.
    articles.sort(
        key=lambda article: article["date"],
        reverse=True,
    )

    # Maximum 20 articles.
    articles = articles[
        :MAX_ARTICLES
    ]

    print("")

    print(
        "########################################"
    )

    print(
        f"# {len(articles)} "
        f"Actualités Dofus retenues"
    )

    print(
        "########################################"
    )

    for index, article in enumerate(
        articles,
        start=1,
    ):

        print(
            f"{index:02d}. "
            f"{format_pubdate(article['date'])} "
            f"- "
            f"{article['title']}"
        )

    # ---------------------------------------------------------
    # CACHE
    # ---------------------------------------------------------

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

    # ---------------------------------------------------------
    # RSS COMPLET
    # ---------------------------------------------------------

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

    # ---------------------------------------------------------
    # RSS DISCORD
    # ---------------------------------------------------------

    print("")

    print(
        "Génération de "
        "dofus-news-discord.xml..."
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

    print("")

    print(
        "########################################"
    )

    print(
        "# DOFUS ACTUALITÉS RSS TERMINÉ"
    )

    print(
        "########################################"
    )


if __name__ == "__main__":
    main()
