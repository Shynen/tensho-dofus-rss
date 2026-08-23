import json
import os
import re

from datetime import datetime, timezone
from email.utils import formatdate
from urllib.parse import urljoin
from xml.etree.ElementTree import (
    Element,
    SubElement,
    ElementTree,
    indent,
)

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)


########################################
# CONFIGURATION
########################################

BASE_URL = "https://www.dofus.com"

SOURCE_URL = (
    "https://www.dofus.com/fr/mmorpg/actualites/maj"
)

OUTPUT = "dofus-changelog.xml"

DISCORD_OUTPUT = (
    "dofus-changelog-discord.xml"
)

CACHE_FILE = (
    "dofus_changelog_cache.json"
)

DISCORD_STATE_FILE = (
    "dofus_changelog_discord_state.json"
)

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
            value = (
                value[:-1]
                + "+00:00"
            )

        dt = datetime.fromisoformat(
            value
        )

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

        from email.utils import (
            parsedate_to_datetime
        )

        dt = parsedate_to_datetime(
            value
        )

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

    ########################################
    # 18 août 2026
    ########################################

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

    ########################################
    # 18/08/2026
    ########################################

    match = re.search(
        r"\b(\d{1,2})[/-]"
        r"(\d{1,2})[/-]"
        r"(\d{4})"
        r"(?:\s*(?:-|–|—)?\s*"
        r"(\d{1,2})(?::|h)(\d{2}))?\b",
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


def extract_date_from_url(url):

    if not url:
        return None

    ########################################
    # PATCH NOTES
    #
    # Exemple :
    # patch-notes-3-6-10-10-18-08-2026
    ########################################

    match = re.search(
        r"-(\d{1,2})-(\d{1,2})-(\d{4})(?:/?$)",
        url,
        re.IGNORECASE,
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

    ########################################
    # Variante éventuelle :
    # 18-08-2026 dans l'URL
    ########################################

    match = re.search(
        r"(\d{1,2})-(\d{1,2})-(\d{4})",
        url,
        re.IGNORECASE,
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
            "Cache Changelog Dofus chargé : "
            "0 articles."
        )

        return {}

    try:

        with open(
            CACHE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(
                f
            )

        if not isinstance(
            data,
            dict
        ):

            data = {}

        print(
            f"Cache Changelog Dofus chargé : "
            f"{len(data)} articles."
        )

        return data

    except Exception as exc:

        print(
            f"⚠️ Erreur lecture cache : "
            f"{exc}"
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

            data = json.load(
                f
            )

        if not isinstance(
            data,
            dict
        ):

            return {}

        return data

    except Exception as exc:

        print(
            f"⚠️ Erreur lecture état Discord : "
            f"{exc}"
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

def is_valid_changelog_url(url):

    value = (
        url
        .lower()
        .rstrip("/")
    )

    if (
        "dofus.com" not in value
    ):
        return False

    if (
        "/fr/mmorpg/actualites/maj/"
        not in value
    ):
        return False

    if (
        value
        == SOURCE_URL.lower().rstrip("/")
    ):
        return False

    ########################################
    # EXCLURE LA PAGE INDEX DES CORRECTIFS
    ########################################

    correctifs_index = (
        BASE_URL
        + "/fr/mmorpg/actualites/maj/correctifs"
    ).lower().rstrip("/")

    if value == correctifs_index:
        return False

    return True


########################################
# EXTRACTION TITRE ARTICLE
########################################

def extract_real_article_title(page):

    ########################################
    # 1. H1
    ########################################

    try:

        h1 = (
            page
            .locator("h1")
            .first
        )

        if h1.count() > 0:

            title = clean_text(
                h1.inner_text(
                    timeout=3000
                )
            )

            if (
                title
                and not title.lower().startswith(
                    "loading"
                )
            ):

                return title

    except Exception:
        pass

    ########################################
    # 2. OG TITLE
    ########################################

    try:

        locator = (
            page
            .locator(
                'meta[property="og:title"]'
            )
            .first
        )

        if locator.count() > 0:

            title = clean_text(
                locator.get_attribute(
                    "content"
                )
            )

            if title:
                return title

    except Exception:
        pass

    ########################################
    # 3. TITLE HTML
    ########################################

    try:

        title = clean_text(
            page.title()
        )

        if (
            title
            and not title.lower().startswith(
                "loading"
            )
        ):

            return title

    except Exception:
        pass

    return ""


########################################
# DESCRIPTION ARTICLE
########################################

def extract_article_description(
    page
):

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

    return description


########################################
# LISTING CHANGELOG
########################################

def collect_changelog_listing():

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

    listing = {}

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
                f"❌ Erreur ouverture page : "
                f"{exc}"
            )

            browser.close()

            return {}

        def extract_entry(link):

            try:

                href = link.get_attribute(
                    "href"
                )

                if not href:
                    return None

                full_url = (
                    urljoin(
                        BASE_URL,
                        href
                    )
                    .split(
                        "#",
                        1
                    )[0]
                    .rstrip("/")
                )

                if not is_valid_changelog_url(
                    full_url
                ):
                    return None

                ########################################
                # TITRE DIRECT DU LIEN
                ########################################

                title = clean_text(
                    link.inner_text(
                        timeout=2000
                    )
                )

                listing_date = None
                card_text = ""

                ########################################
                # REMONTER LA CARTE
                ########################################

                for level in range(
                    1,
                    7
                ):

                    try:

                        node = link

                        for _ in range(
                            level
                        ):

                            node = node.locator(
                                ".."
                            )

                        text = clean_text(
                            node.inner_text(
                                timeout=2000
                            )
                        )

                        if len(text) > len(
                            card_text
                        ):

                            card_text = text

                        candidate_date = (
                            parse_french_date(
                                text
                            )
                        )

                        if candidate_date:

                            listing_date = (
                                candidate_date
                            )

                            ########################################
                            # TITRE DE LA CARTE
                            ########################################

                            for selector in (
                                "h1",
                                "h2",
                                "h3",
                                "h4",
                            ):

                                try:

                                    headings = (
                                        node.locator(
                                            selector
                                        )
                                    )

                                    for j in range(
                                        headings.count()
                                    ):

                                        candidate_title = (
                                            clean_text(
                                                headings.nth(
                                                    j
                                                ).inner_text(
                                                    timeout=1000
                                                )
                                            )
                                        )

                                        if (
                                            candidate_title
                                        ):

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

                ########################################
                # FALLBACK TITRE
                ########################################

                if (
                    not title
                    or title.lower().startswith(
                        "loading"
                    )
                    or title.lower().startswith(
                        "http"
                    )
                ):

                    for level in range(
                        1,
                        7
                    ):

                        try:

                            node = link

                            for _ in range(
                                level
                            ):

                                node = node.locator(
                                    ".."
                                )

                            for selector in (
                                "h1",
                                "h2",
                                "h3",
                                "h4",
                            ):

                                headings = (
                                    node.locator(
                                        selector
                                    )
                                )

                                for j in range(
                                    headings.count()
                                ):

                                    candidate_title = (
                                        clean_text(
                                            headings.nth(
                                                j
                                            ).inner_text(
                                                timeout=1000
                                            )
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

                ########################################
                # DATE FALLBACK LISTING
                ########################################

                if listing_date is None:

                    listing_date = (
                        parse_french_date(
                            card_text
                        )
                    )

                if not title:
                    return None

                return {
                    "url":
                        full_url,

                    "title":
                        title,

                    "date":
                        listing_date,
                }

            except Exception:

                return None

        def collect_visible():

            links = page.locator(
                'a[href*="/fr/mmorpg/actualites/maj/"]'
            )

            before = len(
                listing
            )

            for i in range(
                links.count()
            ):

                try:

                    entry = extract_entry(
                        links.nth(i)
                    )

                    if not entry:
                        continue

                    url = entry[
                        "url"
                    ]

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

                        listing[
                            url
                        ] = entry

                except Exception:
                    pass

            return (
                len(listing)
                - before
            )

        ########################################
        # PREMIER LOT
        ########################################

        collect_visible()

        print(
            f"Premier lot : "
            f"{len(listing)} mises à jour détectées."
        )

        ########################################
        # VOIR PLUS
        ########################################

        for click_number in range(
            1,
            MAX_LOAD_MORE_CLICKS + 1
        ):

            print(
                f"🔄 Recherche du bouton VOIR PLUS "
                f"({click_number}/"
                f"{MAX_LOAD_MORE_CLICKS})..."
            )

            buttons = page.get_by_text(
                "VOIR PLUS",
                exact=True
            )

            if buttons.count() == 0:

                buttons = page.get_by_text(
                    "Voir plus",
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

                    button.click(
                        timeout=10000
                    )

                    clicked = True

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
                f"{len(listing)} "
                f"(+{added})"
            )

            if added == 0:
                break

        browser.close()

    print(
        f"🟢 Total mises à jour récupérées : "
        f"{len(listing)}"
    )

    return listing


########################################
# EXTRACTION ARTICLE
########################################

def extract_article(
    page,
    entry,
    cache
):

    url = entry[
        "url"
    ]

    listing_title = clean_text(
        entry.get(
            "title"
        )
    )

    listing_date = entry.get(
        "date"
    )

    print(
        "   🔎 Ouverture article "
        "avec Playwright..."
    )

    description = ""

    try:

        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.wait_for_timeout(
            1500
        )

        description = (
            extract_article_description(
                page
            )
        )

    except Exception as exc:

        print(
            f"   ⚠️ Page article : "
            f"{exc}"
        )

    ########################################
    # TITRE RÉEL
    ########################################
    #
    # IMPORTANT :
    # On cherche maintenant le vrai H1
    # AVANT d'utiliser le titre du listing.
    #
    # Cela permet ensuite d'extraire la date
    # directement depuis le titre réel.
    ########################################

    real_title = (
        extract_real_article_title(
            page
        )
    )

    if real_title:

        title = real_title

    else:

        title = listing_title

    ########################################
    # FALLBACK TITRE
    ########################################

    if not title:

        title = (
            url
            .rstrip("/")
            .split("/")[-1]
            .replace(
                "-",
                " "
            )
            .strip()
            .title()
        )

    ########################################
    # DATE
    ########################################

    article_date = None
    date_source = None

    ########################################
    # 1. PATCH NOTES -> URL
    ########################################
    #
    # Les patch notes possèdent une date
    # explicite dans leur URL.
    #
    # Exemple :
    # ...18-08-2026
    ########################################

    url_date = extract_date_from_url(
        url
    )

    if (
        "/correctifs/" in url
        and url_date
    ):

        article_date = url_date

        date_source = "URL"

    ########################################
    # 2. DATE DANS LE VRAI TITRE
    ########################################
    #
    # Exemple :
    #
    # MÀJ 3.5 - Pas de repos pour les braves
    # 03 Mars 2026
    #
    # => 03/03/2026
    ########################################

    if article_date is None:

        title_date = (
            parse_french_date(
                real_title
            )
            if real_title
            else None
        )

        if title_date:

            article_date = title_date

            date_source = "TITRE"

    ########################################
    # 3. HEADER
    ########################################

    if article_date is None:

        try:

            h1 = (
                page
                .locator(
                    "h1"
                )
                .first
            )

            if h1.count() > 0:

                header_text = clean_text(
                    h1.inner_text(
                        timeout=3000
                    )
                )

                article_date = (
                    parse_french_date(
                        header_text
                    )
                )

                if article_date:

                    date_source = (
                        "ARTICLE HEADER"
                    )

        except Exception:
            pass

    ########################################
    # 4. META
    ########################################

    if article_date is None:

        try:

            selectors = [
                'meta[property="article:published_time"]',
                'meta[property="og:published_time"]',
                'meta[name="datePublished"]',
            ]

            for selector in selectors:

                locator = (
                    page
                    .locator(
                        selector
                    )
                    .first
                )

                if locator.count() == 0:
                    continue

                value = (
                    locator.get_attribute(
                        "content"
                    )
                )

                article_date = (
                    parse_date(value)
                    or
                    parse_french_date(value)
                )

                if article_date:

                    date_source = "META"

                    break

        except Exception:
            pass

    ########################################
    # 5. LISTING
    ########################################
    #
    # IMPORTANT :
    # Le listing n'est PLUS prioritaire.
    #
    # Il sert uniquement de fallback.
    ########################################

    if article_date is None:

        if listing_date:

            article_date = (
                listing_date
            )

            date_source = "LISTING"

    ########################################
    # 6. CACHE
    ########################################

    if article_date is None:

        if url in cache:

            article_date = parse_date(
                cache[url].get(
                    "pubDate"
                )
            )

            if article_date:

                date_source = (
                    "CACHE"
                )

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

    if not description:

        description = title

    ########################################
    # LOG
    ########################################

    title_source = (
        "ARTICLE"
        if real_title
        else "LISTING"
    )

    print(
        f"   🏷️ Titre trouvé via "
        f"{title_source}: "
        f"{title}"
    )

    print(
        f"   📅 Date trouvée via "
        f"{date_source}: "
        f"{format_pubdate(article_date)}"
    )

    ########################################
    # RESULTAT
    ########################################

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
            article[
                "date"
            ]
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
    # ÉTAT DISCORD
    ########################################

    discord_state = (
        load_discord_state()
    )

    ########################################
    # LISTING
    ########################################

    listing = (
        collect_changelog_listing()
    )

    print("")

    print(
        "########################################"
    )

    print(
        f"# URLs Changelogs Dofus trouvées : "
        f"{len(listing)}"
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
            ]
        )

        for index, entry in enumerate(
            listing.values(),
            start=1
        ):

            print("")

            print(
                f"[{index}/{len(listing)}] "
                f"{entry['url']}"
            )

            if entry.get(
                "title"
            ):

                print(
                    f"   🏷️ Titre trouvé dans "
                    f"le listing: "
                    f"{entry['title']}"
                )

            if entry.get(
                "date"
            ):

                print(
                    f"   📅 Date trouvée dans "
                    f"le listing: "
                    f"{format_pubdate(entry['date'])}"
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

    unique_articles = {}

    for article in articles:

        unique_articles[
            article["url"]
        ] = article

    articles = list(
        unique_articles.values()
    )

    ########################################
    # TRI PAR DATE RÉELLE
    ########################################

    articles.sort(
        key=lambda article:
        article["date"],
        reverse=True
    )

    ########################################
    # LIMITATION
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
        f"Changelogs Dofus retenus"
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

        print(
            f"    {article['url']}"
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
    # DERNIER CHANGELOG
    ########################################

    if articles:

        ########################################
        # IMPORTANT :
        # articles est déjà trié par date
        # réelle décroissante.
        #
        # articles[0] = VRAIMENT LE PLUS RÉCENT
        ########################################

        latest_article = articles[0]

        latest_url = (
            latest_article[
                "url"
            ]
        )

        print("")

        print(
            "🔎 Dernier changelog actuellement "
            "publié sur DOFUS :"
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
        # NOUVEAU
        ########################################

        if latest_url != last_sent_url:

            print("")

            print(
                "🆕 Nouveau changelog à envoyer "
                "sur Discord."
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

        ########################################
        # DÉJÀ ENVOYÉ
        ########################################

        else:

            print("")

            print(
                "ℹ️ Le dernier changelog DOFUS "
                "a déjà été envoyé."
            )

    ########################################
    # IMPORTANT :
    #
    # LE FLUX DISCORD NE DOIT JAMAIS
    # ÊTRE VIDE APRÈS LE PREMIER ENVOI.
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
    # TOUJOURS 1 SEUL ITEM MAXIMUM
    ########################################

    discord_articles = (
        discord_articles[:1]
    )

    create_rss(
        DISCORD_OUTPUT,
        "DOFUS — Changelogs",
        "Dernière note de mise à jour "
        "officielle française de DOFUS.",
        discord_articles
    )

    ########################################
    # LOG
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
