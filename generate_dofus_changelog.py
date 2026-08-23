#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import html
from pathlib import Path
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


########################################
# CONFIGURATION
########################################

BASE_URL = "https://www.dofus.com"

LISTING_URL = (
    f"{BASE_URL}/fr/mmorpg/actualites/maj"
)

CACHE_FILE = Path(
    "dofus-changelog-cache.json"
)

DISCORD_STATE_FILE = Path(
    "dofus-changelog-discord-state.json"
)

RSS_FILE = Path(
    "dofus-changelog.xml"
)

DISCORD_RSS_FILE = Path(
    "dofus-changelog-discord.xml"
)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)


########################################
# OUTILS GENERAUX
########################################

def clean_text(value):

    if not value:
        return ""

    value = html.unescape(
        str(value)
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


def normalize_url(url):

    if not url:
        return ""

    url = urljoin(
        BASE_URL,
        url
    )

    url = url.split(
        "#",
        1
    )[0]

    return url.rstrip("/")


########################################
# DATES
########################################

def parse_date(value):

    if not value:
        return None

    value = clean_text(
        value
    )

    ########################################
    # RFC DATE
    ########################################

    try:

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

    ########################################
    # ISO DATE
    ########################################

    try:

        dt = datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00"
            )
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

    ########################################
    # FORMATS CLASSIQUES
    ########################################

    formats = [

        "%d/%m/%Y",

        "%Y-%m-%d",

        "%d-%m-%Y",

        "%d/%m/%Y %H:%M",

        "%Y-%m-%d %H:%M:%S",

    ]

    for fmt in formats:

        try:

            return datetime.strptime(
                value,
                fmt
            ).replace(
                tzinfo=timezone.utc
            )

        except Exception:

            pass

    return None


def date_from_url(url):

    """
    Les URLs de patch notes DOFUS contiennent
    généralement la date dans leur slug.

    Exemple :

    patch-notes-3-6-10-10-18-08-2026

    devient :

    18/08/2026

    Cette logique est entièrement générique.
    Aucun article précis n'est codé en dur.
    """

    slug = (
        normalize_url(url)
        .rsplit(
            "/",
            1
        )[-1]
    )

    matches = re.findall(
        r"(?<!\d)"
        r"(\d{1,2})-"
        r"(\d{1,2})-"
        r"(\d{4})"
        r"(?!\d)",
        slug
    )

    if not matches:

        return None

    day, month, year = matches[-1]

    try:

        return datetime(
            int(year),
            int(month),
            int(day),
            tzinfo=timezone.utc
        )

    except ValueError:

        return None


########################################
# TITRES
########################################

def clean_title(title):

    title = clean_text(
        title
    )

    if not title:

        return ""

    ########################################
    # CORRECTION DU PREFIXE "DECOUVRIR"
    ########################################

    title = re.sub(
        r"^\s*Découvrir\s+"
        r"(?=\d{1,2}\s+"
        r"(?:Janvier|Février|Mars|Avril|Mai|Juin|"
        r"Juillet|Août|Septembre|Octobre|Novembre|Décembre)"
        r"\b)",
        "",
        title,
        flags=re.IGNORECASE
    )

    ########################################
    # SI "DECOUVRIR" EST SEUL
    ########################################

    title = re.sub(
        r"^\s*Découvrir\s*"
        r"[:\-|]?\s*$",
        "",
        title,
        flags=re.IGNORECASE
    )

    return title.strip()


########################################
# FICHIERS JSON
########################################

def load_json(
    path,
    default
):

    try:

        if path.exists():

            with path.open(
                "r",
                encoding="utf-8"
            ) as f:

                return json.load(
                    f
                )

    except Exception as exc:

        print(
            f"⚠️ Erreur lecture "
            f"{path}: {exc}"
        )

    return default


def save_json(
    path,
    data
):

    with path.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


########################################
# EXTRACTION DATE JSON-LD
########################################

def extract_jsonld_date(
    soup
):

    scripts = soup.find_all(
        "script",
        type="application/ld+json"
    )

    for script in scripts:

        raw = (
            script.string
            or
            script.get_text()
        )

        if not raw:

            continue

        try:

            data = json.loads(
                raw
            )

        except Exception:

            continue

        if isinstance(
            data,
            list
        ):

            objects = data

        else:

            objects = [data]

        for obj in objects:

            if not isinstance(
                obj,
                dict
            ):

                continue

            for key in (
                "datePublished",
                "dateCreated",
                "dateModified"
            ):

                dt = parse_date(
                    obj.get(
                        key
                    )
                )

                if dt:

                    return dt

    return None


########################################
# EXTRACTION DATE META
########################################

def extract_meta_date(
    soup
):

    selectors = [

        {
            "property":
                "article:published_time"
        },

        {
            "name":
                "article:published_time"
        },

        {
            "property":
                "og:published_time"
        },

        {
            "name":
                "date"
        },

        {
            "name":
                "publishdate"
        },

        {
            "name":
                "datePublished"
        },

    ]

    for attrs in selectors:

        tag = soup.find(
            "meta",
            attrs=attrs
        )

        if not tag:

            continue

        dt = parse_date(
            tag.get(
                "content"
            )
        )

        if dt:

            return dt

    ########################################
    # BALISE TIME
    ########################################

    for tag in soup.find_all(
        "time"
    ):

        value = (
            tag.get(
                "datetime"
            )
            or
            tag.get_text(
                " ",
                strip=True
            )
        )

        dt = parse_date(
            value
        )

        if dt:

            return dt

    return None


########################################
# EXTRACTION TITRE
########################################

def extract_title_from_soup(
    soup
):

    ########################################
    # H1
    ########################################

    selectors = [

        "h1",

        '[data-testid="article-title"]',

        ".article-title",

        ".news-title",

    ]

    for selector in selectors:

        tag = soup.select_one(
            selector
        )

        if not tag:

            continue

        title = clean_title(
            tag.get_text(
                " ",
                strip=True
            )
        )

        if title:

            return title

    ########################################
    # OG TITLE
    ########################################

    tag = soup.find(
        "meta",
        property="og:title"
    )

    if tag:

        title = clean_title(
            tag.get(
                "content"
            )
        )

        if title:

            return title

    ########################################
    # TITLE HTML
    ########################################

    if soup.title:

        title = clean_title(
            soup.title.get_text(
                " ",
                strip=True
            )
        )

        title = re.sub(
            r"\s*\|\s*DOFUS.*$",
            "",
            title,
            flags=re.IGNORECASE
        )

        if title:

            return title

    return ""


########################################
# EXTRACTION DES ARTICLES DU LISTING
########################################

def extract_listing_items(
    page
):

    items = []

    seen = set()

    links = page.locator(
        'a[href*="/fr/mmorpg/actualites/maj/"]'
    ).all()

    for link in links:

        try:

            href = link.get_attribute(
                "href"
            )

            url = normalize_url(
                href
            )

            if not url:

                continue

            if url in seen:

                continue

            ########################################
            # NE PAS PRENDRE LA PAGE LISTING
            ########################################

            if (
                url
                == normalize_url(
                    LISTING_URL
                )
            ):

                continue

            ########################################
            # TITRE DU LIEN
            ########################################

            title = clean_title(
                link.inner_text()
            )

            ########################################
            # REMONTER A LA CARTE
            ########################################

            card = None

            try:

                card = link.locator(
                    "xpath="
                    "ancestor::*["
                    "self::article "
                    "or contains(@class,'card') "
                    "or contains(@class,'article')"
                    "][1]"
                )

            except Exception:

                card = None

            date_value = None

            card_text = ""

            ########################################
            # DATE DANS LA CARTE
            ########################################

            if card and card.count():

                try:

                    card_text = clean_text(
                        card.inner_text()
                    )

                except Exception:

                    card_text = ""

                try:

                    times = card.locator(
                        "time"
                    ).all()

                    for time_node in times:

                        value = (
                            time_node.get_attribute(
                                "datetime"
                            )
                            or
                            time_node.inner_text()
                        )

                        date_value = parse_date(
                            value
                        )

                        if date_value:

                            break

                except Exception:

                    pass

                ########################################
                # DATE DANS LE TEXTE
                ########################################

                if not date_value:

                    match = re.search(
                        r"\b"
                        r"(\d{1,2})[/-]"
                        r"(\d{1,2})[/-]"
                        r"(\d{4})"
                        r"\b",
                        card_text
                    )

                    if match:

                        date_value = parse_date(
                            (
                                f"{match.group(1)}/"
                                f"{match.group(2)}/"
                                f"{match.group(3)}"
                            )
                        )

            items.append(
                {

                    "url":
                        url,

                    "title":
                        title,

                    "date":
                        (
                            date_value.isoformat()
                            if date_value
                            else None
                        ),

                }
            )

            seen.add(
                url
            )

        except Exception:

            continue

    return items


########################################
# COLLECTE DU LISTING
########################################

def collect_listing(
    page
):

    print(
        ""
    )

    print(
        "========================================"
    )

    print(
        "Ouverture avec Playwright :"
    )

    print(
        LISTING_URL
    )

    print(
        "========================================"
    )

    page.goto(
        LISTING_URL,
        wait_until="domcontentloaded",
        timeout=60000
    )

    page.wait_for_timeout(
        3000
    )

    items = extract_listing_items(
        page
    )

    print(
        f"Premier lot : "
        f"{len(items)} mises à jour détectées."
    )

    ########################################
    # VOIR PLUS
    ########################################

    for click_number in range(
        1,
        9
    ):

        print(
            f"🔄 Recherche du bouton VOIR PLUS "
            f"({click_number}/8)..."
        )

        buttons = page.get_by_text(
            re.compile(
                r"voir plus",
                re.IGNORECASE
            )
        )

        if not buttons.count():

            print(
                "ℹ️ Plus de bouton VOIR PLUS."
            )

            break

        try:

            button = buttons.last

            button.click(
                timeout=3000
            )

            page.wait_for_timeout(
                1800
            )

        except Exception:

            print(
                "ℹ️ Plus de bouton VOIR PLUS."
            )

            break

        new_items = (
            extract_listing_items(
                page
            )
        )

        if len(new_items) <= len(items):

            print(
                "ℹ️ Plus de contenu détecté."
            )

            break

        items = new_items

    print(
        f"🟢 Total mises à jour récupérées : "
        f"{len(items)}"
    )

    return items


########################################
# ENRICHISSEMENT ARTICLE
########################################

def enrich_item(
    page,
    item,
    cache
):

    url = item[
        "url"
    ]

    cached = cache.get(
        url,
        {}
    )

    listing_title = clean_title(
        item.get(
            "title"
        )
    )

    listing_date = parse_date(
        item.get(
            "date"
        )
    )

    ########################################
    # DATE URL
    ########################################

    url_date = date_from_url(
        url
    )

    title = listing_title

    date_value = (
        url_date
        or
        listing_date
    )

    date_source = (
        "URL"
        if url_date
        else
        (
            "LISTING"
            if listing_date
            else None
        )
    )

    print(
        "   🔎 Ouverture article "
        "avec Playwright..."
    )

    try:

        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.wait_for_timeout(
            1200
        )

        soup = BeautifulSoup(
            page.content(),
            "html.parser"
        )

        ########################################
        # TITRE ARTICLE
        ########################################

        article_title = (
            extract_title_from_soup(
                soup
            )
        )

        if article_title:

            title = article_title

        ########################################
        # DATE ARTICLE
        ########################################

        article_date = (
            extract_jsonld_date(
                soup
            )
            or
            extract_meta_date(
                soup
            )
        )

        ########################################
        # PRIORITE DATE
        #
        # URL > ARTICLE > LISTING > CACHE
        ########################################

        if url_date:

            date_value = url_date

            date_source = "URL"

        elif article_date:

            date_value = article_date

            date_source = "ARTICLE"

        elif listing_date:

            date_value = listing_date

            date_source = "LISTING"

    except Exception as exc:

        print(
            f"   ⚠️ Erreur ouverture article : "
            f"{exc}"
        )

    ########################################
    # CACHE FALLBACK
    ########################################

    if not date_value:

        date_value = parse_date(
            cached.get(
                "date"
            )
        )

        if date_value:

            date_source = "CACHE"

    if not title:

        title = clean_title(
            cached.get(
                "title",
                ""
            )
        )

    ########################################
    # VALIDATION DATE
    ########################################

    if not date_value:

        print(
            "⚠️ Date introuvable."
        )

        return None

    ########################################
    # VALIDATION TITRE
    ########################################

    if not title:

        print(
            "⚠️ Titre introuvable."
        )

        return None

    title = clean_title(
        title
    )

    ########################################
    # AFFICHAGE
    ########################################

    print(
        f"   🏷️ Titre : "
        f"{title}"
    )

    print(
        f"   📅 Date trouvée via "
        f"{date_source}: "
        f"{format_datetime(date_value)}"
    )

    print(
        f"🟢 "
        f"{format_datetime(date_value)} "
        f"- "
        f"{title}"
    )

    result = {

        "url":
            url,

        "title":
            title,

        "date":
            date_value.isoformat(),

    }

    return result


########################################
# GENERATION RSS COMPLET
########################################

def build_rss(
    items,
    path,
    title="DOFUS Changelogs"
):

    now = format_datetime(
        datetime.now(
            timezone.utc
        ),
        usegmt=True
    )

    xml = [

        '<?xml version="1.0" encoding="UTF-8"?>',

        '<rss version="2.0">',

        "<channel>",

        (
            f"<title>"
            f"{html.escape(title)}"
            f"</title>"
        ),

        (
            f"<link>"
            f"{LISTING_URL}"
            f"</link>"
        ),

        (
            "<description>"
            "Changelogs DOFUS"
            "</description>"
        ),

        (
            f"<lastBuildDate>"
            f"{now}"
            f"</lastBuildDate>"
        ),

    ]

    for item in items:

        dt = parse_date(
            item[
                "date"
            ]
        )

        if not dt:

            continue

        pub_date = format_datetime(
            dt,
            usegmt=True
        )

        escaped_title = html.escape(
            item[
                "title"
            ]
        )

        escaped_url = html.escape(
            item[
                "url"
            ]
        )

        xml.extend(

            [

                "<item>",

                (
                    f"<title>"
                    f"{escaped_title}"
                    f"</title>"
                ),

                (
                    f"<link>"
                    f"{escaped_url}"
                    f"</link>"
                ),

                (
                    f"<guid "
                    f'isPermaLink="true">'
                    f"{escaped_url}"
                    f"</guid>"
                ),

                (
                    f"<description>"
                    f"{escaped_title}"
                    f"</description>"
                ),

                (
                    f"<pubDate>"
                    f"{pub_date}"
                    f"</pubDate>"
                ),

                "</item>",

            ]

        )

    xml.extend(

        [

            "</channel>",

            "</rss>",

        ]

    )

    path.write_text(
        "\n".join(
            xml
        )
        + "\n",
        encoding="utf-8"
    )


########################################
# GENERATION RSS DISCORD
########################################

def build_discord_rss(
    item
):

    """
    Flux Discord volontairement minimal :

    RSS 2.0
    1 seul item
    title
    link
    guid
    description
    pubDate valide

    C'est celui destiné à Readybot / Discord.
    """

    if not item:

        xml = "\n".join(

            [

                '<?xml version="1.0" encoding="UTF-8"?>',

                '<rss version="2.0">',

                "<channel>",

                "<title>"
                "DOFUS Changelog Discord"
                "</title>",

                (
                    f"<link>"
                    f"{LISTING_URL}"
                    f"</link>"
                ),

                "<description>"
                "Dernier changelog DOFUS"
                "</description>",

                "</channel>",

                "</rss>",

            ]

        )

        DISCORD_RSS_FILE.write_text(
            xml + "\n",
            encoding="utf-8"
        )

        return

    dt = parse_date(
        item[
            "date"
        ]
    )

    pub_date = format_datetime(
        dt,
        usegmt=True
    )

    title = html.escape(
        item[
            "title"
        ]
    )

    url = html.escape(
        item[
            "url"
        ]
    )

    xml = "\n".join(

        [

            '<?xml version="1.0" encoding="UTF-8"?>',

            '<rss version="2.0">',

            "<channel>",

            "<title>"
            "DOFUS Changelog Discord"
            "</title>",

            (
                f"<link>"
                f"{url}"
                f"</link>"
            ),

            "<description>"
            "Dernier changelog DOFUS"
            "</description>",

            "<item>",

            (
                f"<title>"
                f"{title}"
                f"</title>"
            ),

            (
                f"<link>"
                f"{url}"
                f"</link>"
            ),

            (
                f"<guid "
                f'isPermaLink="true">'
                f"{url}"
                f"</guid>"
            ),

            (
                f"<description>"
                f"{title}"
                f"</description>"
            ),

            (
                f"<pubDate>"
                f"{pub_date}"
                f"</pubDate>"
            ),

            "</item>",

            "</channel>",

            "</rss>",

        ]

    )

    DISCORD_RSS_FILE.write_text(
        xml + "\n",
        encoding="utf-8"
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

    cache = load_json(
        CACHE_FILE,
        {}
    )

    print(
        f"Cache Changelog Dofus chargé : "
        f"{len(cache)} articles."
    )

    ########################################
    # PLAYWRIGHT
    ########################################

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page(
            user_agent=USER_AGENT,
            locale="fr-FR"
        )

        ########################################
        # LISTING
        ########################################

        listing = collect_listing(
            page
        )

        print("")

        print(
            "########################################"
        )

        print(
            "# URLs Changelogs Dofus trouvées : "
            f"{len(listing)}"
        )

        print(
            "########################################"
        )

        ########################################
        # ARTICLES
        ########################################

        results = []

        for index, item in enumerate(
            listing,
            start=1
        ):

            print("")

            print(
                f"[{index}/{len(listing)}] "
                f"{item['url']}"
            )

            if item.get(
                "title"
            ):

                print(
                    f"   🏷️ Titre trouvé dans "
                    f"le listing: "
                    f"{item['title']}"
                )

            if item.get(
                "date"
            ):

                dt = parse_date(
                    item[
                        "date"
                    ]
                )

                if dt:

                    print(
                        f"   📅 Date trouvée dans "
                        f"le listing: "
                        f"{format_datetime(dt)}"
                    )

            ########################################
            # ENRICHISSEMENT
            ########################################

            enriched = enrich_item(
                page,
                item,
                cache
            )

            if enriched:

                results.append(
                    enriched
                )

        browser.close()

    ########################################
    # DEDOUBLONNAGE
    ########################################

    unique = {}

    for item in results:

        unique[
            item[
                "url"
            ]
        ] = item

    results = list(
        unique.values()
    )

    ########################################
    # TRI DATE
    ########################################

    results.sort(

        key=lambda item:
            parse_date(
                item[
                    "date"
                ]
            )
            or
            datetime.min.replace(
                tzinfo=timezone.utc
            ),

        reverse=True

    )

    ########################################
    # MAX 20
    ########################################

    results = results[
        :20
    ]

    ########################################
    # AFFICHAGE
    ########################################

    print("")

    print(
        "########################################"
    )

    print(
        f"# {len(results)} "
        f"Changelogs Dofus retenus"
    )

    print(
        "########################################"
    )

    for index, item in enumerate(
        results,
        start=1
    ):

        dt = parse_date(
            item[
                "date"
            ]
        )

        print(
            f"{index:02d}. "
            f"{format_datetime(dt)} "
            f"- "
            f"{item['title']}"
        )

    ########################################
    # CACHE
    ########################################

    new_cache = {}

    for item in results:

        new_cache[
            item[
                "url"
            ]
        ] = item

    ########################################
    # CONSERVER LES ANCIENS ELEMENTS
    ########################################

    for url, item in cache.items():

        if url not in new_cache:

            new_cache[
                url
            ] = item

    save_json(
        CACHE_FILE,
        new_cache
    )

    ########################################
    # RSS COMPLET
    ########################################

    print("")

    print(
        "Génération de "
        "dofus-changelog.xml..."
    )

    build_rss(
        results,
        RSS_FILE
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

    if not results:

        print(
            "⚠️ Aucun changelog disponible."
        )

        build_discord_rss(
            None
        )

        print(
            "🟢 dofus-changelog-discord.xml "
            "généré sans nouvel envoi."
        )

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

        return

    ########################################
    # DERNIER CHANGELOG
    ########################################

    latest = results[
        0
    ]

    latest_date = parse_date(
        latest[
            "date"
        ]
    )

    print("")

    print(
        "🔎 Dernier changelog actuellement "
        "publié sur DOFUS :"
    )

    print(
        f"   {latest['title']}"
    )

    print(
        f"   {latest['url']}"
    )

    print(
        f"   {format_datetime(latest_date)}"
    )

    ########################################
    # ETAT DISCORD
    ########################################

    state = load_json(
        DISCORD_STATE_FILE,
        {}
    )

    last_url = normalize_url(
        state.get(
            "url"
        )
    )

    last_date = parse_date(
        state.get(
            "date"
        )
    )

    ########################################
    # NOUVEAU ?
    ########################################

    is_new = (

        not last_url

        or

        latest[
            "url"
        ]
        != last_url

        or

        (
            latest_date
            and
            last_date
            and
            latest_date > last_date
        )

    )

    if is_new:

        print("")

        print(
            "🆕 Nouveau changelog à envoyer "
            "sur Discord."
        )

        ########################################
        # GENERATION 1 ITEM
        ########################################

        build_discord_rss(
            latest
        )

        ########################################
        # SAUVEGARDE ETAT
        ########################################

        save_json(

            DISCORD_STATE_FILE,

            {

                "url":
                    latest[
                        "url"
                    ],

                "title":
                    latest[
                        "title"
                    ],

                "date":
                    latest[
                        "date"
                    ],

            }

        )

        print(
            "🟢 État Discord sauvegardé."
        )

        print(
            "🟢 dofus-changelog-discord.xml "
            "généré avec 1 nouveau changelog."
        )

    else:

        print("")

        print(
            "ℹ️ Le dernier changelog DOFUS "
            "a déjà été envoyé."
        )

        ########################################
        # FLUX VIDE = PAS DE NOUVEL ENVOI
        ########################################

        build_discord_rss(
            None
        )

        print(
            "ℹ️ Aucun nouvel envoi Discord."
        )

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
