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


BASE_URL = "https://www.dofus.com"
LISTING_URL = f"{BASE_URL}/fr/mmorpg/actualites/maj"

CACHE_FILE = Path("dofus-changelog-cache.json")
DISCORD_STATE_FILE = Path("dofus-changelog-discord-state.json")

RSS_FILE = Path("dofus-changelog.xml")
DISCORD_RSS_FILE = Path("dofus-changelog-discord.xml")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)


# ============================================================
# OUTILS
# ============================================================

def clean_text(value):
    if not value:
        return ""

    value = html.unescape(str(value))
    value = re.sub(r"\s+", " ", value).strip()

    return value


def normalize_url(url):
    if not url:
        return ""

    url = urljoin(BASE_URL, url)
    return url.split("#", 1)[0].rstrip("/")


# ============================================================
# DATES
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


def parse_date(value):
    if not value:
        return None

    value = clean_text(value)

    # RFC / HTTP date
    try:
        dt = parsedate_to_datetime(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except Exception:
        pass

    # ISO
    try:
        dt = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except Exception:
        pass

    # Formats classiques
    for fmt in (
        "%d/%m/%Y",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(
                value,
                fmt
            ).replace(tzinfo=timezone.utc)

        except Exception:
            pass

    return None


def parse_french_date(value):
    if not value:
        return None

    text = clean_text(value).lower()

    # Exemple :
    # 23 Juin 2026
    # 03 Mars 2026
    match = re.search(
        r"\b"
        r"(\d{1,2})"
        r"\s+"
        r"(janvier|février|fevrier|mars|avril|mai|juin|juillet|"
        r"août|aout|septembre|octobre|novembre|décembre|decembre)"
        r"\s+"
        r"(\d{4})"
        r"\b",
        text,
    )

    if not match:
        return None

    try:
        return datetime(
            int(match.group(3)),
            FRENCH_MONTHS[match.group(2)],
            int(match.group(1)),
            tzinfo=timezone.utc,
        )

    except ValueError:
        return None


def date_from_url(url):
    """
    Fallback dynamique pour les patch notes.

    Exemple :

    patch-notes-3-6-10-10-18-08-2026
    -> 18/08/2026

    Aucun article n'est codé en dur.
    """

    slug = normalize_url(url).rsplit("/", 1)[-1]

    matches = re.findall(
        r"(?<!\d)"
        r"(\d{1,2})-(\d{1,2})-(\d{4})"
        r"(?!\d)",
        slug,
    )

    if not matches:
        return None

    day, month, year = matches[-1]

    try:
        return datetime(
            int(year),
            int(month),
            int(day),
            tzinfo=timezone.utc,
        )

    except ValueError:
        return None


# ============================================================
# TITRES
# ============================================================

def clean_title(title):
    title = clean_text(title)

    if not title:
        return ""

    # Supprime les éléments UI parasites.
    title = re.sub(
        r"^\s*Découvrir\s*[:\-|]?\s*",
        "",
        title,
        flags=re.IGNORECASE,
    )

    title = re.sub(
        r"^\s*Voir plus\s*[:\-|]?\s*",
        "",
        title,
        flags=re.IGNORECASE,
    )

    return title.strip()


def is_valid_title(title):
    title = clean_title(title)

    if not title:
        return False

    invalid = {
        "découvrir",
        "decouvrir",
        "voir plus",
        "voir tous",
        "voir tous les correctifs",
    }

    if title.lower() in invalid:
        return False

    return True


def title_from_url(url):
    """
    Fallback dynamique pour les patch notes.

    Exemple :

    patch-notes-3-6-10-10-18-08-2026

    devient :

    MÀJ - Patch notes 3.6.10.10 du 18/08/2026
    """

    slug = normalize_url(url).rsplit("/", 1)[-1]

    match = re.search(
        r"patch-notes-"
        r"(.+?)"
        r"-(\d{1,2})-(\d{1,2})-(\d{4})$",
        slug,
        flags=re.IGNORECASE,
    )

    if match:
        version = match.group(1).replace("-", ".")
        day = match.group(2).zfill(2)
        month = match.group(3).zfill(2)
        year = match.group(4)

        return (
            f"MÀJ - Patch notes "
            f"{version} du {day}/{month}/{year}"
        )

    # Fallback générique
    slug = re.sub(
        r"^\d+-",
        "",
        slug,
    )

    return clean_title(
        slug.replace("-", " ").title()
    )


# ============================================================
# JSON
# ============================================================

def load_json(path, default):
    try:
        if path.exists():

            with path.open(
                "r",
                encoding="utf-8",
            ) as file:

                data = json.load(file)

                if isinstance(data, type(default)):
                    return data

    except Exception:
        pass

    return default


def save_json(path, data):

    with path.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )


# ============================================================
# EXTRACTION ARTICLE
# ============================================================

def extract_jsonld_date(soup):

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

            if not isinstance(obj, dict):
                continue

            for key in (
                "datePublished",
                "dateCreated",
                "dateModified",
            ):

                dt = parse_date(
                    obj.get(key)
                )

                if dt:
                    return dt

    return None


def extract_meta_date(soup):

    for attrs in (
        {"property": "article:published_time"},
        {"name": "article:published_time"},
        {"property": "og:published_time"},
        {"name": "date"},
        {"name": "publishdate"},
        {"name": "datePublished"},
    ):

        tag = soup.find(
            "meta",
            attrs=attrs,
        )

        if tag:

            dt = parse_date(
                tag.get("content")
            )

            if dt:
                return dt

    for tag in soup.find_all("time"):

        value = (
            tag.get("datetime")
            or tag.get_text(
                " ",
                strip=True,
            )
        )

        dt = (
            parse_date(value)
            or parse_french_date(value)
        )

        if dt:
            return dt

    return None


def extract_title_from_soup(soup):

    # OG TITLE
    tag = soup.find(
        "meta",
        property="og:title",
    )

    if tag:

        title = clean_title(
            tag.get("content")
        )

        if is_valid_title(title):
            return title

    # H1
    for selector in (
        "h1",
        '[data-testid="article-title"]',
        ".article-title",
        ".news-title",
    ):

        tag = soup.select_one(
            selector
        )

        if tag:

            title = clean_title(
                tag.get_text(
                    " ",
                    strip=True,
                )
            )

            if is_valid_title(title):
                return title

    # TITLE HTML
    if soup.title:

        title = clean_title(
            soup.title.get_text(
                " ",
                strip=True,
            )
        )

        title = re.sub(
            r"\s*\|\s*DOFUS.*$",
            "",
            title,
            flags=re.IGNORECASE,
        )

        if is_valid_title(title):
            return title

    return ""


# ============================================================
# LISTING
# ============================================================

def extract_listing_items(page):

    items = []
    seen = set()

    links = page.locator(
        'a[href*="/fr/mmorpg/actualites/maj/"]'
    )

    for index in range(
        links.count()
    ):

        try:

            link = links.nth(index)

            href = normalize_url(
                link.get_attribute("href")
            )

            if not href:
                continue

            # Page /maj elle-même
            if href == normalize_url(
                LISTING_URL
            ):
                continue

            # Page de navigation des correctifs
            if href == normalize_url(
                f"{LISTING_URL}/correctifs"
            ):
                continue

            # Doit être un véritable article
            if not re.search(
                r"/maj/\d+-[^/]+",
                href,
                flags=re.IGNORECASE,
            ):
                continue

            if href in seen:
                continue

            # ------------------------------------------------
            # TEXTE DU LIEN
            # ------------------------------------------------

            link_text = clean_title(
                link.inner_text()
            )

            # ------------------------------------------------
            # TROUVER LE CONTENEUR
            # ------------------------------------------------

            card = None

            for level in range(
                1,
                10,
            ):

                try:

                    parent = link.locator(
                        "xpath="
                        + "/.." * level
                    )

                    parent_links = parent.locator(
                        'a[href*="/fr/mmorpg/actualites/maj/"]'
                    )

                    urls = set()

                    for j in range(
                        parent_links.count()
                    ):

                        parent_href = normalize_url(
                            parent_links.nth(j).get_attribute(
                                "href"
                            )
                        )

                        if re.search(
                            r"/maj/\d+-[^/]+",
                            parent_href,
                            flags=re.IGNORECASE,
                        ):

                            if (
                                parent_href
                                != normalize_url(
                                    f"{LISTING_URL}/correctifs"
                                )
                            ):

                                urls.add(
                                    parent_href
                                )

                    # On veut le conteneur correspondant
                    # à UN seul article.
                    if urls == {href}:

                        card = parent
                        break

                except Exception:
                    continue

            if card is None:
                card = link

            # ------------------------------------------------
            # TEXTE DE LA CARTE
            # ------------------------------------------------

            try:

                card_text = clean_text(
                    card.inner_text()
                )

            except Exception:

                card_text = link_text

            # ------------------------------------------------
            # TITRE
            # ------------------------------------------------

            title = ""

            # Le texte du lien peut être le titre.
            if is_valid_title(
                link_text
            ):

                title = link_text

            # Sinon chercher dans les headings.
            if not title:

                try:

                    headings = card.locator(
                        "h1, h2, h3, h4, h5, h6"
                    )

                    for j in range(
                        headings.count()
                    ):

                        candidate = clean_title(
                            headings.nth(j).inner_text()
                        )

                        if is_valid_title(
                            candidate
                        ):

                            title = candidate
                            break

                except Exception:
                    pass

            # ------------------------------------------------
            # DATE
            # ------------------------------------------------

            dt = None

            # 1. <time>
            try:

                times = card.locator(
                    "time"
                )

                for j in range(
                    times.count()
                ):

                    raw = (
                        times.nth(j)
                        .get_attribute(
                            "datetime"
                        )
                        or times.nth(j).inner_text()
                    )

                    dt = (
                        parse_date(raw)
                        or parse_french_date(raw)
                    )

                    if dt:
                        break

            except Exception:
                pass

            # 2. Date française dans toute la carte
            if not dt:

                dt = parse_french_date(
                    card_text
                )

            # 3. Date numérique
            if not dt:

                match = re.search(
                    r"\b"
                    r"(\d{1,2})"
                    r"[/-]"
                    r"(\d{1,2})"
                    r"[/-]"
                    r"(\d{4})"
                    r"\b",
                    card_text,
                )

                if match:

                    dt = parse_date(
                        f"{match.group(1)}/"
                        f"{match.group(2)}/"
                        f"{match.group(3)}"
                    )

            items.append(
                {
                    "url": href,
                    "title": title,
                    "date": (
                        dt.isoformat()
                        if dt
                        else None
                    ),
                }
            )

            seen.add(href)

        except Exception:
            continue

    return items


# ============================================================
# COLLECTE LISTING
# ============================================================

def collect_listing(page):

    page.goto(
        LISTING_URL,
        wait_until="domcontentloaded",
        timeout=60000,
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

    for i in range(8):

        print(
            f"🔄 Recherche du bouton VOIR PLUS "
            f"({i + 1}/8)..."
        )

        buttons = page.get_by_text(
            re.compile(
                r"voir plus",
                re.IGNORECASE,
            )
        )

        visible_button = None

        for j in range(
            buttons.count()
        ):

            try:

                button = buttons.nth(j)

                if button.is_visible():
                    visible_button = button
                    break

            except Exception:
                continue

        if visible_button is None:

            print(
                "ℹ️ Plus de bouton VOIR PLUS."
            )

            break

        before = len(items)

        try:

            visible_button.scroll_into_view_if_needed()

            visible_button.click(
                timeout=5000
            )

            page.wait_for_timeout(
                2000
            )

        except Exception:

            print(
                "ℹ️ Plus de bouton VOIR PLUS."
            )

            break

        items = extract_listing_items(
            page
        )

        if len(items) <= before:

            print(
                "ℹ️ Plus de contenu détecté."
            )

            break

    print(
        f"🟢 Total mises à jour récupérées : "
        f"{len(items)}"
    )

    return items


# ============================================================
# ENRICHISSEMENT
# ============================================================

def enrich_item(
    page,
    item,
    cache,
):

    url = item["url"]

    cached = cache.get(
        url,
        {},
    )

    listing_title = clean_title(
        item.get("title")
    )

    listing_date = parse_date(
        item.get("date")
    )

    url_date = date_from_url(
        url
    )

    title = listing_title
    dt = listing_date

    print(
        "🔎 Ouverture article avec Playwright..."
    )

    try:

        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        page.wait_for_timeout(
            1200
        )

        soup = BeautifulSoup(
            page.content(),
            "html.parser",
        )

        article_title = (
            extract_title_from_soup(
                soup
            )
        )

        article_date = (
            extract_jsonld_date(
                soup
            )
            or extract_meta_date(
                soup
            )
        )

        # ====================================================
        # TITRE
        # ====================================================

        # CORRECTIFS :
        # le site renvoie souvent le titre de la MÀJ
        # parent dans le H1.
        #
        # On utilise donc le slug du patch pour obtenir
        # son vrai titre dynamiquement.
        if "/correctifs/" in url:

            title = title_from_url(
                url
            )

        elif is_valid_title(
            listing_title
        ):

            title = listing_title

        elif is_valid_title(
            article_title
        ):

            title = article_title

        else:

            title = title_from_url(
                url
            )

        # ====================================================
        # DATE
        # ====================================================

        # Priorité :
        #
        # 1. Listing
        # 2. Article
        # 3. URL
        # 4. Cache

        if listing_date:

            dt = listing_date
            date_source = "LISTING"

        elif article_date:

            dt = article_date
            date_source = "ARTICLE"

        elif url_date:

            dt = url_date
            date_source = "URL"

        else:

            dt = parse_date(
                cached.get("date")
            )

            date_source = (
                "CACHE"
                if dt
                else None
            )

    except Exception as exc:

        print(
            f"⚠️ Erreur ouverture article : "
            f"{exc}"
        )

        date_source = None

    # Dernier fallback cache
    if not dt:

        dt = parse_date(
            cached.get("date")
        )

        if dt:
            date_source = "CACHE"

    # Dernier fallback titre
    if not is_valid_title(
        title
    ):

        title = clean_title(
            cached.get("title")
        )

    if not is_valid_title(
        title
    ):

        title = title_from_url(
            url
        )

    # Impossible de publier sans date.
    if not dt:

        print(
            "⚠️ Date introuvable."
        )

        return None

    if not title:

        print(
            "⚠️ Titre introuvable."
        )

        return None

    title = clean_title(
        title
    )

    print(
        f"   🏷️ Titre : {title}"
    )

    print(
        f"   📅 Date trouvée via "
        f"{date_source or 'FALLBACK'}: "
        f"{format_datetime(dt)}"
    )

    print(
        f"🟢 {format_datetime(dt)} "
        f"- {title}"
    )

    result = {
        "url": url,
        "title": title,
        "date": dt.isoformat(),
    }

    cache[url] = result

    return result


# ============================================================
# RSS COMPLET
# ============================================================

def build_rss(
    items,
    path,
    title="DOFUS Changelogs",
):

    now = format_datetime(
        datetime.now(timezone.utc),
        usegmt=True,
    )

    chunks = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "<channel>",
        f"<title>{html.escape(title)}</title>",
        f"<link>{BASE_URL}/fr/mmorpg/actualites/maj</link>",
        "<description>Changelogs DOFUS</description>",
        f"<lastBuildDate>{now}</lastBuildDate>",
    ]

    for item in items:

        dt = parse_date(
            item["date"]
        )

        if not dt:
            continue

        pub = format_datetime(
            dt,
            usegmt=True,
        )

        chunks.extend(
            [
                "<item>",
                f"<title>{html.escape(item['title'])}</title>",
                f"<link>{html.escape(item['url'])}</link>",
                (
                    f"<guid isPermaLink=\"true\">"
                    f"{html.escape(item['url'])}"
                    f"</guid>"
                ),
                (
                    f"<description>"
                    f"{html.escape(item['title'])}"
                    f"</description>"
                ),
                f"<pubDate>{pub}</pubDate>",
                "</item>",
            ]
        )

    chunks.extend(
        [
            "</channel>",
            "</rss>",
        ]
    )

    path.write_text(
        "\n".join(chunks) + "\n",
        encoding="utf-8",
    )


# ============================================================
# RSS DISCORD / READYBOT
# ============================================================

def build_discord_rss(item):

    """
    Flux volontairement minimal :

    RSS 2.0
    1 seul item
    title
    link
    guid
    description
    pubDate

    C'est le format utilisé pour éviter les problèmes
    de parsing Readybot / Discord.
    """

    if not item:

        build_rss(
            [],
            DISCORD_RSS_FILE,
            "DOFUS Changelog Discord",
        )

        return

    dt = parse_date(
        item["date"]
    )

    if not dt:
        return

    pub = format_datetime(
        dt,
        usegmt=True,
    )

    xml = "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<rss version="2.0">',
            "<channel>",
            "<title>DOFUS Changelog Discord</title>",
            f"<link>{html.escape(item['url'])}</link>",
            "<description>Dernier changelog DOFUS</description>",
            "<item>",
            f"<title>{html.escape(item['title'])}</title>",
            f"<link>{html.escape(item['url'])}</link>",
            (
                f"<guid isPermaLink=\"true\">"
                f"{html.escape(item['url'])}"
                f"</guid>"
            ),
            (
                f"<description>"
                f"{html.escape(item['title'])}"
                f"</description>"
            ),
            f"<pubDate>{pub}</pubDate>",
            "</item>",
            "</channel>",
            "</rss>",
        ]
    )

    DISCORD_RSS_FILE.write_text(
        xml + "\n",
        encoding="utf-8",
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        """
########################################
# Tensho Dofus
# CHANGELOGS / MISES À JOUR
########################################
"""
    )

    cache = load_json(
        CACHE_FILE,
        {},
    )

    print(
        f"Cache Changelog Dofus chargé : "
        f"{len(cache)} articles."
    )

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page(
            user_agent=USER_AGENT,
            locale="fr-FR",
        )

        print(
            "\n========================================"
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

        listing = collect_listing(
            page
        )

        print(
            """
########################################
# URLs Changelogs Dofus trouvées
########################################
"""
        )

        results = []

        for index, item in enumerate(
            listing,
            1,
        ):

            print(
                f"[{index}/{len(listing)}] "
                f"{item['url']}"
            )

            if item.get("title"):

                print(
                    f"   🏷️ Titre trouvé dans le listing: "
                    f"{item['title']}"
                )

            if item.get("date"):

                listing_dt = parse_date(
                    item["date"]
                )

                if listing_dt:

                    print(
                        f"   📅 Date trouvée dans le listing: "
                        f"{format_datetime(listing_dt)}"
                    )

            enriched = enrich_item(
                page,
                item,
                cache,
            )

            if enriched:
                results.append(
                    enriched
                )

        browser.close()

    # ========================================================
    # DÉDOUBLONNAGE
    # ========================================================

    unique = {}

    for item in results:

        unique[
            item["url"]
        ] = item

    results = list(
        unique.values()
    )

    # ========================================================
    # TRI PAR DATE
    # ========================================================

    results.sort(
        key=lambda item: (
            parse_date(
                item["date"]
            )
            or datetime.min.replace(
                tzinfo=timezone.utc
            )
        ),
        reverse=True,
    )

    # Maximum 20 articles dans le RSS complet
    results = results[:20]

    # ========================================================
    # AFFICHAGE
    # ========================================================

    print(
        "\n########################################"
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
        1,
    ):

        print(
            f"{index:02d}. "
            f"{format_datetime(parse_date(item['date']))} "
            f"- {item['title']}"
        )

        print(
            f"    {item['url']}"
        )

    # ========================================================
    # CACHE
    # ========================================================

    save_json(
        CACHE_FILE,
        {
            item["url"]: item
            for item in results
        },
    )

    # ========================================================
    # RSS COMPLET
    # ========================================================

    print(
        "\nGénération de dofus-changelog.xml..."
    )

    build_rss(
        results,
        RSS_FILE,
    )

    print(
        "🟢 dofus-changelog.xml généré."
    )

    # ========================================================
    # RSS DISCORD
    # ========================================================

    print(
        "\nGénération de "
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

        return

    # IMPORTANT :
    # le premier élément est TOUJOURS
    # le plus récent grâce au tri dynamique.
    latest = results[0]

    latest_date = parse_date(
        latest["date"]
    )

    print(
        "\n🔎 Dernier changelog actuellement "
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

    # ========================================================
    # ÉTAT DISCORD
    # ========================================================

    state = load_json(
        DISCORD_STATE_FILE,
        {},
    )

    last_url = normalize_url(
        state.get("url")
    )

    last_date = parse_date(
        state.get("date")
    )

    # ========================================================
    # NOUVEAUTÉ
    # ========================================================

    is_new = (
        not last_url
        or latest["url"] != last_url
        or (
            latest_date
            and last_date
            and latest_date > last_date
        )
    )

    if is_new:

        print(
            "🆕 Nouveau changelog "
            "à envoyer sur Discord."
        )

        build_discord_rss(
            latest
        )

        save_json(
            DISCORD_STATE_FILE,
            {
                "url": latest["url"],
                "title": latest["title"],
                "date": latest["date"],
            },
        )

        print(
            "🟢 État Discord sauvegardé."
        )

        print(
            "🟢 dofus-changelog-discord.xml "
            "généré avec 1 nouveau changelog."
        )

    else:

        print(
            "ℹ️ Le dernier changelog DOFUS "
            "a déjà été envoyé."
        )

        print(
            "ℹ️ Aucun nouvel envoi Discord."
        )

        build_discord_rss(
            None
        )

        print(
            "🟢 dofus-changelog-discord.xml "
            "généré sans nouvel envoi."
        )

    print(
        """
########################################
# DOFUS CHANGELOG RSS TERMINÉ
########################################
"""
    )


# ============================================================
# LANCEMENT
# ============================================================

if __name__ == "__main__":
    main()
