#!/usr/bin/env python3
# -*- coding: utf-8 -*-

########################################
# Tensho Dofus
# CHANGELOGS / MISES À JOUR
########################################

import json
import re
import html
from pathlib import Path
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


# ============================================================
# CONFIGURATION
# ============================================================

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
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)


# ============================================================
# UTILITAIRES
# ============================================================

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
    ).strip()

    return value


def normalize_url(url):
    if not url:
        return ""

    url = urljoin(
        BASE_URL,
        url
    )

    return url.split(
        "#",
        1
    )[0].rstrip("/")


def is_valid_changelog_url(url):
    """
    Vérifie qu'une URL correspond réellement
    à un article de mise à jour.

    La page /maj/correctifs est une catégorie
    et ne doit PAS être considérée comme un article.
    """

    url = normalize_url(
        url
    )

    if not url:
        return False

    if url == normalize_url(
        LISTING_URL
    ):
        return False

    if url == (
        f"{BASE_URL}/fr/mmorpg/actualites/maj/correctifs"
    ):
        return False

    if "/fr/mmorpg/actualites/maj/" not in url:
        return False

    return True


# ============================================================
# DATES
# ============================================================

def parse_date_from_text(
    text
):
    """
    Recherche une date française directement
    dans un texte.

    Exemple :

    MÀJ 3.5 - Pas de repos pour les braves 03 Mars 2026

    retourne :

    03/03/2026 en datetime UTC.
    """

    if not text:
        return None

    text = clean_text(
        text
    )

    if not text:
        return None

    months = {
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

    match = re.search(
        r"\b"
        r"(\d{1,2})\s+"
        r"(janvier|février|fevrier|mars|avril|mai|juin|"
        r"juillet|août|aout|septembre|octobre|novembre|"
        r"décembre|decembre)"
        r"\s+"
        r"(\d{4})"
        r"\b",
        text,
        flags=re.IGNORECASE
    )

    if not match:
        return None

    day = int(
        match.group(1)
    )

    month_name = (
        match.group(2)
        .lower()
    )

    month = months.get(
        month_name
    )

    year = int(
        match.group(3)
    )

    if not month:
        return None

    try:

        return datetime(
            year,
            month,
            day,
            tzinfo=timezone.utc
        )

    except ValueError:

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
    # RFC / HTTP / RSS
    # --------------------------------------------------------

    try:

        dt = parsedate_to_datetime(
            value
        )

        if dt:

            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=timezone.utc
                )

            return dt.astimezone(
                timezone.utc
            )

    except Exception:
        pass

    # --------------------------------------------------------
    # ISO
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Formats classiques
    # --------------------------------------------------------

    formats = (
        "%d/%m/%Y",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
    )

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

    # --------------------------------------------------------
    # Dates françaises en texte
    # --------------------------------------------------------

    return parse_date_from_text(
        value
    )


def date_from_url(url):
    """
    Extrait dynamiquement une date d'une URL
    de patch note.

    Exemple :

    patch-notes-3-6-10-10-18-08-2026

    donne :

    18/08/2026
    """

    slug = normalize_url(
        url
    ).rsplit(
        "/",
        1
    )[-1]

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


# ============================================================
# TITRES
# ============================================================

def clean_title(title):

    title = clean_text(
        title
    )

    if not title:
        return ""

    title = re.sub(
        r"^\s*Découvrir\s*[:\-|]?\s*",
        "",
        title,
        flags=re.IGNORECASE
    )

    title = re.sub(
        r"\s*\|\s*DOFUS.*$",
        "",
        title,
        flags=re.IGNORECASE
    )

    return title.strip()


# ============================================================
# JSON
# ============================================================

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

    except Exception:

        pass

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


# ============================================================
# DATE JSON-LD
# ============================================================

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
                "dateModified",
            ):

                dt = parse_date(
                    obj.get(key)
                )

                if dt:
                    return dt

    return None


# ============================================================
# DATE META / TIME
# ============================================================

def extract_meta_date(
    soup
):

    meta_selectors = (
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
    )

    for attrs in meta_selectors:

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

    for tag in soup.find_all(
        "time"
    ):

        raw = (
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
            raw
        )

        if dt:
            return dt

    return None


# ============================================================
# TITRE ARTICLE
# ============================================================

def extract_title_from_soup(
    soup
):

    selectors = (
        "h1",
        '[data-testid="article-title"]',
        ".article-title",
        ".news-title",
    )

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

    if soup.title:

        title = clean_title(
            soup.title.get_text(
                " ",
                strip=True
            )
        )

        if title:
            return title

    return ""


# ============================================================
# EXTRACTION DATE DU LISTING
# ============================================================

def extract_listing_date(
    link
):

    # --------------------------------------------------------
    # Recherche de la carte
    # --------------------------------------------------------

    card = link.locator(
        "xpath=ancestor::*"
        "[self::article "
        "or contains(@class,'card') "
        "or contains(@class,'article')][1]"
    )

    # --------------------------------------------------------
    # 1. <time datetime="">
    # --------------------------------------------------------

    if card.count():

        try:

            times = card.locator(
                "time"
            )

            for i in range(
                times.count()
            ):

                time_tag = times.nth(
                    i
                )

                raw = (
                    time_tag.get_attribute(
                        "datetime"
                    )
                    or
                    time_tag.inner_text()
                )

                dt = parse_date(
                    raw
                )

                if dt:
                    return dt

        except Exception:
            pass

    # --------------------------------------------------------
    # 2. Attributs data-*
    # --------------------------------------------------------

    attributes = (
        "data-date",
        "data-published",
        "data-publish-date",
        "data-date-published",
        "datetime",
    )

    for attr in attributes:

        try:

            raw = link.get_attribute(
                attr
            )

            if not raw and card.count():

                raw = card.get_attribute(
                    attr
                )

            dt = parse_date(
                raw
            )

            if dt:
                return dt

        except Exception:
            pass

    # --------------------------------------------------------
    # 3. Texte de la carte
    # --------------------------------------------------------

    if card.count():

        try:

            text = clean_text(
                card.inner_text()
            )

            # Dates numériques
            patterns = (
                r"\b\d{1,2}/\d{1,2}/\d{4}\b",
                r"\b\d{4}-\d{1,2}-\d{1,2}\b",
            )

            for pattern in patterns:

                match = re.search(
                    pattern,
                    text
                )

                if match:

                    dt = parse_date(
                        match.group(0)
                    )

                    if dt:
                        return dt

            # Dates françaises
            match = re.search(
                r"\b\d{1,2}\s+"
                r"(?:janvier|février|fevrier|mars|avril|mai|juin|"
                r"juillet|août|aout|septembre|octobre|novembre|"
                r"décembre|decembre)\s+"
                r"\d{4}\b",
                text,
                flags=re.IGNORECASE
            )

            if match:

                dt = parse_date(
                    match.group(0)
                )

                if dt:
                    return dt

        except Exception:
            pass

    return None


# ============================================================
# EXTRACTION LISTING
# ============================================================

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

            href = normalize_url(
                link.get_attribute(
                    "href"
                )
            )

            if not is_valid_changelog_url(
                href
            ):
                continue

            if href in seen:
                continue

            title = clean_title(
                link.inner_text()
            )
            
            # ========================================================
            # DATE DIRECTEMENT DEPUIS LE TITRE DU LISTING
            # ========================================================
            
            dt = parse_date_from_text(
                title
            )
            
            # ========================================================
            # SI AUCUNE DATE DANS LE TITRE,
            # ON CHERCHE DANS LA CARTE
            # ========================================================
            
            if not dt:
            
                dt = extract_listing_date(
                    link
                )

            if dt:

                print(
                    "   📅 Date trouvée dans le listing: "
                    f"{format_datetime(dt, usegmt=True)}"
                )

            item = {
                "url":
                    href,

                "title":
                    title,

                "date":
                    (
                        dt.isoformat()
                        if dt
                        else None
                    ),
            }

            items.append(
                item
            )

            seen.add(
                href
            )

        except Exception:
            continue

    return items


# ============================================================
# COLLECTE LISTING + VOIR PLUS
# ============================================================

def collect_listing(
    page
):

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

    for attempt in range(
        8
    ):

        print(
            f"🔄 Recherche du bouton VOIR PLUS "
            f"({attempt + 1}/8)..."
        )

        selectors = (
            "button:has-text('VOIR PLUS')",
            "button:has-text('Voir plus')",
            "a:has-text('VOIR PLUS')",
            "a:has-text('Voir plus')",
            "[class*='load-more']",
            "[class*='loadmore']",
        )

        button = None

        for selector in selectors:

            try:

                locator = page.locator(
                    selector
                )

                if not locator.count():
                    continue

                for i in range(
                    locator.count()
                ):

                    candidate = locator.nth(
                        i
                    )

                    try:

                        if candidate.is_visible():

                            button = candidate
                            break

                    except Exception:
                        continue

                if button:
                    break

            except Exception:
                continue

        if not button:

            print(
                "ℹ️ Plus de bouton VOIR PLUS."
            )

            break

        previous_count = len(
            items
        )

        try:

            button.scroll_into_view_if_needed()

            page.wait_for_timeout(
                500
            )

            button.click(
                timeout=5000
            )

            page.wait_for_timeout(
                2000
            )

        except Exception:

            print(
                "ℹ️ Plus de contenu détecté."
            )

            break

        new_items = extract_listing_items(
            page
        )

        if len(new_items) <= previous_count:

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
# ============================================================
# CONTENU ARTICLE DOFUS
# ============================================================

def extract_article_content(page):
    """
    Récupère uniquement le contenu principal
    de l'article DOFUS.

    On évite volontairement le <body> complet
    afin de ne pas récupérer :
    - menus
    - navigation
    - boutons
    - sidebar
    - publicité
    - footer
    """

    selectors = [
        "article",
        ".article-content",
        ".news-content",
        ".article-body",
        ".content-article",
        ".ak-container",
        "main",
    ]

    for selector in selectors:

        try:

            locator = page.locator(
                selector
            ).first

            if locator.count() == 0:
                continue

            text = locator.inner_text(
                timeout=5000
            )

            text = text.strip()

            if len(text) < 100:
                continue

            # Nettoyage des lignes
            lines = []

            for line in text.splitlines():

                line = line.strip()

                if not line:
                    continue

                lines.append(line)

            text = "\n".join(
                lines
            )

            if len(text) >= 100:
                return text

        except Exception:
            continue

    return ""

# ============================================================
# ENRICHISSEMENT ARTICLE
# ============================================================

def enrich_item(
    page,
    item,
    cache
):

    url = normalize_url(
        item.get(
            "url"
        )
    )

    if not is_valid_changelog_url(
        url
    ):
        return None

    print(
        f"\n[{url}]"
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

    cached = cache.get(
        url,
        {}
    )

    cached_title = clean_title(
        cached.get(
            "title",
            ""
        )
    )

    cached_date = parse_date(
        cached.get(
            "date"
        )
    )

    is_correctif = (
        "/correctifs/" in
        url.lower()
    )

    # ========================================================
    # DATE DEPUIS URL
    # ========================================================

    url_date = None

    if is_correctif:

        url_date = date_from_url(
            url
        )

    # ========================================================
    # VALEURS INITIALES
    # ========================================================

    title = listing_title

    title_source = (
        "LISTING"
        if listing_title
        else None
    )

    dt = None
    date_source = None

    # ========================================================
    # OUVERTURE ARTICLE
    # ========================================================

    print(
        "🔎 Ouverture article avec Playwright..."
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

        # ====================================================
        # TITRE ARTICLE
        # ====================================================

        article_title = (
            extract_title_from_soup(
                soup
            )
        )

        # ====================================================
        # DATE ARTICLE
        # ====================================================

        article_date = (
            extract_jsonld_date(
                soup
            )
            or
            extract_meta_date(
                soup
            )
        )

        # ====================================================
        # TITRE
        # ====================================================

        if is_correctif:

            # ------------------------------------------------
            # CORRECTIF
            # ------------------------------------------------
            # Le H1 peut être celui de la MÀJ principale.
            # On privilégie donc le titre du listing.

            if listing_title:

                title = listing_title

                title_source = (
                    "LISTING CORRECTIF"
                )

            elif cached_title:

                title = cached_title

                title_source = (
                    "CACHE"
                )

            elif article_title:

                title = article_title

                title_source = (
                    "ARTICLE"
                )

        else:

            # ------------------------------------------------
            # ARTICLE PRINCIPAL
            # ------------------------------------------------
            # On privilégie le vrai titre de l'article.

            if (
                article_title
                and
                not article_title.lower().startswith(
                    "découvrir"
                )
            ):

                title = article_title

                title_source = (
                    "ARTICLE"
                )

            elif listing_title:

                title = listing_title

                title_source = (
                    "LISTING"
                )

            elif cached_title:

                title = cached_title

                title_source = (
                    "CACHE"
                )

        # ====================================================
        # DATE
        # ====================================================
        #
        # PRIORITÉ :
        #
        # CORRECTIF :
        #
        #     URL
        #       ↓
        #     TITRE ARTICLE
        #       ↓
        #     LISTING
        #       ↓
        #     ARTICLE
        #       ↓
        #     CACHE
        #
        # ARTICLE NORMAL :
        #
        #     TITRE ARTICLE
        #       ↓
        #     LISTING
        #       ↓
        #     ARTICLE
        #       ↓
        #     CACHE
        #
        # IMPORTANT :
        # Le cache n'écrase JAMAIS une donnée fraîche.
        # ====================================================

        title_date = None

        # ----------------------------------------------------
        # 1. DATE DANS LE TITRE DE L'ARTICLE
        # ----------------------------------------------------

        if article_title:

            title_date = parse_date_from_text(
                article_title
            )

        # ----------------------------------------------------
        # 2. CORRECTIF : DATE DEPUIS URL
        # ----------------------------------------------------

        if is_correctif and url_date:

            dt = url_date

            date_source = (
                "URL"
            )

        # ----------------------------------------------------
        # 3. DATE DU TITRE ARTICLE
        # ----------------------------------------------------

        elif title_date:

            dt = title_date

            date_source = (
                "TITRE ARTICLE"
            )

        # ----------------------------------------------------
        # 4. DATE DU LISTING
        # ----------------------------------------------------

        elif listing_date:

            dt = listing_date

            date_source = (
                "LISTING"
            )

        # ----------------------------------------------------
        # 5. DATE ARTICLE
        # ----------------------------------------------------

        elif article_date:

            dt = article_date

            date_source = (
                "ARTICLE"
            )

        # ----------------------------------------------------
        # 6. CACHE
        # ----------------------------------------------------

        elif cached_date:

            dt = cached_date

            date_source = (
                "CACHE"
            )

    except Exception as e:

        print(
            f"⚠️ Erreur lors de l'ouverture de l'article : {e}"
        )

        # ====================================================
        # FALLBACK SI PLAYWRIGHT ÉCHOUE
        # ====================================================

        if is_correctif and url_date:

            dt = url_date

            date_source = (
                "URL"
            )

        elif listing_date:

            dt = listing_date

            date_source = (
                "LISTING"
            )

        elif cached_date:

            dt = cached_date

            date_source = (
                "CACHE"
            )

    # ========================================================
    # FALLBACK DATE DEPUIS LE TITRE FINAL
    # ========================================================

    if not dt:

        article_title_date = parse_date_from_text(
            title
        )

        if is_correctif and url_date:

            dt = url_date

            date_source = (
                "URL"
            )

        elif article_title_date:

            dt = article_title_date

            date_source = (
                "TITRE"
            )

        elif listing_date:

            dt = listing_date

            date_source = (
                "LISTING"
            )

        elif article_date:

            dt = article_date

            date_source = (
                "ARTICLE"
            )

        elif cached_date:

            dt = cached_date

            date_source = (
                "CACHE"
            )

    # ========================================================
    # FALLBACK TITRE
    # ========================================================

    if not title:

        title = (
            cached_title
            or
            "Changelog DOFUS"
        )

        title_source = (
            "CACHE"
        )

    # ========================================================
    # VALIDATION
    # ========================================================

    if not dt:

        print(
            "⚠️ Date introuvable."
        )

        return None

    title = clean_title(
        title
    )

    if not title:

        print(
            "⚠️ Titre introuvable."
        )

        return None

    # ========================================================
    # LOG
    # ========================================================

    print(
        f"   🏷️ Titre trouvé via "
        f"{title_source}: "
        f"{title}"
    )

    print(
        f"   📅 Date trouvée via "
        f"{date_source}: "
        f"{format_datetime(dt, usegmt=True)}"
    )

    print(
        f"🟢 {format_datetime(dt, usegmt=True)} "
        f"- {title}"
    )

    # ========================================================
    # RESULTAT
    # ========================================================

    result = {
        "url":
            url,

        "title":
            title,

        "date":
            dt.isoformat(),
    }

    return result


# ============================================================
# RSS COMPLET
# ============================================================

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

    chunks = [
        '<?xml version="1.0" encoding="UTF-8"?>',

        '<rss version="2.0">',

        "<channel>",

        (
            "<title>"
            + html.escape(title)
            + "</title>"
        ),

        (
            "<link>"
            + html.escape(LISTING_URL)
            + "</link>"
        ),

        (
            "<description>"
            "Changelogs DOFUS"
            "</description>"
        ),

        (
            "<lastBuildDate>"
            + now
            + "</lastBuildDate>"
        ),
    ]

    for item in items:

        dt = parse_date(
            item.get(
                "date"
            )
        )

        if dt:

            pub = format_datetime(
                dt,
                usegmt=True
            )

        else:

            pub = now

        chunks.extend(
            [
                "<item>",

                (
                    "<title>"
                    + html.escape(
                        item["title"]
                    )
                    + "</title>"
                ),

                (
                    "<link>"
                    + html.escape(
                        item["url"]
                    )
                    + "</link>"
                ),

                (
                    '<guid isPermaLink="true">'
                    + html.escape(
                        item["url"]
                    )
                    + "</guid>"
                ),

                (
                    "<description>"
                    + html.escape(
                        item["title"]
                    )
                    + "</description>"
                ),

                (
                    "<pubDate>"
                    + pub
                    + "</pubDate>"
                ),

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
        "\n".join(
            chunks
        )
        + "\n",
        encoding="utf-8"
    )


# ============================================================
# RSS DISCORD
# ============================================================

def build_discord_rss(
    item
):

    """
    Flux extrêmement minimal pour Discord / Readybot.

    Un seul item.

    Contient obligatoirement :

    title
    link
    guid
    description
    pubDate

    Le pubDate est toujours valide.
    """

    if not item:

        xml = "\n".join(
            [
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<rss version="2.0">',
                "<channel>",
                "<title>DOFUS Changelog Discord</title>",
                f"<link>{html.escape(LISTING_URL)}</link>",
                "<description>Dernier changelog DOFUS</description>",
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
        item.get(
            "date"
        )
    )

    if not dt:

        dt = datetime.now(
            timezone.utc
        )

    pub = format_datetime(
        dt,
        usegmt=True
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
                "<link>"
                + html.escape(
                    item["url"]
                )
                + "</link>"
            ),

            "<description>"
            "Dernier changelog DOFUS"
            "</description>",

            "<item>",

            (
                "<title>"
                + html.escape(
                    item["title"]
                )
                + "</title>"
            ),

            (
                "<link>"
                + html.escape(
                    item["url"]
                )
                + "</link>"
            ),

            (
                '<guid isPermaLink="true">'
                + html.escape(
                    item["url"]
                )
                + "</guid>"
            ),

            (
                "<description>"
                + html.escape(
                    item["title"]
                )
                + "</description>"
            ),

            (
                "<pubDate>"
                + pub
                + "</pubDate>"
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

    # ========================================================
    # CACHE
    # ========================================================

    cache = load_json(
        CACHE_FILE,
        {}
    )

    print(
        f"Cache Changelog Dofus chargé : "
        f"{len(cache)} articles."
    )

    # ========================================================
    # PLAYWRIGHT
    # ========================================================

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page(
            user_agent=USER_AGENT,
            locale="fr-FR"
        )

        # ====================================================
        # LISTING
        # ====================================================

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

        print(
            f"Total URLs valides : "
            f"{len(listing)}"
        )

        results = []

        # ====================================================
        # TRAITEMENT
        # ====================================================

        for index, item in enumerate(
            listing,
            1
        ):

            print(
                f"\n[{index}/{len(listing)}] "
                f"{item['url']}"
            )

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

    # ========================================================
    # DÉDUPLICATION
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
    # TRI DYNAMIQUE
    # ========================================================

    results.sort(
        key=lambda item:
            parse_date(
                item.get(
                    "date"
                )
            )
            or
            datetime.min.replace(
                tzinfo=timezone.utc
            ),
        reverse=True
    )

    # ========================================================
    # LIMITE RSS
    # ========================================================

    results = results[:20]

    # ========================================================
    # AFFICHAGE
    # ========================================================

    print(
        """
########################################
"""
    )

    print(
        f"# {len(results)} Changelogs Dofus retenus"
    )

    print(
        """
########################################
"""
    )

    for index, item in enumerate(
        results,
        1
    ):

        dt = parse_date(
            item["date"]
        )

        print(
            f"{index:02d}. "
            f"{format_datetime(dt)} - "
            f"{item['title']}"
        )

        print(
            f"{item['url']}"
        )

    # ========================================================
    # CACHE
    # ========================================================

    """
    IMPORTANT :

    On sauvegarde les résultats fraîchement récupérés.

    Le cache ne sert qu'à récupérer une date ou un titre
    lorsqu'une future exécution ne parvient plus à les
    trouver.

    Il n'est JAMAIS prioritaire sur une donnée fraîche.
    """

    existing_cache = cache.copy()

    for item in results:

        existing_cache[
            item["url"]
        ] = item

    save_json(
        CACHE_FILE,
        existing_cache
    )

    # ========================================================
    # RSS COMPLET
    # ========================================================

    print(
        "\nGénération de "
        "dofus-changelog.xml..."
    )

    build_rss(
        results,
        RSS_FILE
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

    # ========================================================
    # AUCUN RESULTAT
    # ========================================================

    if not results:

        print(
            "⚠️ Aucun changelog disponible."
        )

        state = load_json(
            DISCORD_STATE_FILE,
            {}
        )

        previous_url = normalize_url(
            state.get(
                "url"
            )
        )

        previous_date = parse_date(
            state.get(
                "date"
            )
        )

        if (
            previous_url
            and
            previous_date
        ):

            previous_item = {
                "url":
                    previous_url,

                "title":
                    state.get(
                        "title",
                        "Changelog DOFUS"
                    ),

                "date":
                    previous_date.isoformat(),
            }

            build_discord_rss(
                previous_item
            )

        else:

            build_discord_rss(
                None
            )

        print(
            "🟢 dofus-changelog-discord.xml "
            "généré sans nouvel envoi."
        )

        return

    # ========================================================
    # DERNIER ARTICLE
    # ========================================================

    latest = results[0]

    latest_date = parse_date(
        latest["date"]
    )

    print(
        "\n🔎 Dernier changelog "
        "actuellement publié sur DOFUS :"
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
    # ETAT DISCORD
    # ========================================================

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

    # ========================================================
    # DETECTION NOUVEAU
    # ========================================================

    is_new = False

    if not last_url:

        is_new = True

    elif latest["url"] != last_url:

        is_new = True

    elif (
        latest_date
        and
        last_date
        and
        latest_date > last_date
    ):

        is_new = True

    # ========================================================
    # NOUVEAU CHANGELOG
    # ========================================================

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
                "url":
                    latest["url"],

                "title":
                    latest["title"],

                "date":
                    latest["date"],
            }
        )

        print(
            "🟢 État Discord sauvegardé."
        )

        print(
            "🟢 dofus-changelog-discord.xml "
            "généré avec 1 nouveau changelog."
        )

    # ========================================================
    # DEJA ENVOYE
    # ========================================================

    else:

        print(
            "ℹ️ Le dernier changelog "
            "DOFUS a déjà été envoyé."
        )

        # ----------------------------------------------------
        # On conserve le dernier article envoyé dans le RSS.
        # ----------------------------------------------------

        if (
            last_url
            and
            last_date
        ):

            previous_item = {
                "url":
                    last_url,

                "title":
                    state.get(
                        "title",
                        "Changelog DOFUS"
                    ),

                "date":
                    last_date.isoformat(),
            }

            build_discord_rss(
                previous_item
            )

        else:

            build_discord_rss(
                latest
            )

        print(
            "ℹ️ Aucun nouvel envoi Discord."
        )

        print(
            "🟢 dofus-changelog-discord.xml "
            "généré sans nouvel envoi."
        )

    # ========================================================
    # FIN
    # ========================================================

    print(
        """
########################################
# DOFUS CHANGELOG RSS TERMINÉ
########################################
"""
    )


# ============================================================
# EXECUTION
# ============================================================

if __name__ == "__main__":

    main()
