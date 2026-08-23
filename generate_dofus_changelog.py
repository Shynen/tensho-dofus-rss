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
        "AppleWebKit/537.36 Chrome/149.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}


########################################
# HEADER
########################################

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

        if not dt.tzinfo:

            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            timezone.utc
        )

    except Exception:

        return None


def format_pubdate(dt):

    return formatdate(
        dt.timestamp(),
        usegmt=True
    )


def normalize_changelog_title(title):

    title = clean_text(title)

    # Sur certains anciens articles DOFUS, le listing ajoute
    # le préfixe éditorial « Découvrir » au vrai titre.
    # On retire uniquement ce préfixe lorsqu'il est suivi
    # d'une date complète au format « 23 Juin 2026 : ».
    title = re.sub(
        r"^Découvrir\s+\d{1,2}\s+[A-Za-zÀ-ÿ]+\s+\d{4}\s*:\s*",
        "",
        title,
        flags=re.IGNORECASE
    )

    return clean_text(title)


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

    except Exception as e:

        print(
            f"⚠️ Erreur lecture cache : {e}"
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

        if not isinstance(
            data,
            dict
        ):

            return {}

        return data

    except Exception as e:

        print(
            f"⚠️ Erreur lecture état Discord : {e}"
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
# VALIDATION URL
########################################

def is_valid_url(url):

    value = url.lower()

    if "dofus.com" not in value:

        return False

    if "/fr/mmorpg/actualites/maj/" not in value:

        return False

    if value.rstrip("/") == SOURCE_URL.rstrip("/"):

        return False

    return True


########################################
# RÉCUPÉRATION DES URLS
########################################

def collect_changelog_urls():

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

    urls = set()

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

        try:

            page.goto(
                SOURCE_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )

            page.wait_for_timeout(
                4000
            )

        except Exception as e:

            print(
                f"❌ Erreur ouverture page : {e}"
            )

            browser.close()

            return []

        def collect():

            before = len(urls)

            links = page.locator(
                'a[href*="/fr/mmorpg/actualites/maj/"]'
            )

            for i in range(
                links.count()
            ):

                try:

                    href = links.nth(
                        i
                    ).get_attribute(
                        "href"
                    )

                    if not href:

                        continue

                    full_url = urljoin(
                        BASE_URL,
                        href
                    ).rstrip("/")

                    if is_valid_url(
                        full_url
                    ):

                        urls.add(
                            full_url
                        )

                except Exception:

                    pass

            return len(urls) - before

        collect()

        print(
            f"Premier lot : "
            f"{len(urls)} mises à jour détectées."
        )

        for click_number in range(
            1,
            MAX_LOAD_MORE_CLICKS + 1
        ):

            if len(urls) >= MAX_ARTICLES:

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

                    button = buttons.nth(
                        i
                    )

                    if not button.is_visible():

                        continue

                    button.scroll_into_view_if_needed()

                    page.wait_for_timeout(
                        500
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

            added = collect()

            print(
                f"Mises à jour actuellement "
                f"trouvées : "
                f"{len(urls)} (+{added})"
            )

            if added == 0:

                break

        browser.close()

    print(
        f"🟢 Total mises à jour récupérées : "
        f"{len(urls)}"
    )

    return list(urls)


########################################
# EXTRACTION ARTICLE
########################################

def extract_article(
    url,
    cache
):

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    try:

        response = session.get(
            url,
            timeout=30
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

    except Exception as e:

        print(
            f"⚠️ Impossible de charger : {e}"
        )

        return None

    ########################################
    # TITRE
    ########################################

    title = ""

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

    # Normalisation finale du titre avant cache/RSS/Discord.
    title = normalize_changelog_title(title)

    ########################################
    # DATE JSON-LD
    ########################################

    dt = None

    for script in soup.find_all(
        "script",
        type="application/ld+json"
    ):

        raw = (
            script.string
            or script.get_text()
        )

        if not raw:

            continue

        for value in re.findall(
            r'"datePublished"\s*:\s*"([^"]+)"',
            raw
        ):

            dt = parse_date(
                value
            )

            if dt:

                break

        if dt:

            break

    ########################################
    # DATE META
    ########################################

    if not dt:

        for attrs in [

            {
                "property":
                "article:published_time"
            },

            {
                "property":
                "og:published_time"
            },

        ]:

            meta = soup.find(
                "meta",
                attrs=attrs
            )

            if meta:

                dt = parse_date(
                    meta.get(
                        "content"
                    )
                )

                if dt:

                    break

    ########################################
    # DATE TIME
    ########################################

    if not dt:

        for node in soup.find_all(
            "time"
        ):

            dt = parse_date(
                node.get(
                    "datetime"
                )
            )

            if dt:

                break

    ########################################
    # CACHE
    ########################################

    if not dt and url in cache:

        dt = parse_date(
            cache[url].get(
                "pubDate"
            )
        )

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

all_urls = set(
    collect_changelog_urls()
)

print("")

print(
    "########################################"
)

print(
    f"# URLs Changelogs Dofus trouvées : "
    f"{len(all_urls)}"
)

print(
    "########################################"
)

articles = []

########################################
# EXTRACTION
########################################

for index, url in enumerate(
    all_urls,
    start=1
):

    print("")

    print(
        f"[{index}/{len(all_urls)}] {url}"
    )

    article = extract_article(
        url,
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

    latest_url = latest_article[
        "url"
    ]

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
        f"   {format_pubdate(latest_article['date'])}"
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


else:

    print(
        "⚠️ Aucun changelog disponible."
    )


########################################
# SI PAS DE NOUVEAU CHANGELOG
#
# On conserve le dernier article envoyé
# dans le RSS Discord.
#
# IMPORTANT :
# on ne cherche PAS un ancien changelog.
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
                or "Changelog DOFUS",

                "url":
                previous_url,

                "description":
                state.get(
                    "last_sent_description"
                )
                or
                "Note de mise à jour "
                "officielle DOFUS.",

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
