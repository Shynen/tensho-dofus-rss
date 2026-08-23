import asyncio
import json
import os
import re
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://www.dofus.com"

SOURCE_URL = (
    "https://www.dofus.com/fr/mmorpg/actualites/news"
)

CACHE_FILE = "dofus-news-cache.json"

RSS_FILE = "dofus-news.xml"

DISCORD_RSS_FILE = (
    "dofus-news-discord.xml"
)

MAX_ARTICLES = 20

MAX_LISTING_ARTICLES = 50

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)


# ============================================================
# AFFICHAGE
# ============================================================

def print_header():

    print()

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

    print()


# ============================================================
# NETTOYAGE TEXTE
# ============================================================

def clean_text(text):

    if not text:
        return ""

    text = BeautifulSoup(
        str(text),
        "html.parser"
    ).get_text(
        " ",
        strip=True
    )

    text = text.replace(
        "\xa0",
        " "
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def normalize_title(title):

    if not title:
        return ""

    title = clean_text(
        title
    )

    title = re.sub(
        r"\s+",
        " ",
        title
    )

    return title.strip()


# ============================================================
# NETTOYAGE TITRE DOFUS
# ============================================================

def clean_dofus_title(title):

    if not title:
        return ""

    title = normalize_title(
        title
    )

    # --------------------------------------------------------
    # Supprime :
    #
    # Info - 07/07/2026 - 15h00
    # Shop - 20/08/2026 - 16h00
    # Event - 11/08/2026 - 16h00
    #
    # --------------------------------------------------------

    title = re.sub(
        r"\s+(?:Info|Shop|Event)"
        r"\s*-\s*"
        r"\d{1,2}/\d{1,2}/\d{4}"
        r"\s*-\s*"
        r"\d{1,2}h\d{2}"
        r"\s*$",
        "",
        title,
        flags=re.IGNORECASE
    )

    # --------------------------------------------------------
    # Variante sans minutes
    # --------------------------------------------------------

    title = re.sub(
        r"\s+(?:Info|Shop|Event)"
        r"\s*-\s*"
        r"\d{1,2}/\d{1,2}/\d{4}"
        r"\s*-\s*"
        r"\d{1,2}h"
        r"\s*$",
        "",
        title,
        flags=re.IGNORECASE
    )

    # --------------------------------------------------------
    # Variante date seule
    # --------------------------------------------------------

    title = re.sub(
        r"\s+(?:Info|Shop|Event)"
        r"\s*-\s*"
        r"\d{1,2}/\d{1,2}/\d{4}"
        r"\s*$",
        "",
        title,
        flags=re.IGNORECASE
    )

    return normalize_title(
        title
    )


def title_is_valid(title):

    if not title:
        return False

    title = clean_dofus_title(
        title
    )

    if len(title) < 8:
        return False

    invalid_titles = {
        "en savoir+",
        "en savoir +",
        "en savoir",
        "actualités récentes",
        "actualites recentes",
        "lire la suite",
        "voir plus",
    }

    if title.lower() in invalid_titles:
        return False

    return True


# ============================================================
# DATES FRANÇAISES
# ============================================================

MONTHS_FR = {

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


def ensure_timezone(dt):

    if dt is None:
        return None

    if dt.tzinfo is None:

        return dt.replace(
            tzinfo=timezone.utc
        )

    return dt


def parse_french_datetime(text):

    if not text:
        return None

    text = clean_text(
        text
    )

    # ========================================================
    # DD/MM/YYYY - HHhMM
    # ========================================================

    match = re.search(
        r"\b"
        r"(\d{1,2})/"
        r"(\d{1,2})/"
        r"(\d{4})"
        r"\s*"
        r"(?:-|à|a)"
        r"\s*"
        r"(\d{1,2})h"
        r"(\d{2})"
        r"\b",
        text,
        flags=re.IGNORECASE
    )

    if match:

        try:

            return datetime(
                int(match.group(3)),
                int(match.group(2)),
                int(match.group(1)),
                int(match.group(4)),
                int(match.group(5)),
                tzinfo=timezone.utc
            )

        except Exception:

            pass

    # ========================================================
    # DD/MM/YYYY HHhMM
    # ========================================================

    match = re.search(
        r"\b"
        r"(\d{1,2})/"
        r"(\d{1,2})/"
        r"(\d{4})"
        r"\s+"
        r"(\d{1,2})h"
        r"(\d{2})"
        r"\b",
        text,
        flags=re.IGNORECASE
    )

    if match:

        try:

            return datetime(
                int(match.group(3)),
                int(match.group(2)),
                int(match.group(1)),
                int(match.group(4)),
                int(match.group(5)),
                tzinfo=timezone.utc
            )

        except Exception:

            pass

    # ========================================================
    # DD/MM/YYYY sans heure
    # ========================================================

    match = re.search(
        r"\b"
        r"(\d{1,2})/"
        r"(\d{1,2})/"
        r"(\d{4})"
        r"\b",
        text
    )

    if match:

        try:

            return datetime(
                int(match.group(3)),
                int(match.group(2)),
                int(match.group(1)),
                tzinfo=timezone.utc
            )

        except Exception:

            pass

    # ========================================================
    # DD mois YYYY HHhMM
    # ========================================================

    match = re.search(
        r"\b"
        r"(\d{1,2})\s+"
        r"(janvier|février|fevrier|mars|avril|mai|juin|"
        r"juillet|août|aout|septembre|octobre|novembre|"
        r"décembre|decembre)"
        r"\s+"
        r"(\d{4})"
        r"\s*"
        r"(?:-|à|a)"
        r"\s*"
        r"(\d{1,2})h"
        r"(\d{2})"
        r"\b",
        text,
        flags=re.IGNORECASE
    )

    if match:

        try:

            month = MONTHS_FR.get(
                match.group(2).lower()
            )

            if month:

                return datetime(
                    int(match.group(3)),
                    month,
                    int(match.group(1)),
                    int(match.group(4)),
                    int(match.group(5)),
                    tzinfo=timezone.utc
                )

        except Exception:

            pass

    return None


def parse_date(value):

    if not value:
        return None

    value = clean_text(
        value
    )

    if not value:
        return None

    # --------------------------------------------------------
    # Français
    # --------------------------------------------------------

    result = parse_french_datetime(
        value
    )

    if result:
        return result

    # --------------------------------------------------------
    # RFC / HTTP
    # --------------------------------------------------------

    try:

        result = parsedate_to_datetime(
            value
        )

        return ensure_timezone(
            result
        )

    except Exception:

        pass

    # --------------------------------------------------------
    # ISO
    # --------------------------------------------------------

    try:

        iso = value.replace(
            "Z",
            "+00:00"
        )

        result = datetime.fromisoformat(
            iso
        )

        return ensure_timezone(
            result
        )

    except Exception:

        pass

    return None


def format_rss_date(dt):

    if not dt:
        return None

    return format_datetime(
        ensure_timezone(dt)
    )


# ============================================================
# EXTRACTION DATE LOCATOR
# ============================================================

async def extract_date_from_locator(
    locator
):

    try:

        attributes = [

            "datetime",

            "data-date",

            "data-datetime",

            "data-published",

            "data-publish-date",

            "content",

        ]

        for attribute in attributes:

            try:

                value = (
                    await locator.get_attribute(
                        attribute
                    )
                )

                if not value:
                    continue

                date_value = parse_date(
                    value
                )

                if date_value:
                    return date_value

            except Exception:

                pass

        try:

            text = await locator.inner_text()

            date_value = parse_date(
                text
            )

            if date_value:
                return date_value

        except Exception:

            pass

    except Exception:

        pass

    return None


# ============================================================
# EXTRACTION DATE LISTING
# ============================================================

async def extract_listing_date(
    link
):

    # --------------------------------------------------------
    # Lien
    # --------------------------------------------------------

    date_value = (
        await extract_date_from_locator(
            link
        )
    )

    if date_value:
        return date_value

    # --------------------------------------------------------
    # Parents
    # --------------------------------------------------------

    parent = link

    for level in range(8):

        try:

            parent = parent.locator(
                "xpath=.."
            )

            if await parent.count() == 0:
                break

        except Exception:

            break

        selectors = [

            "time",

            "[datetime]",

            "[data-date]",

            "[data-datetime]",

            "[data-published]",

            "[data-publish-date]",

            "[class*='date']",

            "[class*='Date']",

            "[class*='time']",

            "[class*='Time']",

        ]

        for selector in selectors:

            try:

                loc = parent.locator(
                    selector
                )

                count = await loc.count()

                for i in range(
                    min(count, 10)
                ):

                    date_value = (
                        await extract_date_from_locator(
                            loc.nth(i)
                        )
                    )

                    if date_value:
                        return date_value

            except Exception:

                continue

        # ----------------------------------------------------
        # Texte du parent
        # ----------------------------------------------------

        try:

            text = await parent.inner_text()

            date_value = parse_french_datetime(
                text
            )

            if date_value:
                return date_value

        except Exception:

            pass

    return None


# ============================================================
# GET LISTING ARTICLES
# ============================================================

async def get_listing_articles(
    page
):

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

    try:

        await page.goto(
            SOURCE_URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

    except Exception as e:

        print(
            f"❌ Erreur ouverture listing : {e}"
        )

        return []

    await page.wait_for_timeout(
        3000
    )

    # --------------------------------------------------------
    # Sélecteur principal
    # --------------------------------------------------------

    selectors = [

        "a[href*='/fr/mmorpg/actualites/news/']",

        "a[href*='/mmorpg/actualites/news/']",

    ]

    links = None

    for selector in selectors:

        try:

            locator = page.locator(
                selector
            )

            count = await locator.count()

            if count > 0:

                links = locator

                break

        except Exception:

            continue

    if links is None:

        print(
            "❌ Aucun lien d'actualité trouvé."
        )

        return []

    count = await links.count()

    print(
        f"Premier lot : {count} actualités détectées."
    )

    articles = []

    seen_urls = set()

    dates_found = 0

    for i in range(
        count
    ):

        if len(articles) >= MAX_LISTING_ARTICLES:
            break

        try:

            link = links.nth(i)

            href = await link.get_attribute(
                "href"
            )

            if not href:
                continue

            full_url = urljoin(
                BASE_URL,
                href
            )

            # ------------------------------------------------
            # Nettoyage URL
            # ------------------------------------------------

            full_url = (
                full_url
                .split("#", 1)[0]
                .strip()
            )

            if full_url in seen_urls:
                continue

            # ------------------------------------------------
            # Vérification URL Dofus
            # ------------------------------------------------

            if not re.search(
                r"/fr/mmorpg/actualites/news/\d+",
                full_url
            ):

                continue

            seen_urls.add(
                full_url
            )

            # ------------------------------------------------
            # Date listing
            # ------------------------------------------------

            listing_date = (
                await extract_listing_date(
                    link
                )
            )

            if listing_date:
                dates_found += 1

            articles.append(
                {
                    "url": full_url,
                    "date": listing_date,
                }
            )

        except Exception:

            continue

    print(
        f"📅 Dates trouvées dans la liste : "
        f"{dates_found}/{len(articles)}"
    )

    print(
        f"🟢 Total actualités récupérées : "
        f"{len(articles)}"
    )

    print()

    print(
        "########################################"
    )

    print(
        f"# URLs Actualités Dofus trouvées : "
        f"{len(articles)}"
    )

    print(
        "########################################"
    )

    print()

    return articles


# ============================================================
# ARTICLE COMPLET
# ============================================================

async def extract_article_data(
    page,
    url
):

    try:

        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        await page.wait_for_timeout(
            1500
        )

    except Exception as e:

        print(
            f"   ⚠️ Ouverture article impossible : {e}"
        )

        return {
            "title": "",
            "date": None,
        }

    article_date = None

    raw_title = ""

    # ========================================================
    # H1
    # ========================================================

    h1_selectors = [

        "h1",

        "main h1",

        "article h1",

        "[role='main'] h1",

    ]

    for selector in h1_selectors:

        try:

            locator = page.locator(
                selector
            )

            count = await locator.count()

            for i in range(
                min(count, 10)
            ):

                try:

                    candidate = (
                        await locator.nth(i).inner_text()
                    )

                    candidate = clean_text(
                        candidate
                    )

                    if not candidate:
                        continue

                    # ------------------------------------------------
                    # TRÈS IMPORTANT :
                    # on extrait d'abord la date du H1
                    # AVANT de nettoyer le titre.
                    # ------------------------------------------------

                    h1_date = (
                        parse_french_datetime(
                            candidate
                        )
                    )

                    if h1_date:

                        article_date = (
                            h1_date
                        )

                    candidate_title = (
                        clean_dofus_title(
                            candidate
                        )
                    )

                    if title_is_valid(
                        candidate_title
                    ):

                        raw_title = (
                            candidate_title
                        )

                        break

                except Exception:

                    continue

            if raw_title:
                break

        except Exception:

            continue

    # ========================================================
    # META OG TITLE
    # ========================================================

    if not raw_title:

        try:

            og = page.locator(
                'meta[property="og:title"]'
            )

            if await og.count() > 0:

                value = (
                    await og.first.get_attribute(
                        "content"
                    )
                )

                raw_title = clean_dofus_title(
                    value
                )

        except Exception:

            pass

    # ========================================================
    # TWITTER TITLE
    # ========================================================

    if not raw_title:

        try:

            twitter = page.locator(
                'meta[name="twitter:title"]'
            )

            if await twitter.count() > 0:

                value = (
                    await twitter.first.get_attribute(
                        "content"
                    )
                )

                raw_title = clean_dofus_title(
                    value
                )

        except Exception:

            pass

    # ========================================================
    # JSON-LD
    # ========================================================

    try:

        scripts = page.locator(
            'script[type="application/ld+json"]'
        )

        count = await scripts.count()

        for i in range(count):

            try:

                raw = (
                    await scripts.nth(i).inner_text()
                )

                data = json.loads(
                    raw
                )

                objects = (
                    data
                    if isinstance(
                        data,
                        list
                    )
                    else [data]
                )

                for obj in objects:

                    if not isinstance(
                        obj,
                        dict
                    ):
                        continue

                    if not raw_title:

                        headline = obj.get(
                            "headline"
                        )

                        candidate = (
                            clean_dofus_title(
                                headline
                            )
                        )

                        if title_is_valid(
                            candidate
                        ):

                            raw_title = (
                                candidate
                            )

                    if not article_date:

                        published = (
                            obj.get(
                                "datePublished"
                            )
                            or
                            obj.get(
                                "dateCreated"
                            )
                        )

                        parsed = parse_date(
                            published
                        )

                        if parsed:

                            article_date = (
                                parsed
                            )

            except Exception:

                continue

    except Exception:

        pass

    # ========================================================
    # DATE DANS LES ÉLÉMENTS DE LA PAGE
    # ========================================================

    if not article_date:

        selectors = [

            "time",

            "[datetime]",

            "[data-date]",

            "[data-datetime]",

            "[data-published]",

            "[data-publish-date]",

        ]

        for selector in selectors:

            try:

                locator = page.locator(
                    selector
                )

                count = await locator.count()

                for i in range(
                    min(count, 20)
                ):

                    parsed = (
                        await extract_date_from_locator(
                            locator.nth(i)
                        )
                    )

                    if parsed:

                        article_date = (
                            parsed
                        )

                        break

                if article_date:
                    break

            except Exception:

                continue

    # ========================================================
    # DATE DANS LE BODY
    # ========================================================

    if not article_date:

        try:

            body_text = (
                await page.locator(
                    "body"
                ).inner_text()
            )

            parsed = parse_french_datetime(
                body_text
            )

            if parsed:

                article_date = (
                    parsed
                )

        except Exception:

            pass

    # ========================================================
    # TITLE HTML
    # ========================================================

    if not raw_title:

        try:

            page_title = await page.title()

            candidate = clean_dofus_title(
                page_title
            )

            if title_is_valid(
                candidate
            ):

                raw_title = (
                    candidate
                )

        except Exception:

            pass

    return {
        "title": raw_title,
        "date": article_date,
    }


# ============================================================
# CACHE
# ============================================================

def load_cache():

    if not os.path.exists(
        CACHE_FILE
    ):

        print(
            "Cache Actualités Dofus chargé : "
            "0 articles."
        )

        return []

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
            list
        ):

            data = []

        print(
            f"Cache Actualités Dofus chargé : "
            f"{len(data)} articles."
        )

        return data

    except Exception as e:

        print(
            f"⚠️ Erreur lecture cache : {e}"
        )

        return []


def save_cache(
    articles
):

    try:

        with open(
            CACHE_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                articles[:MAX_ARTICLES],
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:

        print(
            f"⚠️ Erreur sauvegarde cache : {e}"
        )


def get_cached_date(
    cache,
    url
):

    for article in cache:

        if article.get(
            "url"
        ) != url:

            continue

        value = article.get(
            "date"
        )

        if value:

            return parse_date(
                value
            )

    return None


# ============================================================
# TRAITEMENT ARTICLES
# ============================================================

async def process_articles(
    page,
    listing_articles,
    cache
):

    results = []

    for index, article in enumerate(
        listing_articles,
        start=1
    ):

        url = article[
            "url"
        ]

        listing_date = article.get(
            "date"
        )

        print(
            f"[{index}/{len(listing_articles)}] "
            f"{url}"
        )

        article_data = (
            await extract_article_data(
                page,
                url
            )
        )

        title = article_data[
            "title"
        ]

        article_date = article_data[
            "date"
        ]

        # ====================================================
        # TITRE
        # ====================================================

        if title:

            print(
                "   🏷️ Titre trouvé via PLAYWRIGHT: "
                f"{title}"
            )

        else:

            print(
                "   ⚠️ Titre introuvable."
            )

        # ====================================================
        # DATE
        # ====================================================

        final_date = None

        if article_date:

            final_date = (
                article_date
            )

            print(
                "   📅 Date trouvée via ARTICLE: "
                f"{format_datetime(final_date)}"
            )

        elif listing_date:

            final_date = (
                listing_date
            )

            print(
                "   📅 Date trouvée via LISTING: "
                f"{format_datetime(final_date)}"
            )

        else:

            cached_date = (
                get_cached_date(
                    cache,
                    url
                )
            )

            if cached_date:

                final_date = (
                    cached_date
                )

                print(
                    "   📅 Date trouvée via CACHE: "
                    f"{format_datetime(final_date)}"
                )

            else:

                print(
                    "   ⚠️ Date introuvable."
                )

        # ====================================================
        # VALIDATION
        # ====================================================

        if not title:

            print(
                "   ❌ Article ignoré : "
                "titre introuvable."
            )

            print()

            continue

        if not final_date:

            print(
                "   ❌ Article ignoré : "
                "date introuvable."
            )

            print()

            continue

        # ====================================================
        # ARTICLE
        # ====================================================

        result = {

            "title": clean_dofus_title(
                title
            ),

            "url": url,

            "guid": url,

            "date": final_date.isoformat(),

        }

        results.append(
            result
        )

        print(
            f"🟢 {format_datetime(final_date)} "
            f"- {result['title']}"
        )

        print()

        await page.wait_for_timeout(
            300
        )

        if len(results) >= MAX_ARTICLES:

            break

    return results


# ============================================================
# FUSION CACHE
# ============================================================

def merge_with_cache(
    new_articles,
    cache
):

    merged = []

    seen = set()

    # --------------------------------------------------------
    # NOUVEAUX
    # --------------------------------------------------------

    for article in new_articles:

        guid = article.get(
            "guid"
        )

        if not guid:
            continue

        if guid in seen:
            continue

        seen.add(
            guid
        )

        merged.append(
            article
        )

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    for article in cache:

        url = article.get(
            "url"
        )

        if not url:
            continue

        if url in seen:
            continue

        title = clean_dofus_title(
            article.get(
                "title",
                ""
            )
        )

        date_value = parse_date(
            article.get(
                "date"
            )
        )

        if not title:
            continue

        if not date_value:
            continue

        merged.append(
            {
                "title": title,
                "url": url,
                "guid": url,
                "date": date_value.isoformat(),
            }
        )

        seen.add(
            url
        )

    # --------------------------------------------------------
    # TRI DATE DESC
    # --------------------------------------------------------

    def sort_key(
        article
    ):

        value = parse_date(
            article.get(
                "date"
            )
        )

        if value:
            return value

        return datetime.min.replace(
            tzinfo=timezone.utc
        )

    merged.sort(
        key=sort_key,
        reverse=True
    )

    return merged[
        :MAX_ARTICLES
    ]


# ============================================================
# XML ESCAPE
# ============================================================

def escape_xml(
    text
):

    if text is None:
        return ""

    return (
        str(text)
        .replace(
            "&",
            "&amp;"
        )
        .replace(
            "<",
            "&lt;"
        )
        .replace(
            ">",
            "&gt;"
        )
        .replace(
            '"',
            "&quot;"
        )
        .replace(
            "'",
            "&apos;"
        )
    )


# ============================================================
# RSS NORMAL
# ============================================================

def generate_normal_rss(
    articles
):

    print(
        "Génération de dofus-news.xml..."
    )

    items = []

    for article in articles:

        date_value = parse_date(
            article.get(
                "date"
            )
        )

        if not date_value:
            continue

        title = escape_xml(
            article[
                "title"
            ]
        )

        url = escape_xml(
            article[
                "url"
            ]
        )

        guid = escape_xml(
            article[
                "guid"
            ]
        )

        pub_date = escape_xml(
            format_rss_date(
                date_value
            )
        )

        description = escape_xml(
            article[
                "title"
            ]
        )

        items.append(
            f"""
    <item>
      <title>{title}</title>
      <link>{url}</link>
      <guid isPermaLink="true">{guid}</guid>
      <pubDate>{pub_date}</pubDate>
      <description>{description}</description>
    </item>
"""
        )

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Tensho Dofus - Actualités</title>
    <link>{SOURCE_URL}</link>
    <description>Actualités françaises de Dofus</description>
    <language>fr</language>
    {''.join(items)}
  </channel>
</rss>
"""

    with open(
        RSS_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            rss
        )

    print(
        "🟢 dofus-news.xml généré."
    )


# ============================================================
# RSS DISCORD
# ============================================================

def generate_discord_rss(
    articles
):

    print(
        "Génération de dofus-news-discord.xml..."
    )

    items = []

    for article in articles:

        date_value = parse_date(
            article.get(
                "date"
            )
        )

        if not date_value:
            continue

        title = escape_xml(
            article[
                "title"
            ]
        )

        url = escape_xml(
            article[
                "url"
            ]
        )

        guid = escape_xml(
            article[
                "guid"
            ]
        )

        pub_date = escape_xml(
            format_rss_date(
                date_value
            )
        )

        description = escape_xml(
            article[
                "title"
            ]
        )

        items.append(
            f"""    <item>
      <title>{title}</title>
      <link>{url}</link>
      <guid isPermaLink="true">{guid}</guid>
      <description>{description}</description>
      <pubDate>{pub_date}</pubDate>
    </item>
"""
        )

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Tensho Dofus - Discord</title>
    <link>{SOURCE_URL}</link>
    <description>Flux Discord des actualités Dofus</description>
    {''.join(items)}
  </channel>
</rss>
"""

    with open(
        DISCORD_RSS_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            rss
        )

    print(
        "🟢 dofus-news-discord.xml généré."
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    print_header()

    cache = load_cache()

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        context = await browser.new_context(

            user_agent=USER_AGENT,

            locale="fr-FR",

            timezone_id="Europe/Paris",

            viewport={
                "width": 1920,
                "height": 1080,
            }

        )

        page = await context.new_page()

        # ====================================================
        # LISTING
        # ====================================================

        listing_articles = (
            await get_listing_articles(
                page
            )
        )

        if not listing_articles:

            await browser.close()

            print(
                "❌ Aucune actualité récupérée."
            )

            return

        # ====================================================
        # ARTICLES
        # ====================================================

        new_articles = (
            await process_articles(
                page,
                listing_articles,
                cache
            )
        )

        await browser.close()

    # ========================================================
    # CACHE + TRI
    # ========================================================

    articles = merge_with_cache(
        new_articles,
        cache
    )

    print()

    print(
        "########################################"
    )

    print(
        f"# {len(articles)} Actualités Dofus retenues"
    )

    print(
        "########################################"
    )

    print()

    for index, article in enumerate(
        articles,
        start=1
    ):

        date_value = parse_date(
            article.get(
                "date"
            )
        )

        if date_value:

            date_display = format_datetime(
                date_value
            )

        else:

            date_display = (
                "DATE INVALIDE"
            )

        print(
            f"{index:02d}. "
            f"{date_display} - "
            f"{article['title']}"
        )

    print()

    # ========================================================
    # CACHE
    # ========================================================

    save_cache(
        articles
    )

    # ========================================================
    # RSS
    # ========================================================

    generate_normal_rss(
        articles
    )

    generate_discord_rss(
        articles
    )

    print()

    print(
        "########################################"
    )

    print(
        "# DOFUS ACTUALITÉS RSS TERMINÉ"
    )

    print(
        "########################################"
    )

    print()


# ============================================================
# EXECUTION
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )
