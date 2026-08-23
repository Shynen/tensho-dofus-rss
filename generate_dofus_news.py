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
# UTILITAIRES
# ============================================================

def clean_text(text):
    if not text:
        return ""

    text = BeautifulSoup(str(text), "html.parser").get_text(
        " ",
        strip=True
    )

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def normalize_title(text):
    """
    Nettoyage léger uniquement.

    On conserve :
    - accents
    - majuscules
    - ponctuation
    - apostrophes
    - vrais titres DOFUS
    """

    text = clean_text(text)

    text = text.replace("\xa0", " ")

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def title_is_valid(title):
    if not title:
        return False

    title = normalize_title(title)

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
        return dt.replace(tzinfo=timezone.utc)

    return dt


def parse_date(value):
    """
    Parse plusieurs formats possibles :
    - RFC 2822
    - ISO
    - YYYY-MM-DD
    - DD/MM/YYYY
    - DD-MM-YYYY
    - dates françaises :
      20 août 2026
      20 aout 2026
    """

    if not value:
        return None

    value = clean_text(value)

    if not value:
        return None

    # --------------------------------------------------------
    # RFC / HTTP
    # --------------------------------------------------------

    try:
        dt = parsedate_to_datetime(value)
        return ensure_timezone(dt)
    except Exception:
        pass

    # --------------------------------------------------------
    # ISO
    # --------------------------------------------------------

    iso_value = value.replace("Z", "+00:00")

    try:
        dt = datetime.fromisoformat(iso_value)
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
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))

            return datetime(
                year,
                month,
                day,
                tzinfo=timezone.utc
            )
        except Exception:
            pass

    # --------------------------------------------------------
    # DD/MM/YYYY
    # --------------------------------------------------------

    match = re.search(
        r"\b(\d{1,2})[\/\.](\d{1,2})[\/\.](\d{4})\b",
        value
    )

    if match:
        try:
            day = int(match.group(1))
            month = int(match.group(2))
            year = int(match.group(3))

            return datetime(
                year,
                month,
                day,
                tzinfo=timezone.utc
            )
        except Exception:
            pass

    # --------------------------------------------------------
    # DD-MM-YYYY
    # --------------------------------------------------------

    match = re.search(
        r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b",
        value
    )

    if match:
        try:
            day = int(match.group(1))
            month = int(match.group(2))
            year = int(match.group(3))

            return datetime(
                year,
                month,
                day,
                tzinfo=timezone.utc
            )
        except Exception:
            pass

    # --------------------------------------------------------
    # DATE FRANÇAISE
    # Exemple :
    # 20 août 2026
    # 20 aout 2026
    # --------------------------------------------------------

    french_pattern = (
        r"\b"
        r"(\d{1,2})"
        r"\s+"
        r"(janvier|février|fevrier|mars|avril|mai|juin|"
        r"juillet|août|aout|septembre|octobre|novembre|"
        r"décembre|decembre)"
        r"\s+"
        r"(\d{4})"
        r"\b"
    )

    match = re.search(
        french_pattern,
        value,
        flags=re.IGNORECASE
    )

    if match:
        try:
            day = int(match.group(1))
            month_name = match.group(2).lower()
            year = int(match.group(3))

            month = MONTHS_FR.get(month_name)

            if month:
                return datetime(
                    year,
                    month,
                    day,
                    tzinfo=timezone.utc
                )

        except Exception:
            pass

    return None


def format_rss_date(dt):
    if not dt:
        return None

    return format_datetime(ensure_timezone(dt))


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

    if not os.path.exists(CACHE_FILE):
        print("Cache Actualités Dofus chargé : 0 articles.")
        return []

    try:

        with open(
            CACHE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if not isinstance(data, list):
            data = []

        print(
            f"Cache Actualités Dofus chargé : "
            f"{len(data)} articles."
        )

        return data

    except Exception as e:

        print(
            f"⚠️ Impossible de charger le cache : {e}"
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
            f"⚠️ Impossible de sauvegarder le cache : {e}"
        )


# ============================================================
# RECUPERATION DATE DEPUIS LE CACHE
# ============================================================

def get_cached_date(cache, url):

    for article in cache:

        cached_url = article.get("url")

        if cached_url != url:
            continue

        cached_date = article.get("date")

        if not cached_date:
            return None

        try:
            return datetime.fromisoformat(
                cached_date.replace("Z", "+00:00")
            )
        except Exception:
            return parse_date(cached_date)

    return None


# ============================================================
# EXTRACTION DATE DEPUIS UN ELEMENT
# ============================================================

async def extract_date_from_locator(locator):

    """
    Essaie toutes les informations possibles
    d'un élément HTML :
    - datetime
    - data-date
    - data-datetime
    - content
    - texte visible
    """

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

                if value:

                    date_value = parse_date(value)

                    if date_value:
                        return date_value

            except Exception:
                pass

        try:

            text = await locator.inner_text()

            date_value = parse_date(text)

            if date_value:
                return date_value

        except Exception:
            pass

    except Exception:
        pass

    return None


# ============================================================
# EXTRACTION DATE DANS LE LISTING
# ============================================================

async def extract_listing_date(link):
    """
    Nouvelle méthode robuste.

    On ne dépend plus uniquement d'un sélecteur
    de carte précis.

    On inspecte progressivement les parents du lien
    et toutes les informations de date disponibles.
    """

    # --------------------------------------------------------
    # 1. Date directement dans le lien
    # --------------------------------------------------------

    date_value = await extract_date_from_locator(link)

    if date_value:
        return date_value

    # --------------------------------------------------------
    # 2. Recherche d'éléments de date dans les parents
    # --------------------------------------------------------

    parent = link

    for level in range(8):

        try:

            parent = parent.locator("xpath=..")

            if await parent.count() == 0:
                break

        except Exception:
            break

        # ----------------------------------------------------
        # Sélecteurs très larges
        # ----------------------------------------------------

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

                loc = parent.locator(selector)

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
        # Recherche dans le texte du parent
        # ----------------------------------------------------

        try:

            text = await parent.inner_text()

            date_value = parse_date(text)

            if date_value:
                return date_value

        except Exception:
            pass

        # ----------------------------------------------------
        # Recherche dans le HTML du parent
        # ----------------------------------------------------

        try:

            html = await parent.inner_html()

            # ISO
            iso_matches = re.findall(
                r"\b\d{4}-\d{2}-\d{2}"
                r"(?:T\d{2}:\d{2}:\d{2}"
                r"(?:\.\d+)?"
                r"(?:Z|[+-]\d{2}:?\d{2})?)?",
                html
            )

            for value in iso_matches:

                date_value = parse_date(value)

                if date_value:
                    return date_value

            # Français
            french_matches = re.findall(
                r"\b\d{1,2}\s+"
                r"(?:janvier|février|fevrier|mars|avril|mai|juin|"
                r"juillet|août|aout|septembre|octobre|novembre|"
                r"décembre|decembre)"
                r"\s+\d{4}\b",
                html,
                flags=re.IGNORECASE
            )

            for value in french_matches:

                date_value = parse_date(value)

                if date_value:
                    return date_value

        except Exception:
            pass

    return None


# ============================================================
# EXTRACTION DU LISTING
# ============================================================

async def get_listing_articles(page):

    print("========================================")
    print("Ouverture avec Playwright :")
    print(NEWS_URL)
    print("========================================")
    print()

    await page.goto(
        NEWS_URL,
        wait_until="domcontentloaded",
        timeout=60000
    )

    await page.wait_for_timeout(3000)

    # --------------------------------------------------------
    # Liens articles
    # --------------------------------------------------------

    links = await page.locator(
        'a[href*="/fr/mmorpg/actualites/news/"]'
    ).all()

    articles = []
    seen_urls = set()

    for link in links:

        try:
            href = await link.get_attribute("href")
        except Exception:
            continue

        if not href:
            continue

        href = urljoin(
            BASE_URL,
            href
        )

        if href.rstrip("/") == NEWS_URL.rstrip("/"):
            continue

        href = href.split("#")[0]

        if href in seen_urls:
            continue

        seen_urls.add(href)

        # ----------------------------------------------------
        # DATE
        # ----------------------------------------------------

        date_value = await extract_listing_date(link)

        articles.append(
            {
                "url": href,
                "date": date_value,
            }
        )

        if len(articles) >= MAX_LISTING_ARTICLES:
            break

    print(
        f"Premier lot : {len(articles)} "
        f"actualités détectées."
    )

    dates_found = sum(
        1
        for article in articles
        if article["date"] is not None
    )

    print(
        f"📅 Dates trouvées dans la liste : "
        f"{dates_found}/{len(articles)}"
    )

    print(
        f"🟢 Total actualités récupérées : "
        f"{len(articles)}"
    )

    print()

    return articles


# ============================================================
# EXTRACTION DU VRAI TITRE
# ============================================================

async def extract_article_title(page, url):

    try:

        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        await page.wait_for_timeout(1500)

    except Exception as e:

        print(
            f"   ⚠️ Ouverture article impossible : {e}"
        )

        return ""

    # --------------------------------------------------------
    # 1. H1
    # --------------------------------------------------------

    h1_selectors = [
        "h1",
        "main h1",
        "article h1",
        "[role='main'] h1",
    ]

    for selector in h1_selectors:

        try:

            locator = page.locator(selector)

            count = await locator.count()

            for i in range(
                min(count, 5)
            ):

                try:

                    text = normalize_title(
                        await locator.nth(i).inner_text()
                    )

                    if title_is_valid(text):
                        return text

                except Exception:
                    continue

        except Exception:
            continue

    # --------------------------------------------------------
    # 2. OG TITLE
    # --------------------------------------------------------

    try:

        og = page.locator(
            'meta[property="og:title"]'
        )

        if await og.count() > 0:

            value = await og.first.get_attribute(
                "content"
            )

            if title_is_valid(value):
                return normalize_title(value)

    except Exception:
        pass

    # --------------------------------------------------------
    # 3. TWITTER TITLE
    # --------------------------------------------------------

    try:

        twitter = page.locator(
            'meta[name="twitter:title"]'
        )

        if await twitter.count() > 0:

            value = await twitter.first.get_attribute(
                "content"
            )

            if title_is_valid(value):
                return normalize_title(value)

    except Exception:
        pass

    # --------------------------------------------------------
    # 4. JSON-LD
    # --------------------------------------------------------

    try:

        scripts = page.locator(
            'script[type="application/ld+json"]'
        )

        count = await scripts.count()

        for i in range(count):

            try:

                raw = await scripts.nth(i).inner_text()

                data = json.loads(raw)

                objects = (
                    data
                    if isinstance(data, list)
                    else [data]
                )

                for obj in objects:

                    if not isinstance(obj, dict):
                        continue

                    headline = obj.get(
                        "headline"
                    )

                    if title_is_valid(headline):
                        return normalize_title(
                            headline
                        )

            except Exception:
                continue

    except Exception:
        pass

    # --------------------------------------------------------
    # 5. TITLE HTML
    # --------------------------------------------------------

    try:

        page_title = await page.title()

        if title_is_valid(page_title):

            page_title = re.sub(
                r"\s*\|\s*DOFUS.*$",
                "",
                page_title,
                flags=re.IGNORECASE
            )

            page_title = re.sub(
                r"\s*-\s*DOFUS.*$",
                "",
                page_title,
                flags=re.IGNORECASE
            )

            if title_is_valid(page_title):

                return normalize_title(
                    page_title
                )

    except Exception:
        pass

    return ""


# ============================================================
# TRAITEMENT DES ARTICLES
# ============================================================

async def process_articles(
    page,
    listing_articles,
    cache
):

    print("########################################")
    print(
        f"# URLs Actualités Dofus trouvées : "
        f"{len(listing_articles)}"
    )
    print("########################################")
    print()

    results = []

    for index, article in enumerate(
        listing_articles,
        start=1
    ):

        url = article["url"]

        date_value = article["date"]

        print(
            f"[{index}/{len(listing_articles)}] "
            f"{url}"
        )

        # ----------------------------------------------------
        # TITRE
        # ----------------------------------------------------

        title = await extract_article_title(
            page,
            url
        )

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
        # DATE
        # ----------------------------------------------------

        if date_value:

            print(
                "   📅 Date trouvée via LISTING: "
                f"{format_datetime(date_value)}"
            )

        else:

            # ------------------------------------------------
            # Si le listing ne fournit pas la date,
            # on regarde le cache.
            # ------------------------------------------------

            cached_date = get_cached_date(
                cache,
                url
            )

            if cached_date:

                date_value = cached_date

                print(
                    "   📅 Date trouvée via CACHE: "
                    f"{format_datetime(date_value)}"
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
        #
        # IMPORTANT :
        # On ne met PLUS la date actuelle.
        #
        # Une fausse date actuelle ferait croire à Readybot
        # qu'un ancien article est une nouvelle publication.
        # ----------------------------------------------------

        if not date_value:

            print(
                "   ❌ Article ignoré : "
                "date réelle introuvable."
            )

            print()

            continue

        # ----------------------------------------------------
        # ARTICLE
        # ----------------------------------------------------

        article_data = {
            "title": title,
            "url": url,
            "date": date_value.isoformat(),
            "guid": url,
        }

        results.append(
            article_data
        )

        print(
            f"🟢 {format_datetime(date_value)} "
            f"- {title}"
        )

        print()

        await page.wait_for_timeout(300)

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
    # Nouveaux articles
    # --------------------------------------------------------

    for article in new_articles:

        guid = article.get("guid")

        if not guid:
            continue

        if guid in seen:
            continue

        seen.add(guid)

        merged.append(
            article
        )

    # --------------------------------------------------------
    # Cache
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

        title = normalize_title(
            article.get("title", "")
        )

        url = article.get(
            "url",
            ""
        )

        date_value = article.get(
            "date"
        )

        if not title or not url:
            continue

        # ----------------------------------------------------
        # On ne fabrique surtout pas une date actuelle.
        # ----------------------------------------------------

        if not date_value:
            continue

        parsed = parse_date(
            date_value
        )

        if not parsed:
            continue

        merged.append(
            {
                "title": title,
                "url": url,
                "date": parsed.isoformat(),
                "guid": guid,
            }
        )

    # --------------------------------------------------------
    # TRI
    # --------------------------------------------------------

    def sort_key(article):

        try:

            return datetime.fromisoformat(
                article.get(
                    "date",
                    ""
                )
            )

        except Exception:

            return datetime.min.replace(
                tzinfo=timezone.utc
            )

    merged.sort(
        key=sort_key,
        reverse=True
    )

    return merged[:MAX_ARTICLES]


# ============================================================
# RSS NORMAL
# ============================================================

def generate_normal_rss(articles):

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

        item = f"""
    <item>
      <title>{title}</title>
      <link>{link}</link>
      <guid isPermaLink="true">{guid}</guid>
      <pubDate>{pub_date}</pubDate>
      <description>{description}</description>
    </item>
"""

        items.append(item)

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

        f.write(rss)

    print(
        "🟢 dofus-news.xml généré."
    )


# ============================================================
# RSS DISCORD
# ============================================================

def generate_discord_rss(articles):

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

        f.write(rss)

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

    print("########################################")

    print(
        f"# {len(articles)} "
        f"Actualités Dofus retenues"
    )

    print("########################################")

    print()

    for index, article in enumerate(
        articles,
        start=1
    ):

        date_value = parse_date(
            article.get("date")
        )

        if date_value:

            date_display = format_datetime(
                date_value
            )

        else:

            date_display = "DATE INVALIDE"

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

    print("########################################")

    print(
        "# DOFUS ACTUALITÉS RSS TERMINÉ"
    )

    print("########################################")

    print()


# ============================================================
# EXECUTION
# ============================================================

if __name__ == "__main__":
    asyncio.run(main())
