import json
import os
import re
import requests

from bs4 import BeautifulSoup
from datetime import datetime, timezone
from email.utils import formatdate
from urllib.parse import urljoin
from xml.etree.ElementTree import Element, SubElement, ElementTree, indent

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)


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

DISCORD_STATE_FILE = "dofus_discord_state.json"

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
        pass

    try:

        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(value)

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
        r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})"
        r"(?:\s*(?:-|–|—)?\s*(\d{1,2})(?::|h)(\d{2}))?\b",
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
# ÉTAT DISCORD
########################################

def load_discord_state():

    if not os.path.exists(DISCORD_STATE_FILE):
        return {}

    try:

        with open(
            DISCORD_STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if not isinstance(data, dict):
            return {}

        return data

    except Exception as exc:

        print(
            f"⚠️ Erreur lecture état Discord : {exc}"
        )

        return {}


def save_discord_state(state):

    with open(
        DISCORD_STATE_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            state,
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
# LISTING DOFUS
########################################

def collect_news_listing():

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
                f"❌ Erreur ouverture page : {exc}"
            )

            browser.close()
            return {}

        def extract_listing_entry(link):

            href = link.get_attribute("href")

            if not href:
                return None

            full_url = (
                urljoin(BASE_URL, href)
                .split("#", 1)[0]
                .rstrip("/")
            )

            if not is_valid_news_url(full_url):
                return None

            title = clean_text(
                link.inner_text(timeout=2000)
            )

            listing_date = None
            card_text = ""

            for level in range(1, 7):

                try:

                    node = link

                    for _ in range(level):
                        node = node.locator("..")

                    text = clean_text(
                        node.inner_text(timeout=2000)
                    )

                    if len(text) > len(card_text):
                        card_text = text

                    candidate_date = parse_french_date(text)

                    if candidate_date is not None:

                        listing_date = candidate_date

                        for selector in (
                            "h1",
                            "h2",
                            "h3",
                            "h4"
                        ):

                            try:

                                headings = node.locator(
                                    selector
                                )

                                for j in range(
                                    headings.count()
                                ):

                                    candidate_title = clean_text(
                                        headings.nth(j).inner_text(
                                            timeout=1000
                                        )
                                    )

                                    if candidate_title:

                                        title = (
                                            candidate_title
                                        )

                                        break

                                if title:
                                    break

                            except Exception:
                                pass

                        break

                except Exception:
                    pass

            if (
                not title
                or title.lower().startswith("http")
                or title.lower().startswith("loading")
            ):

                for level in range(1, 7):

                    try:

                        node = link

                        for _ in range(level):
                            node = node.locator("..")

                        for selector in (
                            "h1",
                            "h2",
                            "h3",
                            "h4"
                        ):

                            headings = node.locator(
                                selector
                            )

                            for j in range(
                                headings.count()
                            ):

                                candidate_title = clean_text(
                                    headings.nth(j).inner_text(
                                        timeout=1000
                                    )
                                )

                                if candidate_title:

                                    title = (
                                        candidate_title
                                    )

                                    break

                            if title:
                                break

                        if title:
                            break

                    except Exception:
                        pass

            if listing_date is None:

                listing_date = parse_french_date(
                    card_text
                )

            if not title:
                return None

            return {
                "title": title,
                "url": full_url,
                "date": listing_date,
            }

        def collect_visible():

            links = page.locator(
                'a[href*="/fr/mmorpg/actualites/news/"]'
            )

            before = len(listing)

            for i in range(
                links.count()
            ):

                try:

                    entry = extract_listing_entry(
                        links.nth(i)
                    )

                    if not entry:
                        continue

                    url = entry["url"]

                    if (
                        url not in listing
                        or (
                            entry["date"]
                            and (
                                listing[url]["date"]
                                is None
                                or entry["date"]
                                > listing[url]["date"]
                            )
                        )
                    ):

                        listing[url] = entry

                except Exception:
                    pass

            return len(listing) - before

        collect_visible()

        print(
            f"Premier lot : "
            f"{len(listing)} actualités détectées."
        )

        for click_number in range(
            1,
            MAX_LOAD_MORE_CLICKS + 1
        ):

            if len(listing) >= LISTING_TARGET:
                break

            print(
                f"🔄 Recherche du bouton VOIR PLUS "
                f"({click_number}/{MAX_LOAD_MORE_CLICKS})..."
            )

            button = page.get_by_text(
                "VOIR PLUS",
                exact=True
            )

            if button.count() == 0:

                button = page.get_by_text(
                    "Voir plus",
                    exact=True
                )

            if button.count() == 0:

                print(
                    "ℹ️ Plus de bouton VOIR PLUS."
                )

                break

            try:

                button.last.scroll_into_view_if_needed(
                    timeout=3000
                )

                button.last.click(
                    timeout=5000
                )

                page.wait_for_timeout(
                    2500
                )

                added = collect_visible()

                print(
                    f"   ➕ {added} nouvelles actualités."
                )

                if added == 0:
                    break

            except Exception as exc:

                print(
                    f"⚠️ Impossible de cliquer "
                    f"sur VOIR PLUS : {exc}"
                )

                break

        browser.close()

    return listing


########################################
# EXTRACTION ARTICLE
########################################

def extract_date_from_html(html):

    if not html:
        return None, "aucune donnée"

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    ########################################
    # JSON-LD
    ########################################

    for script in soup.find_all(
        "script",
        type="application/ld+json"
    ):

        raw = script.string or script.get_text(
            strip=True
        )

        if not raw:
            continue

        try:

            data = json.loads(raw)

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
                    "dateModified"
                ):

                    value = obj.get(key)

                    if not value:
                        continue

                    dt = (
                        parse_date(value)
                        or parse_french_date(value)
                    )

                    if dt:
                        return (
                            dt,
                            f"JSON-LD/{key}"
                        )

        except Exception:
            pass

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
            "date"
        ),
        (
            "name",
            "published"
        ),
        (
            "name",
            "datePublished"
        ),
    ]

    for attr, value in selectors:

        meta = soup.find(
            "meta",
            attrs={
                attr: value
            }
        )

        if meta:

            raw = meta.get(
                "content"
            )

            dt = (
                parse_date(raw)
                or parse_french_date(raw)
            )

            if dt:
                return (
                    dt,
                    f"meta/{attr}={value}"
                )

    ########################################
    # TIME
    ########################################

    for node in soup.find_all(
        "time"
    ):

        raw = node.get(
            "datetime"
        )

        dt = (
            parse_date(raw)
            or parse_french_date(raw)
        )

        if dt:
            return (
                dt,
                "time/datetime"
            )

        visible = node.get_text(
            " ",
            strip=True
        )

        dt = (
            parse_french_date(visible)
            or parse_date(visible)
        )

        if dt:
            return (
                dt,
                "time/text"
            )

    ########################################
    # TEXTE VISIBLE
    ########################################

    body = soup.find(
        "body"
    )

    if body:

        visible_text = clean_text(
            body.get_text(
                " ",
                strip=True
            )
        )

        patterns = [
            r"(?:publié|publication|mis à jour|actualit[ée])"
            r"[^.]{0,120}",

            r"(?:le|du)\s+\d{1,2}\s+"
            r"(?:janvier|février|fevrier|mars|avril|mai|juin|"
            r"juillet|août|aout|septembre|octobre|novembre|"
            r"décembre|decembre)"
            r"\s+\d{4}[^.]{0,80}",
        ]

        for pattern in patterns:

            for chunk in re.findall(
                pattern,
                visible_text,
                flags=re.IGNORECASE
            ):

                dt = parse_french_date(
                    chunk
                )

                if dt:
                    return (
                        dt,
                        "texte publication"
                    )

        dt = parse_french_date(
            visible_text
        )

        if dt:
            return (
                dt,
                "texte visible"
            )

    ########################################
    # HTML BRUT
    ########################################

    dt = parse_french_date(
        html
    )

    if dt:
        return (
            dt,
            "HTML brut"
        )

    return (
        None,
        "introuvable"
    )


def extract_article(
    url,
    listing_entry,
    cache
):

    print(
        "   🔎 Ouverture article avec Playwright..."
    )

    html = ""

    title = clean_text(
        listing_entry.get(
            "title"
        )
    )

    listing_date = listing_entry.get(
        "date"
    )

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
                url,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            page.wait_for_timeout(
                2500
            )

            try:

                page.wait_for_load_state(
                    "networkidle",
                    timeout=10000
                )

            except PlaywrightTimeoutError:
                pass

            html = page.content()

        except Exception as exc:

            print(
                f"⚠️ Playwright impossible à charger : "
                f"{exc}"
            )

        finally:

            browser.close()

    ########################################
    # FALLBACK REQUESTS
    ########################################

    if not html:

        try:

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30
            )

            response.raise_for_status()

            html = response.text

        except Exception as exc:

            print(
                f"⚠️ Impossible de charger l'article : "
                f"{exc}"
            )

            html = ""

    ########################################
    # PARSING
    ########################################

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    ########################################
    # TITRE
    #
    # Le listing reste prioritaire.
    ########################################

    if not title:

        h1 = soup.find(
            "h1"
        )

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
            }
        )

        if meta:

            title = clean_text(
                meta.get(
                    "content"
                )
            )

    if not title:

        title = (
            url.rstrip("/")
            .split("/")[-1]
            .replace("-", " ")
            .strip()
            .title()
        )

    ########################################
    # DATE
    #
    # IMPORTANT :
    # La date du listing est prioritaire.
    ########################################

    if listing_date:

        dt = listing_date

        date_source = (
            "LISTING"
        )

    else:

        dt, date_source = (
            extract_date_from_html(
                html
            )
        )

        if not dt and url in cache:

            dt = parse_date(
                cache[url].get(
                    "pubDate"
                )
            )

            if dt:
                date_source = "cache"

    if not dt:

        print(
            "⚠️ Date introuvable."
        )

        return None

    ########################################
    # DESCRIPTION
    ########################################

    description = ""

    meta = soup.find(
        "meta",
        attrs={
            "name": "description"
        }
    )

    if meta:

        description = clean_text(
            meta.get(
                "content"
            )
        )

    if not description:

        meta = soup.find(
            "meta",
            attrs={
                "property":
                "og:description"
            }
        )

        if meta:

            description = clean_text(
                meta.get(
                    "content"
                )
            )

    if not description:

        description = title

    print(
        f"   🏷️ Titre : {title}"
    )

    print(
        f"   📅 Date trouvée via : "
        f"{date_source}"
    )

    return {

        "title": title,

        "url": url,

        "description": description,

        "date": dt,

    }


########################################
# CRÉATION RSS
########################################

def create_rss(
    filename,
    title,
    description,
    articles
):

    now = formatdate(
        datetime.now(
            timezone.utc
        ).timestamp(),
        usegmt=True
    )

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
                "isPermaLink":
                "true"
            }
        ).text = article[
            "url"
        ]

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

cache = load_cache()

discord_state = load_discord_state()

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

########################################
# EXTRACTION
########################################

for index, (
    url,
    listing_entry
) in enumerate(
    listing.items(),
    start=1
):

    print("")
    print(
        f"[{index}/{len(listing)}] "
        f"{url}"
    )

    article = extract_article(
        url,
        listing_entry,
        cache
    )

    if not article:
        continue

    articles.append(
        article
    )

    print(
        f"🟢 "
        f"{format_pubdate(article['date'])} "
        f"- "
        f"{article['title']}"
    )


########################################
# DÉDOUBLONNAGE
########################################

unique = {}

for article in articles:

    url = article[
        "url"
    ]

    if (
        url not in unique
        or article["date"]
        > unique[url]["date"]
    ):

        unique[url] = article


articles = list(
    unique.values()
)


########################################
# TRI DU PLUS RÉCENT AU PLUS ANCIEN
########################################

articles.sort(
    key=lambda article:
    article["date"],
    reverse=True
)


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
print("")

for index, article in enumerate(
    articles,
    start=1
):

    print(
        f"{index:02d}. "
        f"{format_pubdate(article['date'])} "
        f"- "
        f"{article['title']}"
    )


########################################
# CACHE
########################################

for article in articles:

    cache[
        article["url"]
    ] = {

        "title":
        article["title"],

        "description":
        article["description"],

        "pubDate":
        format_pubdate(
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

discord_articles = []

last_sent_url = (
    discord_state.get(
        "last_sent_url"
    )
)


########################################
# DERNIÈRE ACTUALITÉ
########################################

if articles:

    latest_article = articles[0]

    latest_url = latest_article[
        "url"
    ]

    print("")
    print(
        "🔎 Dernière actualité actuellement "
        "publiée sur DOFUS :"
    )

    print(
        f"   {latest_article['title']}"
    )

    print(
        f"   {latest_url}"
    )

    print(
        f"   "
        f"{format_pubdate(latest_article['date'])}"
    )

    ########################################
    # NOUVELLE ACTUALITÉ
    ########################################

    if latest_url != last_sent_url:

        print("")
        print(
            "🆕 Nouvelle actualité Discord."
        )

        discord_articles = [
            latest_article
        ]

        save_discord_state(
            {

                "last_sent_url":
                latest_url,

                "last_sent_pubDate":
                format_pubdate(
                    latest_article[
                        "date"
                    ]
                ),

                "last_sent_title":
                latest_article[
                    "title"
                ],

                "last_sent_description":
                latest_article[
                    "description"
                ],

            }
        )

        print(
            "🟢 État Discord sauvegardé."
        )

    else:

        print("")
        print(
            "ℹ️ La dernière actualité "
            "a déjà été envoyée."
        )


########################################
# CONSERVATION DU DERNIER ITEM
#
# Le flux Discord ne doit JAMAIS être vide.
########################################

if not discord_articles:

    state = load_discord_state()

    previous_url = state.get(
        "last_sent_url"
    )

    previous_date = parse_date(
        state.get(
            "last_sent_pubDate"
        )
    )

    if (
        previous_url
        and previous_date
    ):

        discord_articles = [

            {

                "title":
                state.get(
                    "last_sent_title"
                )
                or "Actualité DOFUS",

                "url":
                previous_url,

                "description":
                state.get(
                    "last_sent_description"
                )
                or
                "Actualité officielle "
                "française de DOFUS.",

                "date":
                previous_date,

            }

        ]


########################################
# GÉNÉRATION FLUX DISCORD
########################################

create_rss(
    DISCORD_OUTPUT,

    "DOFUS — Actualités",

    "Dernière actualité officielle "
    "française de DOFUS.",

    discord_articles[:1]
)


if (
    discord_articles
    and articles
    and discord_articles[0]["url"]
    == articles[0]["url"]
    and articles[0]["url"]
    != last_sent_url
):

    print(
        "🟢 dofus-news-discord.xml "
        "généré avec 1 nouvelle actualité."
    )

else:

    print(
        "🟢 dofus-news-discord.xml "
        "généré sans nouvel envoi."
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
print("")
