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
NEWS_URL = "https://www.dofus.com/fr/mmorpg/actualites/news"

CACHE_FILE = "dofus-news-cache.json"

RSS_FILE = "dofus-news.xml"
DISCORD_RSS_FILE = "dofus-news-discord.xml"

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
    print("########################################")
    print("# Tensho Dofus")
    print("# ACTUALITÉS FRANÇAISES")
    print("########################################")
    print()


# ============================================================
# UTILITAIRES TEXTE
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

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def normalize_title(text):
    if not text:
        return ""

    text = clean_text(text)

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


# ============================================================
# NETTOYAGE DU TITRE DOFUS
# ============================================================

def clean_dofus_title(title):
    """
    Nettoie le H1 récupéré par Playwright.

    Dofus peut renvoyer quelque chose comme :

        Saison Ocre : prenez le temps de vous souvenir…
        Info - 07/07/2026 - 15h00

    ou :

        Packs de classe 2.0 : quatrième salve !
        Shop - 20/08/2026 - 16h00

    ou :

        La DOFUS Cup est de retour !
        Event - 11/08/2026 - 16h00

    On conserve uniquement le véritable titre.
    """

    title = normalize_title(title)

    if not title:
        return ""

    # --------------------------------------------------------
    # Suppression de la partie :
    #
    # Info - 07/07/2026 - 15h00
    # Shop - 20/08/2026 - 16h00
    # Event - 11/08/2026 - 16h00
    #
    # avec éventuellement plusieurs espaces.
    # --------------------------------------------------------

    title = re.sub(
        r"\s+(?:Info|Shop|Event)\s*-\s*"
        r"\d{1,2}/\d{1,2}/\d{4}"
        r"\s*-\s*"
        r"\d{1,2}h\d{2}"
        r"\s*$",
        "",
        title,
        flags=re.IGNORECASE
    )

    # --------------------------------------------------------
    # Variante si l'heure contient "h" sans minutes
    # --------------------------------------------------------

    title = re.sub(
        r"\s+(?:Info|Shop|Event)\s*-\s*"
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
        r"\s+(?:Info|Shop|Event)\s*-\s*"
        r"\d{1,2}/\d{1,2}/\d{4}"
        r"\s*$",
        "",
        title,
        flags=re.IGNORECASE
    )

    # --------------------------------------------------------
    # Nettoyage final
    # --------------------------------------------------------

    title = normalize_title(title)

    return title


def title_is_valid(title):
    if not title:
        return False

    title = clean_dofus_title(title)

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
# DATES
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
    """
    Cherche :

        07/07/2026 - 15h00
        20/08/2026 - 17h00
        20/08/2026 à 17h00
        20 août 2026 - 17h00
    """

    if not text:
        return None

    text = clean_text(text)

    # --------------------------------------------------------
    # DD/MM/YYYY + heure
    # --------------------------------------------------------

    match = re.search(
        r"\b"
        r"(\d{1,2})/"
        r"(\d{1,2})/"
        r"(\d{4})"
        r"\s*"
        r"(?:-|à|a)?"
        r"\s*"
        r"(\d{1,2})h"
        r"(\d{2})"
        r"\b",
        text,
        flags=re.IGNORECASE
    )

    if match:

        try:

            day = int(
                match.group(1)
            )

            month = int(
                match.group(2)
            )

            year = int(
                match.group(3)
            )

            hour = int(
                match.group(4)
            )

            minute = int(
                match.group(5)
            )

            return datetime(
                year,
                month,
                day,
                hour,
                minute,
                tzinfo=timezone.utc
            )

        except Exception:
            pass

    # --------------------------------------------------------
    # DD/MM/YYYY sans heure
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # DD mois YYYY + heure
    # --------------------------------------------------------

    match = re.search(
        r"\b"
        r"(\d{1,2})\s+"
        r"(janvier|février|fevrier|mars|avril|mai|juin|"
        r"juillet|août|aout|septembre|octobre|novembre|"
        r"décembre|decembre)"
        r"\s+"
        r"(\d{4})"
        r"\s*"
        r"(?:-|à|a)?"
        r"\s*"
        r"(\d{1,2})h"
        r"(\d{2})"
        r"\b",
        text,
        flags=re.IGNORECASE
    )

    if match:

        try:

            day = int(
                match.group(1)
            )

            month_name = (
                match.group(2).lower()
            )

            month = MONTHS_FR.get(
                month_name
            )

            year = int(
                match.group(3)
            )

            hour = int(
                match.group(4)
            )

            minute = int(
                match.group(5)
            )

            if month:

                return datetime(
                    year,
                    month,
                    day,
                    hour,
                    minute,
                    tzinfo=timezone.utc
                )

        except Exception:
            pass

    return None


def parse_date(value):
    if not value:
        return None

    value = clean_text(value)

    if not value:
        return None

    # --------------------------------------------------------
    # Date française avec heure
    # --------------------------------------------------------

    french_datetime = parse_french_datetime(
        value
    )

    if french_datetime:
        return french_datetime

    # --------------------------------------------------------
    # RFC / HTTP
    # --------------------------------------------------------

    try:

        dt = parsedate_to_datetime(
            value
        )

        return ensure_timezone(dt)

    except Exception:
        pass

    # --------------------------------------------------------
    # ISO
    # --------------------------------------------------------

    iso_value = value.replace(
        "Z",
        "+00:00"
    )

    try:

        dt = datetime.fromisoformat(
            iso_value
        )

        return ensure_timezone(dt)

    except Exception:
        pass

    # --------------------------------------------------------
    # YYYY-MM-DD
    # --------------------------------------------------------

    match = re.search(
        r"\b(\d{4})-(\d{2})-(\d{2})\b",
        value
    )

    if match:

        try:

            return datetime(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
                tzinfo=timezone.utc
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
# XML
# ============================================================

def escape_xml(text):
    if text is None:
        return ""

    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


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

            data = json.load(f)

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
            f"⚠️ Impossible de charger le cache : "
            f"{e}"
        )

        return []


def save_cache(articles):

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
            f"⚠️ Impossible de sauvegarder le cache : "
            f"{e}"
        )


def get_cached_date(
    cache,
    url
):

    for article in cache:

        if article.get("url") != url:
            continue

        cached_date = article.get(
            "date"
        )

        if not cached_date:
            return None

        return parse_date(
            cached_date
        )

    return None


# ============================================================
# EXTRACTION DATE DEPUIS LOCATOR
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

                value = await locator.get_attribute(
                    attribute
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
    # 1. Lien lui-même
    # --------------------------------------------------------

    date_value = await extract_date_from_locator(
        link
    )

    if date_value:
        return date_value

    # --------------------------------------------------------
    # 2. Parents successifs
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

                if count == 0:
                    continue

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

            date_value = parse_date(
                text
            )

            if date_value:
                return date_value

        except Exception:
            pass

        # ----------------------------------------------------
        # HTML du parent
        # ----------------------------------------------------

        try:

            html = await parent.inner_html()

            date_value = parse_date(
                html
            )

            if date_value:
                return date_value

        except Exception:
            pass

    return None


# ============================================================
# EXTRACTION DU TITRE + DATE DEPUIS L'ARTICLE
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
            f"   ⚠️ Ouverture article impossible : "
            f"{e}"
        )

        return {
            "title": "",
            "date": None,
        }

    article_date = None

    # ========================================================
    # DATE : chercher directement dans la page
    # ========================================================

    date_selectors = [

        # éléments classiques
        "time",

        # attributs date
        "[datetime]",
        "[data-date]",
        "[data-datetime]",
        "[data-published]",
        "[data-publish-date]",

        # classes contenant date
        "[class*='date']",
        "[class*='Date']",
        "[class*='time']",
        "[class*='Time']",

    ]

    for selector in date_selectors:

        try:

            locator = page.locator(
                selector
            )

            count = await locator.count()

            for i in range(
                min(count, 20)
            ):

                article_date = (
                    await extract_date_from_locator(
                        locator.nth(i)
                    )
                )

                if article_date:
                    break

            if article_date:
                break

        except Exception:
            continue

    # ========================================================
    # DATE : recherche dans le texte global
    # ========================================================

    if not article_date:

        try:

            body_text = await page.locator(
                "body"
            ).inner_text()

            article_date = parse_french_datetime(
                body_text
            )

        except Exception:
            pass

    # ========================================================
    # DATE : recherche dans HTML global
    # ========================================================

    if not article_date:

        try:

            html = await page.content()

            article_date = parse_date(
                html
            )

        except Exception:
            pass

    # ========================================================
    # TITRE : H1
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

                    raw_title = (
                        await locator.nth(i).inner_text()
                    )

                    title = clean_dofus_title(
                        raw_title
                    )

                    if title_is_valid(
                        title
                    ):

                        return {
                            "title": title,
                            "date": article_date,
                        }

                except Exception:
                    continue

        except Exception:
            continue

    # ========================================================
    # TITRE : OG TITLE
    # ========================================================

    try:

        og = page.locator(
            'meta[property="og:title"]'
        )

        if await og.count() > 0:

            value = await og.first.get_attribute(
                "content"
            )

            title = clean_dofus_title(
                value
            )

            if title_is_valid(
                title
            ):

                return {
                    "title": title,
                    "date": article_date,
                }

    except Exception:
        pass

    # ========================================================
    # TITRE : TWITTER
    # ========================================================

    try:

        twitter = page.locator(
            'meta[name="twitter:title"]'
        )

        if await twitter.count() > 0:

            value = await twitter.first.get_attribute(
                "content"
            )

            title = clean_dofus_title(
                value
            )

            if title_is_valid(
                title
            ):

                return {
                    "title": title,
                    "date": article_date,
                }

    except Exception:
        pass

    # ========================================================
    # TITRE : JSON-LD
    # ========================================================

    try:

        scripts = page.locator(
            'script[type="application/ld+json"]'
        )

        count = await scripts.count()

        for i in range(count):

            try:

                raw = await scripts.nth(i).inner_text()

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

                    headline = obj.get(
                        "headline"
                    )

                    title = clean_dofus_title(
                        headline
                    )

                    if title_is_valid(
                        title
                    ):

                        # JSON-LD peut aussi contenir
                        # la vraie date.

                        json_date = (
                            obj.get("datePublished")
                            or obj.get("dateCreated")
                        )

                        json_date_parsed = (
                            parse_date(
                                json_date
                            )
                        )

                        if json_date_parsed:
                            article_date = (
                                json_date_parsed
                            )

                        return {
                            "title": title,
                            "date": article_date,
                        }

            except Exception:
                continue

    except Exception:
        pass

    # ========================================================
    # TITRE : TITLE HTML
    # ========================================================

    try:

        page_title = await page.title()

        title = clean_dofus_title(
            page_title
        )

        if title_is_valid(
            title
        ):

            return {
                "title": title,
                "date": article_date,
            }

    except Exception:
        pass

    return {
        "title": "",
        "date": article_date,
    }


# ============================================================
# TRAITEMENT DES ARTICLES
# ============================================================

async def process_articles(
    page,
    listing_articles,
    cache
):

    print(
        "########################################"
    )

    print(
        f"# URLs Actualités Dofus trouvées : "
        f"{len(listing_articles)}"
    )

    print(
        "########################################"
    )

    print()

    results = []

    for index, article in enumerate(
        listing_articles,
        start=1
    ):

        url = article["url"]

        listing_date = article["date"]

        print(
            f"[{index}/{len(listing_articles)}] "
            f"{url}"
        )

        # ----------------------------------------------------
        # ARTICLE COMPLET
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # TITRE
        # ----------------------------------------------------

        if title:

            print(
                f"   🏷️ Titre trouvé via PLAYWRIGHT: "
                f"{title}"
            )

        else:

            print(
                "   ⚠️ Impossible de récupérer "
                "le vrai titre."
            )

        # ----------------------------------------------------
        # DATE PRIORITAIRE
        # ----------------------------------------------------
        #
        # 1. Page article
        # 2. Listing
        # 3. Cache
        #
        # ----------------------------------------------------

        final_date = None

        if article_date:

            final_date = article_date

            print(
                "   📅 Date trouvée via ARTICLE: "
                f"{format_datetime(final_date)}"
            )

        elif listing_date:

            final_date = listing_date

            print(
                "   📅 Date trouvée via LISTING: "
                f"{format_datetime(final_date)}"
            )

        else:

            cached_date = get_cached_date(
                cache,
                url
            )

            if cached_date:

                final_date = cached_date

                print(
                    "   📅 Date trouvée via CACHE: "
                    f"{format_datetime(final_date)}"
                )

            else:

                print(
                    "   ⚠️ Date introuvable."
                )

        # ----------------------------------------------------
        # TITRE OBLIGATOIRE
        # ----------------------------------------------------

        if not title:

            print(
                "   ❌ Article ignoré : "
                "titre réel introuvable."
            )

            print()

            continue

        # ----------------------------------------------------
        # DATE OBLIGATOIRE
        # ----------------------------------------------------

        if not final_date:

            print(
                "   ❌ Article ignoré : "
                "date réelle introuvable."
            )

            print()

            continue

        # ----------------------------------------------------
        # DONNÉES
        # ----------------------------------------------------

        article_result = {
            "title": title,
            "url": url,
            "date": final_date.isoformat(),
            "guid": url,
        }

        results.append(
            article_result
        )

        print(
            f"🟢 {format_datetime(final_date)} "
            f"- {title}"
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

        guid = (
            article.get("guid")
            or article.get("url")
        )

        if not guid:
            continue

        if guid in seen:
            continue

        title = clean_dofus_title(
            article.get(
                "title",
                ""
            )
        )

        url = article.get(
            "url",
            ""
        )

        date_value = parse_date(
            article.get(
                "date"
            )
        )

        if not title:
            continue

        if not url:
            continue

        if not date_value:
            continue

        merged.append(
            {
                "title": title,
                "url": url,
                "date": date_value.isoformat(),
                "guid": guid,
            }
        )

        seen.add(
            guid
        )

    # --------------------------------------------------------
    # TRI
    # --------------------------------------------------------

    def sort_key(article):

        date_value = parse_date(
            article.get(
                "date"
            )
        )

        if date_value:
            return date_value

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

        title = escape_xml(
            article["title"]
        )

        link = escape_xml(
            article["url"]
        )

        guid = escape_xml(
            article["guid"]
        )

        date_value = parse_date(
            article["date"]
        )

        if not date_value:
            continue

        pub_date = escape_xml(
            format_rss_date(
                date_value
            )
        )

        description = escape_xml(
            article["title"]
        )

        items.append(
            f"""
    <item>
      <title>{title}</title>
      <link>{link}</link>
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
    <link>{NEWS_URL}</link>
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

        title = escape_xml(
            article["title"]
        )

        link = escape_xml(
            article["url"]
        )

        guid = escape_xml(
            article["guid"]
        )

        date_value = parse_date(
            article["date"]
        )

        if not date_value:
            continue

        pub_date = escape_xml(
            format_rss_date(
                date_value
            )
        )

        description = escape_xml(
            article["title"]
        )

        items.append(
            f"""    <item>
      <title>{title}</title>
      <link>{link}</link>
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
    <link>{NEWS_URL}</link>
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

        # ----------------------------------------------------
        # 1. LISTING
        # ----------------------------------------------------

        listing_articles = (
            await get_listing_articles(
                page
            )
        )

        # ----------------------------------------------------
        # 2. ARTICLES
        # ----------------------------------------------------

        new_articles = (
            await process_articles(
                page,
                listing_articles,
                cache
            )
        )

        await browser.close()

    # --------------------------------------------------------
    # 3. CACHE
    # --------------------------------------------------------

    articles = merge_with_cache(
        new_articles,
        cache
    )

    print()

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

    # --------------------------------------------------------
    # 4. CACHE
    # --------------------------------------------------------

    save_cache(
        articles
    )

    # --------------------------------------------------------
    # 5. RSS
    # --------------------------------------------------------

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
    asyncio.run(main())
