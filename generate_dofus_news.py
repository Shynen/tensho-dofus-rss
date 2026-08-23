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


# ============================================================
# USER AGENT
# ============================================================

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0 Safari/537.36"
)


# ============================================================
# TITRES INVALIDES
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
}


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
# UTILITAIRES
# ============================================================

def clean_text(value):
    return re.sub(
        r"\s+",
        " ",
        str(value or ""),
    ).strip()


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


# ============================================================
# DATE
# ============================================================

def parse_date(value):

    if not value:
        return None

    value = clean_text(value)

    # ISO
    try:

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

    # RFC / HTTP
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
        pass

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
        r"\b"
        r"(\d{1,2})\s+"
        r"(janvier|février|fevrier|mars|avril|mai|juin|"
        r"juillet|août|aout|septembre|octobre|novembre|"
        r"décembre|decembre)"
        r"\s+(\d{4})"
        r"(?:\s+(?:à|a|at)\s+"
        r"(\d{1,2})"
        r"(?::|h)"
        r"(\d{2}))?"
        r"\b",
        text,
        flags=re.IGNORECASE,
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

        except Exception:
            pass

    # Exemple :
    # 20/08/2026
    # 20/08/2026 17:00

    match = re.search(
        r"\b"
        r"(\d{1,2})[/-]"
        r"(\d{1,2})[/-]"
        r"(\d{4})"
        r"(?:[ T]"
        r"(\d{1,2}):"
        r"(\d{2}))?"
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


def format_pubdate(dt):

    return formatdate(
        dt.timestamp(),
        usegmt=True,
    )


# ============================================================
# ID DOFUS
# ============================================================

def extract_article_id(url):

    match = re.search(
        r"/news/(\d+)-",
        url,
        flags=re.IGNORECASE,
    )

    if not match:
        return 0

    try:
        return int(match.group(1))

    except Exception:
        return 0


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

        return {}

    try:

        with open(
            CACHE_FILE,
            "r",
            encoding="utf-8",
        ) as file:

            data = json.load(file)

        if not isinstance(
            data,
            dict,
        ):

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
# URL VALIDE
# ============================================================

def is_valid_news_url(url):

    if not url:
        return False

    value = url.lower()

    return (
        "/fr/mmorpg/actualites/news/"
        in value
        and
        "/news/" in value
    )


# ============================================================
# PLAYWRIGHT
# ============================================================

def collect_news():

    print("")
    print(
        "========================================"
    )
    print(
        "Ouverture avec Playwright :"
    )
    print(SOURCE_URL)
    print(
        "========================================"
    )

    # URL -> informations du listing
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

            page.wait_for_timeout(
                4000
            )

        except Exception as exc:

            print(
                "❌ Erreur ouverture DOFUS : "
                f"{exc}"
            )

            browser.close()

            return []

        # --------------------------------------------------------
        # COLLECTE
        # --------------------------------------------------------

        def collect_visible():

            links = page.locator(
                'a[href*="/fr/mmorpg/actualites/news/"]'
            )

            for index in range(
                links.count()
            ):

                try:

                    link = links.nth(
                        index
                    )

                    href = link.get_attribute(
                        "href"
                    )

                    if not href:
                        continue

                    url = urljoin(
                        BASE_URL,
                        href,
                    )

                    url = (
                        url
                        .split("#", 1)[0]
                        .rstrip("/")
                    )

                    if not is_valid_news_url(
                        url
                    ):
                        continue

                    # ------------------------------------------------
                    # TITRE
                    # ------------------------------------------------

                    title = None

                    # On remonte progressivement dans
                    # les conteneurs de la carte.

                    for level in range(
                        1,
                        7,
                    ):

                        try:

                            parent = link.locator(
                                "xpath="
                                + "/.." * level
                            )

                            for selector in (
                                "h1",
                                "h2",
                                "h3",
                                "h4",
                                "h5",
                                "h6",
                            ):

                                headings = (
                                    parent.locator(
                                        selector
                                    )
                                )

                                for h in range(
                                    headings.count()
                                ):

                                    candidate = clean_text(
                                        headings.nth(h).inner_text(
                                            timeout=1500
                                        )
                                    )

                                    if is_valid_title(
                                        candidate
                                    ):

                                        title = (
                                            candidate
                                        )

                                        break

                                if title:
                                    break

                            if title:
                                break

                        except Exception:
                            continue

                    # Fallback texte du lien

                    if not title:

                        try:

                            candidate = clean_text(
                                link.inner_text(
                                    timeout=1500
                                )
                            )

                            if is_valid_title(
                                candidate
                            ):

                                title = candidate

                        except Exception:
                            pass

                    # ------------------------------------------------
                    # DATE DU LISTING
                    # ------------------------------------------------

                    listing_date = None

                    for level in range(
                        1,
                        7,
                    ):

                        try:

                            parent = link.locator(
                                "xpath="
                                + "/.." * level
                            )

                            text = clean_text(
                                parent.inner_text(
                                    timeout=1500
                                )
                            )

                            if not text:
                                continue

                            candidate_date = (
                                parse_french_date(
                                    text
                                )
                            )

                            if candidate_date:

                                listing_date = (
                                    candidate_date
                                )

                                break

                        except Exception:
                            continue

                    # ------------------------------------------------
                    # SAUVEGARDE
                    # ------------------------------------------------

                    if url not in articles:

                        articles[url] = {
                            "url": url,
                            "title": title or "",
                            "date": listing_date,
                            "id": extract_article_id(
                                url
                            ),
                        }

                    else:

                        # Si une deuxième occurrence
                        # fournit une date alors que
                        # la première n'en avait pas.

                        if (
                            articles[url]["date"]
                            is None
                            and listing_date
                        ):

                            articles[url]["date"] = (
                                listing_date
                            )

                        if (
                            not is_valid_title(
                                articles[url]["title"]
                            )
                            and is_valid_title(
                                title
                            )
                        ):

                            articles[url]["title"] = (
                                title
                            )

                except Exception:
                    continue

        # Premier passage
        collect_visible()

        print(
            "Premier lot : "
            f"{len(articles)} actualités détectées."
        )

        dated_count = sum(
            1
            for article in articles.values()
            if article["date"] is not None
        )

        print(
            "📅 Dates trouvées dans la liste : "
            f"{dated_count}/{len(articles)}"
        )

        # --------------------------------------------------------
        # VOIR PLUS
        # --------------------------------------------------------

        for click_number in range(
            1,
            MAX_LOAD_MORE_CLICKS + 1,
        ):

            if len(articles) >= MAX_ARTICLES:

                break

            print(
                "🔄 Recherche du bouton "
                f"VOIR PLUS "
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

                    button = buttons.nth(
                        i
                    )

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
                "Actualités actuellement "
                f"trouvées : {after} "
                f"(+{after - before})"
            )

            if after == before:

                break

        browser.close()

    # --------------------------------------------------------
    # LISTE FINALE
    # --------------------------------------------------------

    result = list(
        articles.values()
    )

    print(
        "🟢 Total actualités récupérées : "
        f"{len(result)}"
    )

    return result


# ============================================================
# TITRE DE SECOURS
# ============================================================

def make_fallback_title(url):

    slug = (
        url.rstrip("/")
        .split("/")[-1]
    )

    # Supprime l'ID numérique
    slug = re.sub(
        r"^\d+-",
        "",
        slug,
    )

    return (
        slug
        .replace("-", " ")
        .strip()
        .title()
    )


# ============================================================
# DESCRIPTION
# ============================================================

def get_description(
    page,
    title,
):

    description = ""

    # Meta description

    try:

        meta = page.locator(
            'meta[name="description"]'
        )

        if meta.count():

            description = clean_text(
                meta.first.get_attribute(
                    "content"
                )
            )

    except Exception:
        pass

    # OpenGraph

    if not description:

        try:

            meta = page.locator(
                'meta[property="og:description"]'
            )

            if meta.count():

                description = clean_text(
                    meta.first.get_attribute(
                        "content"
                    )
                )

        except Exception:
            pass

    return (
        description
        or title
    )


# ============================================================
# ENRICHISSEMENT DES ARTICLES
# ============================================================

def enrich_articles(
    articles,
):

    print("")

    for index, article in enumerate(
        articles,
        start=1,
    ):

        print(
            f"[{index}/{len(articles)}] "
            f"{article['url']}"
        )

        print(
            "   🏷️ Titre trouvé via LISTING: "
            f"{article['title']}"
        )

        if article["date"]:

            print(
                "   📅 Date trouvée via LISTING: "
                f"{format_pubdate(article['date'])}"
            )

        # --------------------------------------------------------
        # Si le titre est invalide,
        # on essaie la page individuelle.
        #
        # IMPORTANT :
        # on ne remplace PAS une bonne date du listing
        # par une date du cache.
        # --------------------------------------------------------

        if is_valid_title(
            article["title"]
        ):

            if article["date"]:

                print(
                    f"🟢 "
                    f"{format_pubdate(article['date'])} "
                    f"- "
                    f"{article['title']}"
                )

            continue

        # Fallback titre depuis URL

        article["title"] = (
            make_fallback_title(
                article["url"]
            )
        )

        print(
            "   🛠️ Titre corrigé depuis URL : "
            f"{article['title']}"
        )

    return articles


# ============================================================
# CLASSEMENT
# ============================================================

def sort_articles(
    articles,
):

    # --------------------------------------------------------
    # LOGIQUE DÉFINITIVE :
    #
    # 1. DATE DE PUBLICATION DU LISTING
    # 2. ID DOFUS EN CAS D'ÉGALITÉ
    #
    # Le cache n'intervient JAMAIS ici.
    #
    # Exemple actuel :
    #
    # Saison Ocre       20/08 ID 1770807
    # Ankama Live       20/08 ID 1771404
    # Packs classe      20/08 ID 1771141
    # ZEVENT             20/08 ID 1771339
    #
    # => Ankama Live = ID le plus élevé
    #
    # Cela permet de neutraliser l'article mis
    # en avant placé artificiellement en première position.
    # --------------------------------------------------------

    articles = [
        article
        for article in articles
        if article["date"] is not None
    ]

    articles.sort(
        key=lambda article: (
            article["date"],
            article["id"],
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

        # IMPORTANT READYBOT
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
# MAIN
# ============================================================

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


cache = load_cache()


# ============================================================
# SCAN DOFUS
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
# ENRICHISSEMENT
# ============================================================

articles = enrich_articles(
    articles
)


# ============================================================
# CLASSEMENT
# ============================================================

articles = sort_articles(
    articles
)


# ============================================================
# LIMITE RSS COMPLET
# ============================================================

articles = articles[
    :MAX_ARTICLES
]


# ============================================================
# AFFICHAGE FINAL
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
# DESCRIPTION + CACHE
# ============================================================

for article in articles:

    # Description simple.
    #
    # On garde le système volontairement minimal :
    # le titre est suffisant pour Discord.

    article["description"] = (
        article["title"]
    )

    # --------------------------------------------------------
    # CACHE :
    #
    # Le cache sert à conserver les informations historiques.
    #
    # MAIS :
    # il n'est PAS utilisé pour choisir le dernier article.
    # --------------------------------------------------------

    cache[
        article["url"]
    ] = {
        "title": article["title"],
        "description": article["description"],
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
# DISCORD
# ============================================================

print("")
print(
    "Génération de dofus-news-discord.xml..."
)


if articles:

    # ========================================================
    # DERNIÈRE ACTUALITÉ
    # ========================================================

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
        "   "
        f"{format_pubdate(latest['date'])}"
    )

    # ========================================================
    # ÉTAT DISCORD
    # ========================================================
    #
    # On utilise un fichier séparé dans le cache :
    #
    # _discord_last_sent
    #
    # Il contient UNIQUEMENT l'URL du dernier article
    # envoyé à Discord.
    #
    # Ainsi :
    #
    # Run 1 -> article A -> envoyé
    # Run 2 -> article A -> déjà envoyé
    # Run 3 -> article A -> déjà envoyé
    #
    # Quand DOFUS publie B :
    #
    # Run suivant -> B différent -> envoyé
    #
    # Aucune file d'attente.
    # Aucun article intermédiaire.
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

    # --------------------------------------------------------
    # NOUVEL ARTICLE
    # --------------------------------------------------------

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
        # Flux Discord = UN SEUL ARTICLE
        # ----------------------------------------------------

        create_rss(
            DISCORD_OUTPUT,
            "DOFUS — Actualités",
            "Dernière actualité officielle française de DOFUS.",
            [latest],
        )

        # ----------------------------------------------------
        # Sauvegarde état
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
            "avec 1 nouveau article."
        )

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
        # On génère quand même un flux RSS valide
        # contenant le dernier article.
        #
        # Cela permet à Readybot de parser le fichier
        # même lorsqu'aucun nouvel article n'est détecté.
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

else:

    print("")
    print(
        "⚠️ Aucune actualité disponible."
    )

    # Flux RSS vide mais parfaitement valide.

    create_rss(
        DISCORD_OUTPUT,
        "DOFUS — Actualités",
        "Dernière actualité officielle française de DOFUS.",
        [],
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
