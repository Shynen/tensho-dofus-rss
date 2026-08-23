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

    text = BeautifulSoup(
        str(text),
        "html.parser"
    ).get_text(" ", strip=True)

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def normalize_title(text):
    """
    Nettoyage léger du titre.

    IMPORTANT :
    - Les accents sont conservés.
    - Le texte n'est PAS transformé en slug.
    - Le titre réel fourni par DOFUS est conservé.
    """

    text = clean_text(text)

    if not text:
        return ""

    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)

    # Suppression de quelques suffixes possibles ajoutés
    # par les métadonnées HTML.
    text = re.sub(
        r"\s*\|\s*DOFUS(?:\s+MMORPG)?\s*$",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\s*-\s*DOFUS(?:\s+MMORPG)?\s*$",
        "",
        text,
        flags=re.IGNORECASE
    )

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
        "voir la suite",
        "plus d'informations",
        "plus d’informations",
        "menu",
        "accueil",
    }

    if title.lower() in invalid_titles:
        return False

    # Protection supplémentaire contre les titres composés
    # uniquement d'un bouton/navigation.
    if title.lower().startswith("en savoir"):
        return False

    if title.lower().startswith("actualités récentes"):
        return False

    if title.lower().startswith("actualites recentes"):
        return False

    return True


def parse_date(value):
    if not value:
        return None

    value = str(value).strip()

    if not value:
        return None

    # --------------------------------------------------------
    # Format RFC / HTTP classique
    # --------------------------------------------------------

    try:
        dt = parsedate_to_datetime(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt

    except Exception:
        pass

    # --------------------------------------------------------
    # Formats ISO éventuels
    # --------------------------------------------------------

    iso_formats = [
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ]

    for fmt in iso_formats:

        try:
            dt = datetime.strptime(value, fmt)

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            return dt

        except Exception:
            continue

    return None


def format_rss_date(dt):
    if not dt:
        return format_datetime(datetime.now().astimezone())

    return format_datetime(dt)


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
    # On récupère les liens d'articles.
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

        href = urljoin(BASE_URL, href)

        # On évite la page principale.
        if href.rstrip("/") == NEWS_URL.rstrip("/"):
            continue

        # Nettoyage éventuel des ancres.
        href = href.split("#")[0]

        if href in seen_urls:
            continue

        seen_urls.add(href)

        # ----------------------------------------------------
        # DATE
        # ----------------------------------------------------

        date_value = None

        try:

            # On remonte quelques niveaux pour trouver
            # la carte/listing de l'article.

            container = link.locator(
                "xpath=ancestor::*["
                "self::article or "
                "self::li or "
                "contains(@class,'card') or "
                "contains(@class,'item')"
                "][1]"
            )

            if await container.count() > 0:

                time_locators = [
                    "time",
                    "[datetime]",
                    "[data-date]",
                    "[class*='date']",
                ]

                for selector in time_locators:

                    loc = container.locator(selector)

                    if await loc.count() == 0:
                        continue

                    for i in range(
                        min(await loc.count(), 3)
                    ):

                        try:

                            element = loc.nth(i)

                            datetime_attr = (
                                await element.get_attribute(
                                    "datetime"
                                )
                            )

                            if datetime_attr:

                                date_value = parse_date(
                                    datetime_attr
                                )

                            if not date_value:

                                text = await element.inner_text()

                                date_value = parse_date(text)

                            if date_value:
                                break

                        except Exception:
                            continue

                    if date_value:
                        break

        except Exception:
            pass

        # ----------------------------------------------------
        # Recherche plus large si la date n'a pas été trouvée.
        # ----------------------------------------------------

        if not date_value:

            try:

                parent = link.locator("xpath=..")

                for _ in range(4):

                    if await parent.count() == 0:
                        break

                    html = await parent.inner_html()

                    match = re.search(
                        r"\d{4}-\d{2}-\d{2}"
                        r"(?:T\d{2}:\d{2}:\d{2}"
                        r"(?:\.\d+)?"
                        r"(?:Z|[+-]\d{2}:?\d{2})?)?",
                        html
                    )

                    if match:

                        date_value = parse_date(
                            match.group(0)
                        )

                    if date_value:
                        break

                    parent = parent.locator("xpath=..")

            except Exception:
                pass

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

    """
    Récupère le TITRE RÉEL de l'article.

    Ordre de priorité :

      1. og:title
      2. twitter:title
      3. JSON-LD headline
      4. H1 réellement pertinent
      5. <title>

    IMPORTANT :

    On ne fait PAS confiance au premier H1 trouvé.

    Le site DOFUS contient des éléments de navigation
    pouvant apparaître comme H1 et retourner :
      - Actualités récentes
      - En savoir+

    Ces valeurs sont explicitement rejetées.

    Le slug URL n'est jamais utilisé comme titre.
    """

    try:

        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        # Laisser le temps au rendu JS de DOFUS.
        await page.wait_for_timeout(1800)

    except Exception as e:

        print(
            f"   ⚠️ Ouverture article impossible : {e}"
        )

        return ""

    # ========================================================
    # 1. OG:TITLE
    # ========================================================

    try:

        og_selectors = [
            'meta[property="og:title"]',
            'meta[property="og:title"][content]',
        ]

        for selector in og_selectors:

            locator = page.locator(selector)

            if await locator.count() == 0:
                continue

            for i in range(
                min(await locator.count(), 3)
            ):

                try:

                    value = await locator.nth(i).get_attribute(
                        "content"
                    )

                    value = normalize_title(value)

                    if title_is_valid(value):
                        return value

                except Exception:
                    continue

    except Exception:
        pass

    # ========================================================
    # 2. TWITTER TITLE
    # ========================================================

    try:

        twitter_selectors = [
            'meta[name="twitter:title"]',
            'meta[property="twitter:title"]',
            'meta[name="twitter:title"][content]',
        ]

        for selector in twitter_selectors:

            locator = page.locator(selector)

            if await locator.count() == 0:
                continue

            for i in range(
                min(await locator.count(), 3)
            ):

                try:

                    value = await locator.nth(i).get_attribute(
                        "content"
                    )

                    value = normalize_title(value)

                    if title_is_valid(value):
                        return value

                except Exception:
                    continue

    except Exception:
        pass

    # ========================================================
    # 3. JSON-LD
    # ========================================================

    try:

        scripts = page.locator(
            'script[type="application/ld+json"]'
        )

        count = await scripts.count()

        for i in range(count):

            try:

                raw = await scripts.nth(i).inner_text()

                if not raw.strip():
                    continue

                data = json.loads(raw)

                objects = []

                if isinstance(data, list):

                    objects.extend(data)

                elif isinstance(data, dict):

                    objects.append(data)

                    # Certains JSON-LD utilisent @graph.
                    graph = data.get("@graph")

                    if isinstance(graph, list):
                        objects.extend(graph)

                for obj in objects:

                    if not isinstance(obj, dict):
                        continue

                    possible_titles = [
                        obj.get("headline"),
                        obj.get("name"),
                    ]

                    for headline in possible_titles:

                        headline = normalize_title(
                            headline
                        )

                        if title_is_valid(headline):
                            return headline

            except Exception:
                continue

    except Exception:
        pass

    # ========================================================
    # 4. H1 PERTINENT
    # ========================================================

    try:

        # On cherche plusieurs H1 possibles mais on rejette
        # explicitement les éléments de navigation.

        h1_selectors = [
            "main h1",
            "article h1",
            "[role='main'] h1",
            "h1",
        ]

        candidates = []

        for selector in h1_selectors:

            locator = page.locator(selector)

            if await locator.count() == 0:
                continue

            for i in range(
                min(await locator.count(), 10)
            ):

                try:

                    text = await locator.nth(i).inner_text()

                    text = normalize_title(text)

                    if not text:
                        continue

                    if not title_is_valid(text):
                        continue

                    # Évite les doublons.
                    if text not in candidates:
                        candidates.append(text)

                except Exception:
                    continue

        # On privilégie les H1 longs et significatifs.
        # Les titres d'articles DOFUS sont généralement
        # beaucoup plus longs que les éléments de navigation.

        candidates.sort(
            key=lambda value: (
                len(value),
                value.count(" ")
            ),
            reverse=True
        )

        if candidates:

            return candidates[0]

    except Exception:
        pass

    # ========================================================
    # 5. TITLE HTML
    # ========================================================

    try:

        page_title = await page.title()

        page_title = normalize_title(page_title)

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

            page_title = normalize_title(
                page_title
            )

            if title_is_valid(page_title):
                return page_title

    except Exception:
        pass

    # ========================================================
    # AUCUN TITRE FIABLE
    # ========================================================

    return ""


# ============================================================
# TRAITEMENT DES ARTICLES
# ============================================================

async def process_articles(page, listing_articles):

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
        # TITRE RÉEL
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

            print(
                "   ⚠️ Date introuvable."
            )

        # ----------------------------------------------------
        # TITRE ABSENT
        #
        # IMPORTANT :
        # On n'utilise PAS le slug URL.
        # ----------------------------------------------------

        if not title:

            print(
                "   ❌ Article ignoré : "
                "titre réel introuvable."
            )

            print()

            continue

        # ----------------------------------------------------
        # DATE ABSENTE
        #
        # Dernier recours : date actuelle.
        # Cela garantit un pubDate valide.
        # ----------------------------------------------------

        if not date_value:

            date_value = datetime.now().astimezone()

        article_data = {
            "title": title,
            "url": url,
            "date": date_value.isoformat(),
            "guid": url,
        }

        results.append(article_data)

        print(
            f"🟢 {format_datetime(date_value)} "
            f"- {title}"
        )

        print()

        # Petite pause entre les pages.
        await page.wait_for_timeout(300)

        if len(results) >= MAX_ARTICLES:
            break

    return results


# ============================================================
# FUSION AVEC LE CACHE
# ============================================================

def merge_with_cache(
    new_articles,
    cache
):

    merged = []
    seen = set()

    # --------------------------------------------------------
    # Les nouveaux articles passent en premier.
    # --------------------------------------------------------

    for article in new_articles:

        guid = article.get("guid")

        if not guid or guid in seen:
            continue

        seen.add(guid)

        merged.append(article)

    # --------------------------------------------------------
    # Puis anciens articles du cache.
    # --------------------------------------------------------

    for article in cache:

        guid = (
            article.get("guid")
            or article.get("url")
        )

        if not guid or guid in seen:
            continue

        seen.add(guid)

        title = normalize_title(
            article.get("title", "")
        )

        url = article.get("url", "")
        date_value = article.get("date")

        if not title or not url:
            continue

        if not date_value:
            date_value = (
                datetime.now()
                .astimezone()
                .isoformat()
            )

        merged.append(
            {
                "title": title,
                "url": url,
                "date": date_value,
                "guid": guid,
            }
        )

    # --------------------------------------------------------
    # Tri par date décroissante.
    # --------------------------------------------------------

    def sort_key(article):

        try:

            return datetime.fromisoformat(
                article.get("date", "")
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

        try:

            date_value = datetime.fromisoformat(
                article["date"]
            )

        except Exception:

            date_value = (
                datetime.now()
                .astimezone()
            )

        pub_date = escape_xml(
            format_rss_date(date_value)
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

    # --------------------------------------------------------
    # IMPORTANT :
    #
    # Flux volontairement extrêmement minimal
    # pour Readybot / Discord.
    #
    # Uniquement :
    #   title
    #   link
    #   guid
    #   description
    #   pubDate
    #
    # Le pubDate est obligatoire pour que Readybot
    # puisse correctement détecter les nouveaux articles.
    # --------------------------------------------------------

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

        try:

            date_value = datetime.fromisoformat(
                article["date"]
            )

        except Exception:

            date_value = (
                datetime.now()
                .astimezone()
            )

        pub_date = escape_xml(
            format_rss_date(date_value)
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
            await get_listing_articles(page)
        )

        # ----------------------------------------------------
        # 2. ARTICLES INDIVIDUELS
        # ----------------------------------------------------

        new_articles = (
            await process_articles(
                page,
                listing_articles
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

        try:

            date_value = datetime.fromisoformat(
                article["date"]
            )

            date_display = format_datetime(
                date_value
            )

        except Exception:

            date_display = article["date"]

        print(
            f"{index:02d}. "
            f"{date_display} - "
            f"{article['title']}"
        )

    print()

    # --------------------------------------------------------
    # 4. SAUVEGARDE CACHE
    # --------------------------------------------------------

    save_cache(articles)

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
    print("# DOFUS ACTUALITÉS RSS TERMINÉ")
    print("########################################")
    print()


# ============================================================
# LANCEMENT
# ============================================================

if __name__ == "__main__":
    asyncio.run(main())
