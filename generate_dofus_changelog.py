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
SOURCE_URL = "https://www.dofus.com/fr/mmorpg/actualites/maj"

OUTPUT = "dofus-changelog.xml"
DISCORD_OUTPUT = "dofus-changelog-discord.xml"
CACHE_FILE = "dofus_changelog_cache.json"
DISCORD_STATE_FILE = "dofus_changelog_discord_state.json"

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


########################################
# OUTILS
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

    text = clean_text(
        value
    ).lower()

    # 18/08/2026 - 17h00
    match = re.search(
        r"\b(\d{1,2})/(\d{1,2})/(\d{4})"
        r"(?:\s*[-–—]\s*(\d{1,2})h(?:(\d{2}))?)?",
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

    # 18 août 2026 - 17h00
    match = re.search(
        r"\b(\d{1,2})\s+"
        r"(janvier|février|fevrier|mars|avril|mai|juin|juillet|"
        r"août|aout|septembre|octobre|novembre|décembre|decembre)"
        r"\s+(\d{4})"
        r"(?:\s*[-–—]\s*(\d{1,2})h(?:(\d{2}))?)?",
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

    return None


def parse_any_date(values):

    for value in values:

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

    if not os.path.exists(
        CACHE_FILE
    ):

        print(
            "Aucun cache Changelog Dofus trouvé."
        )

        return {}

    try:

        with open(
            CACHE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if not isinstance(
            data,
            dict
        ):

            return {}

        print(
            f"Cache Changelog Dofus chargé : "
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

    if not os.path.exists(
        DISCORD_STATE_FILE
    ):

        return {}

    try:

        with open(
            DISCORD_STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        return (
            data
            if isinstance(data, dict)
            else {}
        )

    except Exception as exc:

        print(
            f"⚠️ Erreur lecture état Discord : {exc}"
        )

        return {}


def save_discord_state(state):

    temp_file = (
        f"{DISCORD_STATE_FILE}.tmp"
    )

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )

        f.flush()

        try:

            os.fsync(
                f.fileno()
            )

        except Exception:

            pass

    os.replace(
        temp_file,
        DISCORD_STATE_FILE
    )


########################################
# URL
########################################

def is_valid_url(url):

    value = url.lower()

    return (
        "dofus.com" in value
        and "/fr/mmorpg/actualites/maj/" in value
        and value.rstrip("/")
        != SOURCE_URL.rstrip("/")
    )


########################################
# RÉCUPÉRATION LISTING
#
# On récupère aussi le texte autour
# du lien afin de récupérer la date
# directement depuis la carte.
########################################

def collect_changelog_entries():

    print("")

    print(
        "========================================"
    )

    print(
        "Ouverture avec Playwright :"
    )

    print(
        SOURCE_URL
    )

    print(
        "========================================"
    )

    entries = {}

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

        try:

            page.goto(
                SOURCE_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            page.wait_for_timeout(
                4000
            )

        except Exception as exc:

            print(
                f"❌ Erreur ouverture page : {exc}"
            )

            browser.close()

            return {}

        def collect_visible():

            before = len(entries)

            links = page.locator(
                'a[href*="/fr/mmorpg/actualites/maj/"]'
            )

            for i in range(
                links.count()
            ):

                try:

                    link = links.nth(i)

                    href = link.get_attribute(
                        "href"
                    )

                    if not href:
                        continue

                    full_url = urljoin(
                        BASE_URL,
                        href
                    )

                    full_url = (
                        full_url
                        .split("#", 1)[0]
                        .rstrip("/")
                    )

                    if not is_valid_url(
                        full_url
                    ):
                        continue

                    card_text = ""

                    for level in range(
                        1,
                        6
                    ):

                        try:

                            ancestor = link

                            for _ in range(level):

                                ancestor = (
                                    ancestor.locator(
                                        ".."
                                    )
                                )

                            text = clean_text(
                                ancestor.inner_text(
                                    timeout=2000
                                )
                            )

                            if len(text) >= 20:

                                card_text = text

                                if parse_french_date(
                                    text
                                ):

                                    break

                        except Exception:

                            pass

                    listing_date = (
                        parse_french_date(
                            card_text
                        )
                    )

                    listing_title = ""

                    try:

                        listing_title = clean_text(
                            link.inner_text(
                                timeout=2000
                            )
                        )

                    except Exception:

                        pass

                    if full_url not in entries:

                        entries[
                            full_url
                        ] = {

                            "url": full_url,

                            "listing_date":
                                listing_date,

                            "listing_title":
                                listing_title,

                            "listing_text":
                                card_text,

                        }

                    else:

                        if (
                            entries[
                                full_url
                            ][
                                "listing_date"
                            ] is None
                            and listing_date is not None
                        ):

                            entries[
                                full_url
                            ][
                                "listing_date"
                            ] = listing_date

                except Exception:

                    pass

            return (
                len(entries) - before
            )

        collect_visible()

        print(
            f"Premier lot : "
            f"{len(entries)} mises à jour détectées."
        )

        for click_number in range(
            1,
            MAX_LOAD_MORE_CLICKS + 1
        ):

            if len(entries) >= MAX_ARTICLES:

                break

            print(
                f"🔄 Recherche du bouton VOIR PLUS "
                f"({click_number}/"
                f"{MAX_LOAD_MORE_CLICKS})..."
            )

            buttons = page.get_by_text(
                "VOIR PLUS",
                exact=True
            )

            clicked = False

            for i in range(
                buttons.count()
            ):

                try:

                    button = buttons.nth(i)

                    if not button.is_visible():
                        continue

                    button.scroll_into_view_if_needed()

                    page.wait_for_timeout(
                        300
                    )

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
                    timeout=10000
                )

            except PlaywrightTimeoutError:

                pass

            added = collect_visible()

            print(
                f"Mises à jour actuellement "
                f"trouvées : "
                f"{len(entries)} (+{added})"
            )

            if added == 0:

                break

        browser.close()

    print(
        f"🟢 Total mises à jour récupérées : "
        f"{len(entries)}"
    )

    return entries


########################################
# EXTRACTION DATE ARTICLE PLAYWRIGHT
########################################

def extract_date_from_page(page):

    ########################################
    # 1. EN-TÊTE / H1
    ########################################

    try:

        h1 = page.locator(
            "h1"
        ).first

        if h1.count() > 0:

            for level in range(
                0,
                6
            ):

                try:

                    node = h1

                    for _ in range(level):

                        node = node.locator(
                            ".."
                        )

                    text = clean_text(
                        node.inner_text(
                            timeout=3000
                        )
                    )

                    dt = parse_french_date(
                        text
                    )

                    if dt is not None:

                        return (
                            dt,
                            "ARTICLE HEADER"
                        )

                except Exception:

                    pass

    except Exception:

        pass

    ########################################
    # 2. JSON-LD
    ########################################

    try:

        scripts = page.locator(
            'script[type="application/ld+json"]'
        ).all_text_contents()

        for raw in scripts:

            try:

                data = json.loads(
                    raw
                )

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
                    "dateModified",
                ):

                    dt = parse_date(
                        obj.get(key)
                    )

                    if dt is not None:

                        return (
                            dt,
                            f"JSON-LD/{key}"
                        )

                graph = obj.get(
                    "@graph"
                )

                if isinstance(
                    graph,
                    list
                ):

                    for child in graph:

                        if not isinstance(
                            child,
                            dict
                        ):

                            continue

                        for key in (
                            "datePublished",
                            "dateCreated",
                            "dateModified",
                        ):

                            dt = parse_date(
                                child.get(key)
                            )

                            if dt is not None:

                                return (
                                    dt,
                                    f"JSON-LD/{key}"
                                )

    except Exception:

        pass

    ########################################
    # 3. META
    ########################################

    selectors = [

        (
            'meta[property="article:published_time"]',
            "META/article:published_time"
        ),

        (
            'meta[property="og:published_time"]',
            "META/og:published_time"
        ),

        (
            'meta[name="datePublished"]',
            "META/datePublished"
        ),

        (
            'meta[name="published"]',
            "META/published"
        ),

        (
            'meta[name="date"]',
            "META/date"
        ),

    ]

    for selector, source in selectors:

        try:

            locator = (
                page
                .locator(selector)
                .first
            )

            if locator.count() == 0:
                continue

            value = locator.get_attribute(
                "content"
            )

            dt = (
                parse_date(value)
                or parse_french_date(value)
            )

            if dt is not None:

                return (
                    dt,
                    source
                )

        except Exception:

            pass

    ########################################
    # 4. TIME
    ########################################

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

            dt = parse_any_date(
                values
            )

            if dt is not None:

                return (
                    dt,
                    "TIME"
                )

    except Exception:

        pass

    return None, None


########################################
# ARTICLE COMPLET
########################################

def extract_article(
    page,
    entry,
    cache
):

    url = entry[
        "url"
    ]

    print(
        "   🔎 Ouverture article "
        "avec Playwright..."
    )

    try:

        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        page.wait_for_timeout(
            1800
        )

    except Exception as exc:

        print(
            f"   ⚠️ Playwright : {exc}"
        )

    ########################################
    # TITRE
    ########################################

    title = ""

    try:

        h1 = page.locator(
            "h1"
        ).first

        if h1.count() > 0:

            title = clean_text(
                h1.inner_text(
                    timeout=3000
                )
            )

    except Exception:

        pass

    if not title:

        try:

            title = clean_text(
                page.title()
            )

        except Exception:

            pass

    if not title:

        title = entry.get(
            "listing_title",
            ""
        )

    if not title:

        title = (
            url
            .rstrip("/")
            .split("/")[-1]
            .replace("-", " ")
            .strip()
            .title()
        )

    ########################################
    # DATE ARTICLE
    ########################################

    article_date, date_source = (
        extract_date_from_page(
            page
        )
    )

    ########################################
    # FALLBACK LISTING
    ########################################

    if article_date is None:

        article_date = entry.get(
            "listing_date"
        )

        if article_date is not None:

            date_source = "LISTING"

    ########################################
    # FALLBACK CACHE
    ########################################

    if article_date is None:

        if url in cache:

            article_date = parse_date(
                cache[url].get(
                    "pubDate"
                )
            )

            if article_date is not None:

                date_source = "CACHE"

    ########################################
    # AUCUNE DATE
    ########################################

    if article_date is None:

        print(
            "   ⚠️ Date introuvable."
        )

        return None

    ########################################
    # DESCRIPTION
    ########################################

    description = ""

    try:

        locator = (
            page
            .locator(
                'meta[name="description"]'
            )
            .first
        )

        if locator.count() > 0:

            description = clean_text(
                locator.get_attribute(
                    "content"
                )
            )

    except Exception:

        pass

    if not description:

        try:

            locator = (
                page
                .locator(
                    'meta[property="og:description"]'
                )
                .first
            )

            if locator.count() > 0:

                description = clean_text(
                    locator.get_attribute(
                        "content"
                    )
                )

        except Exception:

            pass

    if not description:

        description = title

    print(
        f"   🏷️ Titre : {title}"
    )

    print(
        f"   📅 Date trouvée via "
        f"{date_source}: "
        f"{format_pubdate(article_date)}"
    )

    return {

        "title":
            title,

        "url":
            url,

        "description":
            description,

        "date":
            article_date,

    }


########################################
# RSS
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

    SubElement(
        channel,
        "lastBuildDate"
    ).text = formatdate(
        datetime.now(
            timezone.utc
        ).timestamp(),
        usegmt=True
    )

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

def main():

    print("")

    print(
        "########################################"
    )

    print(
        "# Tensho Dofus"
    )

    print(
        "# CHANGELOGS / MISES À JOUR"
    )

    print(
        "########################################"
    )

    print("")

    ########################################
    # CACHE
    ########################################

    cache = load_cache()

    ########################################
    # DISCORD STATE
    ########################################

    discord_state = (
        load_discord_state()
    )

    ########################################
    # LISTING
    ########################################

    entries = (
        collect_changelog_entries()
    )

    print("")

    print(
        "########################################"
    )

    print(
        f"# URLs Changelogs Dofus trouvées : "
        f"{len(entries)}"
    )

    print(
        "########################################"
    )

    ########################################
    # ARTICLES
    ########################################

    articles = []

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

        for index, entry in enumerate(
            entries.values(),
            start=1
        ):

            print("")

            print(
                f"[{index}/{len(entries)}] "
                f"{entry['url']}"
            )

            if entry.get(
                "listing_date"
            ) is not None:

                print(
                    "   📅 Date trouvée "
                    "dans le listing : "
                    f"{format_pubdate(entry['listing_date'])}"
                )

            article = extract_article(
                page,
                entry,
                cache
            )

            if article is None:

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

        browser.close()

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
    # TRI
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
        f"Changelogs Dofus retenus"
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
        "Génération de dofus-changelog.xml..."
    )

    create_rss(
        OUTPUT,

        "DOFUS — Changelogs",

        "Notes de mise à jour officielles "
        "françaises de DOFUS.",

        articles
    )

    print(
        "🟢 dofus-changelog.xml généré."
    )

    ########################################
    # RSS DISCORD
    ########################################

    print("")

    print(
        "Génération de "
        "dofus-changelog-discord.xml..."
    )

    discord_articles = []

    last_sent_url = (
        discord_state.get(
            "last_sent_url"
        )
    )

    ########################################
    # DERNIER CHANGELOG UNIQUEMENT
    ########################################

    if articles:

        latest_article = articles[0]

        latest_url = (
            latest_article["url"]
        )

        print("")

        print(
            "🔎 Dernier changelog "
            "actuellement publié sur DOFUS :"
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
        # DÉJÀ ENVOYÉ
        ########################################

        if latest_url == last_sent_url:

            print("")

            print(
                "ℹ️ Le dernier changelog DOFUS "
                "a déjà été envoyé."
            )

            print(
                "ℹ️ Aucun nouvel envoi Discord."
            )

        ########################################
        # NOUVEAU
        ########################################

        else:

            print("")

            print(
                "🆕 Nouveau changelog "
                "à envoyer sur Discord."
            )

            discord_articles = [
                latest_article
            ]

            ########################################
            # SAUVEGARDE IMMÉDIATE
            ########################################

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

        print(
            "⚠️ Aucun changelog disponible."
        )

    ########################################
    # SI PAS DE NOUVEAU
    #
    # On conserve le dernier article
    # déjà envoyé.
    #
    # On ne cherche JAMAIS un ancien
    # changelog à rattraper.
    ########################################

    if not discord_articles:

        state = load_discord_state()

        previous_url = (
            state.get(
                "last_sent_url"
            )
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
                        (
                            state.get(
                                "last_sent_title"
                            )
                            or
                            "Changelog DOFUS"
                        ),

                    "url":
                        previous_url,

                    "description":
                        (
                            state.get(
                                "last_sent_description"
                            )
                            or
                            "Note de mise à jour "
                            "officielle DOFUS."
                        ),

                    "date":
                        previous_date,

                }

            ]

    ########################################
    # GÉNÉRATION FLUX DISCORD
    ########################################

    create_rss(
        DISCORD_OUTPUT,

        "DOFUS — Changelogs",

        "Dernière note de mise à jour "
        "officielle française de DOFUS.",

        discord_articles
    )

    ########################################
    # LOG FINAL
    ########################################

    if (
        discord_articles
        and articles
        and discord_articles[0]["url"]
        == articles[0]["url"]
        and articles[0]["url"]
        != last_sent_url
    ):

        print(
            "🟢 dofus-changelog-discord.xml "
            "généré avec 1 nouveau changelog."
        )

    elif discord_articles:

        print(
            "🟢 dofus-changelog-discord.xml "
            "généré sans nouvel envoi."
        )

    else:

        print(
            "🟢 dofus-changelog-discord.xml "
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
        "# DOFUS CHANGELOG RSS TERMINÉ"
    )

    print(
        "########################################"
    )

    print("")


########################################
# EXECUTION
########################################

if __name__ == "__main__":
    main()
