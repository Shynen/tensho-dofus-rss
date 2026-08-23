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

SOURCE_URL = (
    "https://www.dofus.com/fr/mmorpg/actualites/news"
)

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


########################################
# MOIS FRANÇAIS
########################################

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


########################################
# OUTILS TEXTE / DATES
########################################

def clean_text(value):
    return re.sub(
        r"\s+",
        " ",
        str(value or "")
    ).strip()


def parse_date(value):

    if not value:
        return None

    try:

        value = clean_text(value)

        if value.endswith("Z"):
            value = value[:-1] + "+00:00"

        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            timezone.utc
        )

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
                FRENCH_MONTHS[
                    match.group(2).lower()
                ],
                int(match.group(1)),
                int(match.group(4) or 0),
                int(match.group(5) or 0),
                tzinfo=timezone.utc,
            )

        except ValueError:
            pass


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
            pass


    return None


def parse_any_date_candidates(values):

    for value in values:

        if not value:
            continue

        dt = (
            parse_date(value)
            or parse_french_date(value)
        )

        if dt is not None:
            return dt

    return None


def format_pubdate(dt):

    return formatdate(
        dt.timestamp(),
        usegmt=True
    )


########################################
# CACHE
########################################

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
            encoding="utf-8"
        ) as f:

            data = json.load(f)


        if not isinstance(data, dict):
            data = {}


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
        encoding="utf-8"
    ) as f:

        json.dump(
            cache,
            f,
            ensure_ascii=False,
            indent=2
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

    urls = {}

    google_rss = (
        "https://news.google.com/rss/search"
        "?q=site%3Adofus.com%2Ffr%2Fmmorpg%2Factualites%2Fnews%2F"
        "&hl=fr&gl=FR&ceid=FR%3Afr"
    )

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
            "xml"
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

                if is_valid_news_url(
                    final_url
                ):

                    urls[final_url] = None

            except Exception as exc:

                print(
                    "⚠️ Impossible de résoudre "
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
            "❌ Google News RSS indisponible : "
            f"{exc}"
        )


    return urls


########################################
# LISTING DOFUS
########################################

def collect_news_listing():

    """
    IMPORTANT :

    Le listing sert UNIQUEMENT à récupérer
    les URLs.

    On NE récupère PLUS la date ici.

    La vraie date sera récupérée directement
    sur chaque page article avec Playwright.
    """

    print("")
    print("========================================")
    print("Ouverture avec Playwright :")
    print(SOURCE_URL)
    print("========================================")

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

            page.wait_for_timeout(5000)


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

                if len(listing) >= LISTING_TARGET:
                    break

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

                    if full_url not in listing:

                        listing[full_url] = None

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


        ########################################
        # VOIR PLUS
        ########################################

        for click_number in range(
            1,
            MAX_LOAD_MORE_CLICKS + 1
        ):

            if len(listing) >= LISTING_TARGET:
                break

            print(
                f"🔄 Recherche du bouton "
                f"VOIR PLUS "
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


            page.wait_for_timeout(
                2500
            )


            try:

                page.wait_for_load_state(
                    "networkidle",
                    timeout=10000,
                )

            except PlaywrightTimeoutError:
                pass


            before = len(listing)

            collect_visible_listing()

            added = (
                len(listing) - before
            )

            print(
                f"Actualités actuellement "
                f"trouvées : "
                f"{len(listing)} "
                f"(+{added})"
            )


            if added == 0:
                break


        browser.close()


    ########################################
    # FALLBACK
    ########################################

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


    print(
        f"🟢 Total actualités récupérées : "
        f"{len(listing)}"
    )

    return listing


########################################
# EXTRACTION DATE HTML FALLBACK
########################################

def extract_date_from_html_soup(soup):

    ########################################
    # JSON-LD
    ########################################

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


            for key in (
                "datePublished",
                "dateCreated",
            ):

                value = obj.get(key)

                dt = (
                    parse_date(value)
                    or parse_french_date(value)
                )

                if dt is not None:

                    return (
                        dt,
                        f"JSON-LD/{key}"
                    )


    ########################################
    # META
    ########################################

    selectors = [

        (
            "property",
            "article:published_time"
        ),

        (
            "property",
            "og:published_time"
        ),

        (
            "name",
            "datePublished"
        ),

        (
            "name",
            "published"
        ),

        (
            "name",
            "date"
        ),

        (
            "property",
            "og:date"
        ),

    ]


    for attr, value in selectors:

        meta = soup.find(
            "meta",
            attrs={
                attr: value
            },
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
                f"META/{value}"
            )


    ########################################
    # TIME
    ########################################

    for node in soup.find_all("time"):

        values = [

            node.get("datetime"),

            node.get("data-date"),

            node.get("data-datetime"),

            node.get_text(
                " ",
                strip=True
            ),

        ]

        dt = parse_any_date_candidates(
            values
        )

        if dt is not None:

            return (
                dt,
                "TIME"
            )


    return None, None


########################################
# EXTRACTION ARTICLE AVEC PLAYWRIGHT
########################################

def extract_article_with_playwright(
    page,
    url
):

    try:

        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        page.wait_for_timeout(
            1800
        )


        ########################################
        # TITRE
        ########################################

        title = ""

        title_selectors = [

            "h1",

            "article h1",

            "main h1",

            '[data-testid="article-title"]',

        ]


        for selector in title_selectors:

            try:

                loc = (
                    page
                    .locator(selector)
                    .first
                )

                if loc.count() == 0:
                    continue

                if not loc.is_visible():
                    continue

                text = clean_text(
                    loc.inner_text(
                        timeout=3000
                    )
                )

                if text and len(text) > 4:

                    title = text

                    break

            except Exception:
                pass


        ########################################
        # FALLBACK OG TITLE
        ########################################

        if not title:

            try:

                title = clean_text(
                    page
                    .locator(
                        'meta[property="og:title"]'
                    )
                    .get_attribute(
                        "content"
                    )
                )

            except Exception:
                pass


        ########################################
        # FALLBACK PAGE TITLE
        ########################################

        if not title:

            try:

                title = clean_text(
                    page.title()
                )

            except Exception:
                pass


        title_source = (
            "PLAYWRIGHT"
            if title
            else None
        )


        ########################################
        # DATE
        ########################################

        article_date = None

        article_date_source = None


        ########################################
        # JSON-LD RENDU
        ########################################

        try:

            scripts = page.locator(
                'script[type="application/ld+json"]'
            ).all_text_contents()


            for raw in scripts:

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

                    if not isinstance(
                        obj,
                        dict
                    ):
                        continue


                    for key in (
                        "datePublished",
                        "dateCreated",
                    ):

                        value = obj.get(
                            key
                        )

                        dt = (
                            parse_date(value)
                            or parse_french_date(value)
                        )


                        if dt:

                            article_date = dt

                            article_date_source = (
                                f"ARTICLE JSON-LD/{key}"
                            )

                            break


                    if article_date:
                        break


                if article_date:
                    break


        except Exception:
            pass


        ########################################
        # META RENDU
        ########################################

        if article_date is None:

            meta_selectors = [

                (
                    'meta[property="article:published_time"]',
                    "ARTICLE META/article:published_time",
                ),

                (
                    'meta[property="og:published_time"]',
                    "ARTICLE META/og:published_time",
                ),

                (
                    'meta[name="datePublished"]',
                    "ARTICLE META/datePublished",
                ),

                (
                    'meta[name="published"]',
                    "ARTICLE META/published",
                ),

                (
                    'meta[name="date"]',
                    "ARTICLE META/date",
                ),

            ]


            for selector, source in meta_selectors:

                try:

                    locator = (
                        page
                        .locator(selector)
                        .first
                    )

                    if locator.count() == 0:
                        continue

                    raw = locator.get_attribute(
                        "content"
                    )

                    dt = (
                        parse_date(raw)
                        or parse_french_date(raw)
                    )

                    if dt:

                        article_date = dt

                        article_date_source = source

                        break

                except Exception:
                    pass


        ########################################
        # TIME RENDU
        ########################################

        if article_date is None:

            try:

                times = page.locator(
                    "time"
                )

                for i in range(
                    times.count()
                ):

                    node = times.nth(i)

                    values = [

                        node.get_attribute(
                            "datetime"
                        ),

                        node.get_attribute(
                            "data-date"
                        ),

                        node.get_attribute(
                            "data-datetime"
                        ),

                        node.inner_text(
                            timeout=2000
                        ),

                    ]


                    dt = parse_any_date_candidates(
                        values
                    )


                    if dt:

                        article_date = dt

                        article_date_source = (
                            "ARTICLE TIME"
                        )

                        break


            except Exception:
                pass


        ########################################
        # TEXTE ARTICLE
        ########################################

        if article_date is None:

            try:

                body_text = (
                    page
                    .locator("body")
                    .inner_text(
                        timeout=5000
                    )
                )

                dt = parse_french_date(
                    body_text
                )

                if dt:

                    article_date = dt

                    article_date_source = (
                        "ARTICLE TEXT"
                    )

            except Exception:
                pass


        ########################################
        # DESCRIPTION
        ########################################

        description = ""


        try:

            description = clean_text(
                page
                .locator(
                    'meta[name="description"]'
                )
                .get_attribute(
                    "content"
                )
            )

        except Exception:
            pass


        if not description:

            try:

                description = clean_text(
                    page
                    .locator(
                        'meta[property="og:description"]'
                    )
                    .get_attribute(
                        "content"
                    )
                )

            except Exception:
                pass


        if not description:
            description = title


        ########################################
        # VALIDATION
        ########################################

        if not title:

            print(
                "   ⚠️ Titre introuvable."
            )

            return None


        if article_date is None:

            print(
                "   ⚠️ Date article introuvable."
            )

            return None


        return {

            "title": title,

            "title_source": title_source,

            "url": url,

            "description": description,

            "date": article_date,

            "date_source": article_date_source,

        }


    except Exception as exc:

        print(
            f"⚠️ Impossible de charger "
            f"l'article : {exc}"
        )

        return None


########################################
# FALLBACK REQUESTS
########################################

def extract_article_requests_fallback(
    url
):

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30,
        )

        response.raise_for_status()


        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )


        ########################################
        # TITRE
        ########################################

        title = ""

        h1 = soup.find("h1")

        if h1:

            title = clean_text(
                h1.get_text(
                    " ",
                    strip=True
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


        ########################################
        # DATE
        ########################################

        article_date, source = (
            extract_date_from_html_soup(
                soup
            )
        )


        if not title:
            return None


        if article_date is None:
            return None


        ########################################
        # DESCRIPTION
        ########################################

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

            description = title


        return {

            "title": title,

            "title_source": "REQUESTS",

            "url": url,

            "description": description,

            "date": article_date,

            "date_source": (
                f"REQUESTS {source}"
            ),

        }


    except Exception:
        return None


########################################
# ARTICLE COMPLET
########################################

def extract_article(
    page,
    url,
    cache
):

    ########################################
    # 1. PLAYWRIGHT
    ########################################

    article = (
        extract_article_with_playwright(
            page,
            url
        )
    )


    if article is not None:
        return article


    ########################################
    # 2. FALLBACK HTTP
    ########################################

    print(
        "   ⚠️ Playwright article incomplet, "
        "tentative fallback HTTP..."
    )


    article = (
        extract_article_requests_fallback(
            url
        )
    )


    if article is not None:
        return article


    ########################################
    # 3. CACHE
    ########################################

    if url in cache:

        cached = cache[url]

        dt = parse_date(
            cached.get(
                "pubDate"
            )
        )


        if dt is not None:

            return {

                "title": clean_text(
                    cached.get(
                        "title"
                    )
                ),

                "title_source": "CACHE",

                "url": url,

                "description": clean_text(
                    cached.get(
                        "description"
                    )
                ),

                "date": dt,

                "date_source": "CACHE",

            }


    return None


########################################
# CREATION RSS
########################################

def create_rss(
    filename,
    title,
    description,
    articles
):

    rss = Element(
        "rss",
        {
            "version": "2.0"
        }
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


    ########################################
    # ITEMS
    ########################################

    for article in articles:

        item = SubElement(
            channel,
            "item"
        )


        SubElement(
            item,
            "title"
        ).text = article[
            "title"
        ]


        SubElement(
            item,
            "link"
        ).text = article[
            "url"
        ]


        SubElement(
            item,
            "guid",
            {
                "isPermaLink": "true"
            }
        ).text = article[
            "url"
        ]


        # IMPORTANT :
        # pubDate est toujours présent.

        SubElement(
            item,
            "pubDate"
        ).text = format_pubdate(
            article["date"]
        )


        SubElement(
            item,
            "description"
        ).text = article[
            "description"
        ]


    tree = ElementTree(
        rss
    )


    indent(
        tree,
        space="  "
    )


    tree.write(
        filename,
        encoding="utf-8",
        xml_declaration=True
    )


########################################
# MAIN
########################################

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


    ########################################
    # CACHE
    ########################################

    cache = load_cache()


    ########################################
    # LISTING
    ########################################

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


    ########################################
    # ARTICLES
    ########################################

    articles = []


    urls = list(
        listing.keys()
    )


    ########################################
    # UN SEUL NAVIGATEUR PLAYWRIGHT
    ########################################

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )


        page = browser.new_page(
            locale="fr-FR",
            user_agent=HEADERS[
                "User-Agent"
            ],
        )


        for index, url in enumerate(
            urls,
            start=1
        ):

            print(
                f"[{index}/{len(urls)}] "
                f"{url}"
            )


            article = extract_article(
                page,
                url,
                cache
            )


            if article is not None:

                articles.append(
                    article
                )


                print(
                    f"   🏷️ Titre trouvé via "
                    f"{article['title_source']}: "
                    f"{article['title']}"
                )


                print(
                    f"   📅 Date trouvée via "
                    f"{article['date_source']}: "
                    f"{format_pubdate(article['date'])}"
                )


                print(
                    f"🟢 "
                    f"{format_pubdate(article['date'])}"
                    f" - "
                    f"{article['title']}"
                )


            else:

                print(
                    "   ❌ Article ignoré : "
                    "titre ou date introuvable."
                )


        browser.close()


    ########################################
    # UNE URL = UN ARTICLE
    ########################################

    unique_articles = {
        article["url"]: article
        for article in articles
    }


    articles = list(
        unique_articles.values()
    )


    ########################################
    # PLUS RÉCENT EN PREMIER
    ########################################

    articles.sort(
        key=lambda article: article["date"],
        reverse=True
    )


    ########################################
    # MAXIMUM 20
    ########################################

    articles = articles[
        :MAX_ARTICLES
    ]


    ########################################
    # AFFICHAGE
    ########################################

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
        start=1
    ):

        print(
            f"{index:02d}. "
            f"{format_pubdate(article['date'])}"
            f" - "
            f"{article['title']}"
        )


    ########################################
    # CACHE
    ########################################

    for article in articles:

        cache[
            article["url"]
        ] = {

            "title": article[
                "title"
            ],

            "description": article[
                "description"
            ],

            "pubDate": format_pubdate(
                article["date"]
            ),

        }


    save_cache(
        cache
    )


    ########################################
    # RSS COMPLET
    ########################################

    print("")

    print(
        "Génération de dofus-news.xml..."
    )


    create_rss(
        OUTPUT,

        "DOFUS — Actualités",

        "Actualités officielles françaises "
        "de DOFUS.",

        articles
    )


    print(
        "🟢 dofus-news.xml généré."
    )


    ########################################
    # RSS DISCORD
    ########################################

    print("")

    print(
        "Génération de "
        "dofus-news-discord.xml..."
    )


    # ====================================================
    # IMPORTANT
    #
    # LE FLUX DISCORD CONTIENT TOUJOURS
    # EXACTEMENT UN SEUL ARTICLE :
    #
    # LE PLUS RÉCENT.
    #
    # Il ne contient jamais les 20 articles
    # du flux principal.
    #
    # Cela limite fortement le risque que
    # Discord récupère plusieurs anciens articles
    # après un timeout ou un retard du workflow.
    # ====================================================

    discord_articles = articles[
        :1
    ]


    create_rss(
        DISCORD_OUTPUT,

        "DOFUS — Actualités",

        "Dernière actualité officielle "
        "française de DOFUS.",

        discord_articles
    )


    print(
        "🟢 dofus-news-discord.xml généré."
    )


    ########################################
    # FIN
    ########################################

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


########################################
# EXECUTION
########################################

if __name__ == "__main__":
    main()
