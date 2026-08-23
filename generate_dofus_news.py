import json
import os
import re

from datetime import datetime, timezone
from email.utils import formatdate
from urllib.parse import urljoin
from xml.etree.ElementTree import Element, SubElement, ElementTree, indent

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://www.dofus.com"

SOURCE_URL = (
    "https://www.dofus.com/fr/mmorpg/actualites/news"
)

OUTPUT = "dofus-news.xml"
DISCORD_OUTPUT = "dofus-news-discord.xml"
CACHE_FILE = "dofus_news_cache.json"

MAX_ARTICLES = 20
MAX_LOAD_MORE_CLICKS = 8

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0 Safari/537.36"
)


# ============================================================
# MOIS FRANÇAIS
# ============================================================

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


# ============================================================
# TITRES À IGNORER
# ============================================================

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
    "voir tous",
    "voir tous les articles",
    "lire la suite",
    "en savoir plus",
    "en savoir+",
    "audience et publicité",
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

    return True


def normalize_url(value):
    if not value:
        return ""

    return (
        urljoin(BASE_URL, value)
        .split("#", 1)[0]
        .rstrip("/")
    )


def extract_article_id(url):
    match = re.search(
        r"/news/(\d+)-",
        url,
        re.IGNORECASE,
    )

    if not match:
        return 0

    try:
        return int(match.group(1))
    except Exception:
        return 0


# ============================================================
# DATES
# ============================================================

def parse_french_date(value):
    if not value:
        return None

    text = clean_text(value).lower()

    # Exemple :
    # 20 août 2026
    # 20 août 2026 à 18h00
    # 20 août 2026 à 18:00

    match = re.search(
        r"\b"
        r"(\d{1,2})\s+"
        r"(janvier|février|fevrier|mars|avril|mai|juin|"
        r"juillet|août|aout|septembre|octobre|novembre|"
        r"décembre|decembre)"
        r"\s+(\d{4})"
        r"(?:\s+(?:à|a|at)\s+"
        r"(\d{1,2})(?::|h)(\d{2}))?"
        r"\b",
        text,
        re.IGNORECASE,
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
        except Exception:
            pass

    # Exemple :
    # 20/08/2026
    # 20/08/2026 18:00

    match = re.search(
        r"\b"
        r"(\d{1,2})[/-]"
        r"(\d{1,2})[/-]"
        r"(\d{4})"
        r"(?:[ T](\d{1,2}):(\d{2}))?"
        r"\b",
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
        except Exception:
            pass

    return None


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
        pass

    return None


def format_pubdate(dt):
    return formatdate(
        dt.timestamp(),
        usegmt=True,
    )


# ============================================================
# CACHE
# ============================================================

def load_cache():
    if not os.path.exists(CACHE_FILE):
        print("Cache Actualités Dofus chargé : 0 articles.")
        return {}

    try:
        with open(
            CACHE_FILE,
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

        if not isinstance(data, dict):
            return {}

        print(
            "Cache Actualités Dofus chargé : "
            f"{len(data)} articles."
        )

        return data

    except Exception as exc:
        print(
            "⚠️ Erreur lecture cache : "
            f"{exc}"
        )
        return {}


def save_cache(cache):
    with open(
        CACHE_FILE,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            cache,
            file,
            ensure_ascii=False,
            indent=2,
        )


# ============================================================
# URL NEWS VALIDE
# ============================================================

def is_valid_news_url(url):
    if not url:
        return False

    return (
        "/fr/mmorpg/actualites/news/"
        in url.lower()
    )


# ============================================================
# EXTRACTION D'UNE CARTE
# ============================================================

def find_article_card(link, article_url):
    """
    Remonte depuis le lien jusqu'au plus petit conteneur
    correspondant uniquement à CET article.

    Cela évite de récupérer par erreur un titre provenant
    d'une autre zone de la page.
    """

    fallback = None

    for level in range(1, 13):

        try:
            parent = link.locator(
                "xpath=" + "/.." * level
            )

            links = parent.locator(
                'a[href*="/fr/mmorpg/actualites/news/"]'
            )

            urls = set()

            for i in range(links.count()):
                href = links.nth(i).get_attribute("href")
                url = normalize_url(href)

                if is_valid_news_url(url):
                    urls.add(url)

            if urls != {article_url}:
                continue

            text = clean_text(
                parent.inner_text(
                    timeout=1500
                )
            )

            if not text:
                continue

            if fallback is None:
                fallback = parent

            if parse_french_date(text):
                return parent

        except Exception:
            continue

    return fallback


# ============================================================
# EXTRACTION TITRE
# ============================================================

def extract_card_title(card, link, card_text):
    """
    Recherche le titre uniquement à l'intérieur de la carte.

    On ne dépend d'aucun titre ou URL spécifique.
    """

    # --------------------------------------------------------
    # 1. Texte du lien lui-même
    # --------------------------------------------------------

    try:
        candidate = clean_text(
            link.inner_text(
                timeout=1500
            )
        )

        if is_valid_title(candidate):
            return candidate

    except Exception:
        pass

    # --------------------------------------------------------
    # 2. Ligne située juste avant la date
    # --------------------------------------------------------

    lines = [
        clean_text(line)
        for line in str(card_text or "").splitlines()
        if clean_text(line)
    ]

    for i, line in enumerate(lines):

        if parse_french_date(line):

            for j in range(
                i - 1,
                max(-1, i - 6),
                -1,
            ):

                candidate = lines[j]

                if parse_french_date(candidate):
                    continue

                if is_valid_title(candidate):
                    return candidate

    # --------------------------------------------------------
    # 3. Headings de la carte
    # --------------------------------------------------------

    try:
        headings = card.locator(
            "h1, h2, h3, h4, h5, h6"
        )

        candidates = []

        for i in range(headings.count()):

            candidate = clean_text(
                headings.nth(i).inner_text(
                    timeout=1500
                )
            )

            if is_valid_title(candidate):
                candidates.append(candidate)

        if candidates:
            return candidates[0]

    except Exception:
        pass

    return ""


# ============================================================
# COLLECTE DES ACTUALITÉS
# ============================================================

def collect_news():

    print("")
    print("========================================")
    print("Ouverture avec Playwright :")
    print(SOURCE_URL)
    print("========================================")

    articles = {}

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page(
            locale="fr-FR",
            user_agent=USER_AGENT,
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
                "❌ Erreur ouverture DOFUS : "
                f"{exc}"
            )

            browser.close()
            return []

        # ----------------------------------------------------
        # SCAN DES CARTES VISIBLES
        # ----------------------------------------------------

        def collect_visible():

            links = page.locator(
                'a[href*="/fr/mmorpg/actualites/news/"]'
            )

            for index in range(
                links.count()
            ):

                try:

                    link = links.nth(index)

                    href = link.get_attribute(
                        "href"
                    )

                    article_url = normalize_url(
                        href
                    )

                    if not is_valid_news_url(
                        article_url
                    ):
                        continue

                    # ----------------------------------------
                    # CARTE
                    # ----------------------------------------

                    card = find_article_card(
                        link,
                        article_url,
                    )

                    if card is None:
                        continue

                    card_text = clean_text(
                        card.inner_text(
                            timeout=1500
                        )
                    )

                    # ----------------------------------------
                    # DATE
                    # ----------------------------------------

                    listing_date = (
                        parse_french_date(
                            card_text
                        )
                    )

                    # Secours si la carte immédiate
                    # ne contient pas la date.

                    if listing_date is None:

                        for level in range(
                            1,
                            5,
                        ):

                            try:

                                parent = link.locator(
                                    "xpath="
                                    + "/.." * level
                                )

                                candidate = (
                                    parse_french_date(
                                        parent.inner_text(
                                            timeout=1500
                                        )
                                    )
                                )

                                if candidate:
                                    listing_date = candidate
                                    break

                            except Exception:
                                continue

                    if listing_date is None:
                        continue

                    # ----------------------------------------
                    # TITRE
                    # ----------------------------------------

                    title = extract_card_title(
                        card,
                        link,
                        card_text,
                    )

                    # ----------------------------------------
                    # ENREGISTREMENT
                    # ----------------------------------------

                    article = {
                        "url": article_url,
                        "title": title,
                        "date": listing_date,
                        "id": extract_article_id(
                            article_url
                        ),
                    }

                    # Une URL peut apparaître plusieurs fois
                    # dans une même carte (image + titre).
                    #
                    # On conserve la meilleure information.

                    if article_url not in articles:

                        articles[article_url] = article

                    else:

                        existing = articles[
                            article_url
                        ]

                        if (
                            not is_valid_title(
                                existing["title"]
                            )
                            and is_valid_title(
                                title
                            )
                        ):
                            existing["title"] = title

                except Exception:
                    continue

        # Premier scan

        collect_visible()

        print(
            "Premier lot : "
            f"{len(articles)} actualités détectées."
        )

        # ----------------------------------------------------
        # VOIR PLUS
        # ----------------------------------------------------

        for click_number in range(
            1,
            MAX_LOAD_MORE_CLICKS + 1,
        ):

            if len(articles) >= MAX_ARTICLES:
                break

            print(
                "🔄 Recherche du bouton VOIR PLUS "
                f"({click_number}/{MAX_LOAD_MORE_CLICKS})..."
            )

            buttons = page.get_by_text(
                "VOIR PLUS",
                exact=True,
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

                    print(
                        "🟢 VOIR PLUS cliqué."
                    )

                    break

                except Exception:
                    continue

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

            before = len(
                articles
            )

            collect_visible()

            after = len(
                articles
            )

            print(
                "Actualités actuellement trouvées : "
                f"{after} "
                f"(+{after - before})"
            )

            if after == before:
                break

        browser.close()

    result = list(
        articles.values()
    )

    print(
        "🟢 Total actualités récupérées : "
        f"{len(result)}"
    )

    return result


# ============================================================
# VÉRIFICATION DU TITRE DU DERNIER ARTICLE
# ============================================================

def verify_latest_title(article):

    if not article:
        return article

    print("")
    print(
        "🔎 Vérification du titre du dernier article :"
    )

    print(
        f"   {article['url']}"
    )

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page(
            locale="fr-FR",
            user_agent=USER_AGENT,
        )

        try:

            page.goto(
                article["url"],
                wait_until="domcontentloaded",
                timeout=60000,
            )

            page.wait_for_timeout(
                1500
            )

            # ------------------------------------------------
            # PRIORITÉ : OG TITLE
            # ------------------------------------------------

            try:

                meta = page.locator(
                    'meta[property="og:title"]'
                )

                if meta.count():

                    title = clean_text(
                        meta.first.get_attribute(
                            "content"
                        )
                    )

                    if is_valid_title(title):

                        article["title"] = title

                        print(
                            "   🏷️ Titre confirmé : "
                            f"{title}"
                        )

                        browser.close()

                        return article

            except Exception:
                pass

            # ------------------------------------------------
            # SECOURS : H1
            # ------------------------------------------------

            try:

                headings = page.locator(
                    "h1"
                )

                for i in range(
                    headings.count()
                ):

                    title = clean_text(
                        headings.nth(i).inner_text(
                            timeout=1500
                        )
                    )

                    if is_valid_title(title):

                        article["title"] = title

                        print(
                            "   🏷️ Titre confirmé : "
                            f"{title}"
                        )

                        break

            except Exception:
                pass

        except Exception as exc:

            print(
                "   ⚠️ Impossible de vérifier "
                f"le titre : {exc}"
            )

        finally:

            browser.close()

    return article


# ============================================================
# CLASSEMENT
# ============================================================

def sort_articles(articles):

    """
    Classement totalement générique.

    PRIORITÉ 1 :
        date de publication

    PRIORITÉ 2 :
        ID DOFUS si plusieurs articles ont exactement
        la même date.

    Aucune URL ou aucun titre n'est connu à l'avance.
    """

    articles = [
        article
        for article in articles
        if article.get("date") is not None
    ]

    articles.sort(
        key=lambda article: (
            article["date"],
            article.get("id", 0),
        ),
        reverse=True,
    )

    return articles


# ============================================================
# RSS
# ============================================================

def create_rss(
    filename,
    channel_title,
    channel_description,
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
    ).text = channel_title

    SubElement(
        channel,
        "link",
    ).text = SOURCE_URL

    SubElement(
        channel,
        "description",
    ).text = channel_description

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

        # IMPORTANT POUR READYBOT
        SubElement(
            item,
            "pubDate",
        ).text = format_pubdate(
            article["date"]
        )

        SubElement(
            item,
            "description",
        ).text = article.get(
            "description",
            article["title"],
        )

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
# PROGRAMME PRINCIPAL
# ============================================================

print("")
print(
    "########################################"
)
print(
    "# Tensho Dofus"
)
print(
    "# ACTUALITÉS / NEWS"
)
print(
    "########################################"
)

cache = load_cache()


# ============================================================
# SCAN
# ============================================================

articles = collect_news()


print("")
print(
    "########################################"
)
print(
    "# URLs Actualités Dofus trouvées : "
    f"{len(articles)}"
)
print(
    "########################################"
)


# ============================================================
# CLASSEMENT
# ============================================================

articles = sort_articles(
    articles
)


# ============================================================
# DERNIER ARTICLE
# ============================================================

if articles:

    articles[0] = verify_latest_title(
        articles[0]
    )


# ============================================================
# LIMITE RSS COMPLET
# ============================================================

articles = articles[
    :MAX_ARTICLES
]


# ============================================================
# AFFICHAGE
# ============================================================

print("")
print(
    "########################################"
)
print(
    f"# {len(articles)} "
    "Actualités Dofus retenues"
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

    print(
        f"    {article['url']}"
    )


# ============================================================
# CACHE DES ARTICLES
# ============================================================

for article in articles:

    article["description"] = (
        article["title"]
    )

    cache[
        article["url"]
    ] = {
        "title": article["title"],
        "description": article["description"],
        "pubDate": format_pubdate(
            article["date"]
        ),
    }


# ============================================================
# RSS COMPLET
# ============================================================

save_cache(
    cache
)

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
    "Génération de dofus-news-discord.xml..."
)


if not articles:

    print(
        "⚠️ Aucune actualité disponible."
    )

    create_rss(
        DISCORD_OUTPUT,
        "DOFUS — Actualités",
        "Dernière actualité officielle française de DOFUS.",
        [],
    )

    print(
        "🟢 Flux Discord vide généré."
    )

else:

    latest = articles[0]

    print("")
    print(
        "🔎 Dernière actualité actuellement "
        "publiée sur DOFUS :"
    )

    print(
        f"   {latest['title']}"
    )

    print(
        f"   {latest['url']}"
    )

    print(
        f"   {format_pubdate(latest['date'])}"
    )


    # ========================================================
    # DERNIER ARTICLE ENVOYÉ
    # ========================================================

    last_sent = cache.get(
        "_discord_last_sent"
    )

    if isinstance(
        last_sent,
        dict,
    ):

        last_sent_url = (
            last_sent.get("url")
        )

    else:

        last_sent_url = None


    # ========================================================
    # NOUVEL ARTICLE
    # ========================================================

    if latest["url"] != last_sent_url:

        print("")
        print(
            "🆕 Nouveau dernier article "
            "à envoyer sur Discord."
        )

        print(
            f"   Ancien : "
            f"{last_sent_url or 'Aucun'}"
        )

        print(
            f"   Nouveau : "
            f"{latest['url']}"
        )


        # ----------------------------------------------------
        # UN SEUL ITEM DANS LE RSS DISCORD
        # ----------------------------------------------------

        create_rss(
            DISCORD_OUTPUT,
            "DOFUS — Actualités",
            "Dernière actualité officielle française de DOFUS.",
            [latest],
        )


        # ----------------------------------------------------
        # SAUVEGARDE
        # ----------------------------------------------------

        cache[
            "_discord_last_sent"
        ] = {
            "url": latest["url"],
            "title": latest["title"],
            "pubDate": format_pubdate(
                latest["date"]
            ),
        }

        save_cache(
            cache
        )

        print(
            "🟢 État Discord sauvegardé."
        )

        print(
            "🟢 dofus-news-discord.xml généré "
            "avec 1 nouvel article."
        )


    # ========================================================
    # DÉJÀ ENVOYÉ
    # ========================================================

    else:

        print("")
        print(
            "ℹ️ Le dernier article DOFUS "
            "a déjà été envoyé."
        )

        print(
            "ℹ️ Aucun nouvel envoi Discord."
        )


        # ----------------------------------------------------
        # IMPORTANT :
        #
        # Le fichier reste un RSS valide avec UN article.
        #
        # Readybot peut donc continuer à le parser.
        # Le script, lui, ne considère pas cet article
        # comme un nouvel envoi.
        # ----------------------------------------------------

        create_rss(
            DISCORD_OUTPUT,
            "DOFUS — Actualités",
            "Dernière actualité officielle française de DOFUS.",
            [latest],
        )

        print(
            "🟢 dofus-news-discord.xml généré "
            "sans nouvel envoi."
        )


# ============================================================
# FIN
# ============================================================

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
