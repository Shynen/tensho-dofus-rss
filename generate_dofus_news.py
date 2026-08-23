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


INVALID_TITLES = {
    "",
    "tous",
    "découvrir",
    "decouvrir",
    "actualités",
    "actualites",
    "actualités récentes",
    "actualites recentes",
    "news",
    "voir plus",
    "lire la suite",
    "en savoir plus",
    "en savoir+",
}


# ============================================================
# UTILITAIRES
# ============================================================

def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_title(value):
    return clean_text(value).lower()


def is_valid_title(value):
    title = clean_text(value)

    if not title:
        return False

    if normalize_title(title) in INVALID_TITLES:
        return False

    if len(title) < 5:
        return False

    if len(title) > 250:
        return False

    # Un titre ne doit pas être uniquement une date.
    if parse_french_date(title) is not None:
        return False

    return True


def parse_date(value):
    if not value:
        return None

    value = clean_text(value)

    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"

        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except Exception:
        pass

    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except Exception:
        return None


def parse_french_date(value):
    if not value:
        return None

    text = clean_text(value).lower()

    # Exemple :
    # 20 août 2026
    # 20 août 2026 à 17h00
    # 20 août 2026 à 17:00
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
                FRENCH_MONTHS[match.group(2).lower()],
                int(match.group(1)),
                int(match.group(4) or 0),
                int(match.group(5) or 0),
                tzinfo=timezone.utc,
            )
        except ValueError:
            return None

    # Exemple :
    # 20/08/2026
    # 20-08-2026
    match = re.search(
        r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})"
        r"(?:[ T](\d{1,2}):(\d{2}))?",
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
            return None

    return None


def format_pubdate(dt):
    return formatdate(
        dt.timestamp(),
        usegmt=True,
    )


def extract_article_id(url):
    """
    Récupère l'identifiant numérique DOFUS présent dans l'URL.

    Exemple :
    /1771404-ankama-live-...
    -> 1771404
    """

    match = re.search(
        r"/news/(\d+)-",
        url,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    try:
        return int(match.group(1))
    except ValueError:
        return None


# ============================================================
# CACHE
# ============================================================

def load_cache():
    if not os.path.exists(CACHE_FILE):
        print(
            "Cache Actualités Dofus chargé : 0 articles."
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


# ============================================================
# VALIDATION URL
# ============================================================

def is_valid_news_url(url):
    value = url.lower()

    return (
        "dofus.com" in value
        and "/fr/mmorpg/actualites/news/" in value
        and value.rstrip("/") != SOURCE_URL.rstrip("/")
    )


# ============================================================
# FALLBACK GOOGLE NEWS
# ============================================================

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


# ============================================================
# RÉCUPÉRATION DU LISTING DOFUS
# ============================================================

def collect_news_urls():

    print("")
    print("========================================")
    print("Ouverture avec Playwright :")
    print(SOURCE_URL)
    print("========================================")

    # URL -> date potentielle du listing
    news_data = {}

    # URL -> titre potentiel du listing
    news_titles = {}

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

            return {}, {}

        def collect_visible_urls():

            before = len(news_data)

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

                    if not is_valid_news_url(full_url):
                        continue

                    # ------------------------------------------------
                    # TITRE DU LISTING
                    # ------------------------------------------------

                    listing_title = None

                    for level in range(1, 7):

                        try:

                            parent = link.locator(
                                "xpath=" + "/.." * level
                            )

                            for selector in (
                                "h1",
                                "h2",
                                "h3",
                                "h4",
                                "h5",
                                "h6",
                            ):

                                headings = parent.locator(
                                    selector
                                )

                                for j in range(
                                    headings.count()
                                ):

                                    candidate = clean_text(
                                        headings.nth(j).inner_text(
                                            timeout=2000
                                        )
                                    )

                                    if not is_valid_title(
                                        candidate
                                    ):
                                        continue

                                    listing_title = candidate

                                    break

                                if listing_title:
                                    break

                            if listing_title:
                                break

                        except Exception:
                            continue

                    # Fallback : texte du lien
                    if not listing_title:

                        try:

                            candidate = clean_text(
                                link.inner_text(
                                    timeout=2000
                                )
                            )

                            if is_valid_title(
                                candidate
                            ):
                                listing_title = candidate

                        except Exception:
                            pass

                    if listing_title:

                        news_titles[
                            full_url
                        ] = listing_title

                    # ------------------------------------------------
                    # DATE DU LISTING
                    # ------------------------------------------------

                    listing_date = None

                    for level in range(1, 7):

                        try:

                            parent = link.locator(
                                "xpath=" + "/.." * level
                            )

                            card_text = clean_text(
                                parent.inner_text(
                                    timeout=2000
                                )
                            )

                            # IMPORTANT :
                            #
                            # On ne prend une date que si le
                            # conteneur contient également le titre.
                            #
                            # Cela évite de récupérer la date
                            # d'un élément global de la page,
                            # notamment d'une actualité mise en avant.

                            if (
                                listing_title
                                and listing_title.lower()
                                not in card_text.lower()
                            ):
                                continue

                            candidate_date = (
                                parse_french_date(
                                    card_text
                                )
                            )

                            if candidate_date is not None:

                                listing_date = (
                                    candidate_date
                                )

                                break

                        except Exception:
                            continue

                    if full_url not in news_data:

                        news_data[
                            full_url
                        ] = listing_date

                    elif (
                        news_data[full_url] is None
                        and listing_date is not None
                    ):

                        news_data[
                            full_url
                        ] = listing_date

                except Exception:
                    pass

            return (
                len(news_data) - before
            )

        # Premier passage
        collect_visible_urls()

        dated = sum(
            1
            for value in news_data.values()
            if value is not None
        )

        print(
            f"Premier lot : "
            f"{len(news_data)} actualités détectées."
        )

        print(
            f"📅 Dates trouvées dans la liste : "
            f"{dated}/{len(news_data)}"
        )

        # ------------------------------------------------------------
        # VOIR PLUS
        # ------------------------------------------------------------

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

    # ------------------------------------------------------------
    # FALLBACK
    # ------------------------------------------------------------

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

            news_data[
                fallback_url
            ] = None

    print(
        f"🟢 Total actualités récupérées : "
        f"{len(news_data)}"
    )

    return news_data, news_titles


# ============================================================
# EXTRACTION DATE PAGE ARTICLE
# ============================================================

def extract_date_from_soup(soup):

    # ------------------------------------------------------------
    # JSON-LD
    # ------------------------------------------------------------

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

                if not isinstance(
                    obj,
                    dict,
                ):
                    continue

                # DATEPUBLISHED DOIT ÊTRE PRIORITAIRE.
                #
                # DateModified ne doit pas transformer
                # une ancienne actualité en nouvelle actualité.

                for key in (
                    "datePublished",
                    "dateCreated",
                ):

                    value = obj.get(key)

                    if not value:
                        continue

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

    # ------------------------------------------------------------
    # META
    # ------------------------------------------------------------

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

    # ------------------------------------------------------------
    # TIME
    # ------------------------------------------------------------

    for node in soup.find_all("time"):

        raw_value = node.get(
            "datetime"
        )

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

            return (
                dt,
                "TIME",
            )

    # ------------------------------------------------------------
    # TEXTE VISIBLE
    # ------------------------------------------------------------

    visible_text = clean_text(
        soup.get_text(
            " ",
            strip=True,
        )
    )

    pattern = (
        r"\b\d{1,2}\s+"
        r"(?:janvier|février|fevrier|mars|avril|mai|juin|"
        r"juillet|août|aout|septembre|octobre|novembre|"
        r"décembre|decembre)"
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


# ============================================================
# EXTRACTION ARTICLE
# ============================================================

def extract_article(
    url,
    cache,
    listing_date=None,
    listing_title=None,
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
            f"⚠️ Impossible de charger : {exc}"
        )

        return None

    cached = cache.get(
        url,
        {},
    )

    cached_title = clean_text(
        cached.get("title")
    )

    cached_date = parse_date(
        cached.get("pubDate")
    )

    # ============================================================
    # TITRE
    # ============================================================

    title = ""

    # Le listing est utilisé uniquement si son titre
    # est réellement exploitable.
    if is_valid_title(
        listing_title
    ):

        title = clean_text(
            listing_title
        )

    # Cache
    if not title and is_valid_title(
        cached_title
    ):

        title = cached_title

    # JSON-LD headline
    if not title:

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

                if not isinstance(
                    obj,
                    dict,
                ):
                    continue

                headline = clean_text(
                    obj.get("headline")
                )

                if is_valid_title(
                    headline
                ):

                    title = headline
                    break

            if title:
                break

    # OpenGraph
    if not title:

        meta = soup.find(
            "meta",
            attrs={
                "property": "og:title"
            },
        )

        if meta:

            candidate = clean_text(
                meta.get("content")
            )

            if is_valid_title(
                candidate
            ):

                title = candidate

    # H1
    if not title:

        h1 = soup.find("h1")

        if h1:

            candidate = clean_text(
                h1.get_text(
                    " ",
                    strip=True,
                )
            )

            if is_valid_title(
                candidate
            ):

                title = candidate

    # Title HTML
    if not title and soup.title:

        candidate = clean_text(
            soup.title.get_text()
        )

        if is_valid_title(
            candidate
        ):

            title = candidate

    # Slug
    if not title:

        title = (
            url.rstrip("/")
            .split("/")[-1]
            .replace("-", " ")
            .strip()
            .title()
        )

    # ============================================================
    # DATE
    # ============================================================

    dt, date_source = (
        extract_date_from_soup(
            soup
        )
    )

    # ------------------------------------------------------------
    # IMPORTANT :
    #
    # La date publiée de la page individuelle est prioritaire.
    # Le listing ne sert qu'en fallback.
    # ------------------------------------------------------------

    if dt is None:

        if cached_date is not None:

            dt = cached_date
            date_source = "CACHE"

        elif listing_date is not None:

            dt = listing_date
            date_source = "LISTING"

    if dt is None:

        print(
            "⚠️ Date introuvable."
        )

        return None

    # ============================================================
    # DESCRIPTION
    # ============================================================

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


# ============================================================
# CORRECTION DES DATES DU LISTING
# ============================================================

def repair_suspicious_listing_dates(
    articles,
    cache,
):
    """
    Corrige les cas où le listing DOFUS attribue
    une date récente à une ancienne actualité mise
    en avant.

    Exemple du bug actuel :

    Saison Ocre
    ID 1770807
    listing -> 20 août 2026
    vraie date -> 07 juillet 2026

    Les identifiants des articles DOFUS sont globalement
    croissants avec leur publication.

    Si une actualité possède une date très récente mais
    un ID nettement inférieur à d'autres actualités datées
    du même moment, on considère cette date comme suspecte.

    Le cache est alors prioritaire.
    """

    if not articles:
        return articles

    # Articles avec ID connu
    known = []

    for article in articles:

        article_id = extract_article_id(
            article["url"]
        )

        if article_id is not None:

            known.append(
                (
                    article,
                    article_id,
                )
            )

    if len(known) < 2:
        return articles

    for article, article_id in known:

        suspicious = False

        for other, other_id in known:

            if other is article:
                continue

            # Un article avec un ID supérieur
            # ne devrait normalement pas être plus ancien
            # de manière flagrante.

            if other_id > article_id:

                delta_days = (
                    article["date"]
                    - other["date"]
                ).total_seconds() / 86400

                # L'article actuel est au moins 3 jours
                # plus récent qu'un article ayant un ID supérieur.
                #
                # C'est exactement le type de comportement
                # provoqué par une actualité épinglée.

                if delta_days > 3:

                    suspicious = True
                    break

        if not suspicious:
            continue

        cached = cache.get(
            article["url"],
            {},
        )

        cached_date = parse_date(
            cached.get("pubDate")
        )

        cached_title = clean_text(
            cached.get("title")
        )

        print("")
        print(
            "⚠️ Date potentiellement "
            "incorrecte détectée :"
        )

        print(
            f"   {article['title']}"
        )

        print(
            f"   ID : {article_id}"
        )

        print(
            f"   Date actuelle : "
            f"{format_pubdate(article['date'])}"
        )

        if cached_date is not None:

            print(
                "   🛠️ Correction via CACHE : "
                f"{format_pubdate(cached_date)}"
            )

            article["date"] = cached_date

            if is_valid_title(
                cached_title
            ):

                article["title"] = (
                    cached_title
                )

        else:

            print(
                "   ℹ️ Aucun cache disponible : "
                "date conservée."
            )

    return articles


# ============================================================
# RSS
# ============================================================

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

        # IMPORTANT :
        # pubDate = date de publication réelle.
        # Jamais la date d'exécution du workflow.

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

    tree = ElementTree(
        rss
    )

    indent(
        tree,
        space="  ",
    )

    tree.write(
        filename,
        encoding="utf-8",
        xml_declaration=True,
    )


# ============================================================
# MAIN
# ============================================================

print("")
print("########################################")
print("# Tensho Dofus")
print("# ACTUALITÉS FRANÇAISES")
print("########################################")


cache = load_cache()


news_data, news_titles = (
    collect_news_urls()
)


print("")
print("########################################")
print(
    f"# URLs Actualités Dofus trouvées : "
    f"{len(news_data)}"
)
print("########################################")


articles = []


for index, (
    url,
    listing_date,
) in enumerate(
    news_data.items(),
    start=1,
):

    print("")
    print(
        f"[{index}/{len(news_data)}] "
        f"{url}"
    )

    listing_title = (
        news_titles.get(url)
    )

    if listing_title:

        print(
            f"   🏷️ Titre trouvé via LISTING: "
            f"{listing_title}"
        )

    article = extract_article(
        url,
        cache,
        listing_date=listing_date,
        listing_title=listing_title,
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


# ============================================================
# UNE URL = UN ARTICLE
# ============================================================

unique_articles = {}

for article in articles:

    url = article["url"]

    if (
        url not in unique_articles
        or article["date"]
        > unique_articles[url]["date"]
    ):

        unique_articles[url] = article


articles = list(
    unique_articles.values()
)


# ============================================================
# RÉPARATION DES DATES SUSPECTES
# ============================================================

articles = (
    repair_suspicious_listing_dates(
        articles,
        cache,
    )
)


# ============================================================
# TRI DU PLUS RÉCENT AU PLUS ANCIEN
# ============================================================

articles.sort(
    key=lambda article: article["date"],
    reverse=True,
)


articles = articles[
    :MAX_ARTICLES
]


# ============================================================
# AFFICHAGE FINAL
# ============================================================

print("")
print("########################################")
print(
    f"# {len(articles)} "
    f"Actualités Dofus retenues"
)
print("########################################")
print("")


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


# ============================================================
# CACHE
# ============================================================

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


# ============================================================
# RSS COMPLET
# ============================================================

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


# ============================================================
# RSS DISCORD
# ============================================================

print("")
print(
    "Génération de "
    "dofus-news-discord.xml..."
)


# IMPORTANT :
#
# Le flux Discord contient EXACTEMENT
# UN SEUL ARTICLE :
#
# le plus récent.
#
# Le flux principal conserve les 20 articles.

discord_articles = articles[:1]


create_rss(
    DISCORD_OUTPUT,
    "DOFUS — Actualités",
    "Dernière actualité officielle française de DOFUS.",
    discord_articles,
)


print(
    "🟢 dofus-news-discord.xml généré."
)


print("")
print("########################################")
print("# DOFUS ACTUALITÉS RSS TERMINÉ")
print("########################################")
print("")
