"""
spain_news.py
Pulls Spain news headlines from RSS feeds (no APIs), translates them from
Spanish into English, categorizes each story as Diplomacy, Military, Energy,
Economy, or Local Events, and writes docs/spain_news.json.

Rules:
- Up to 20 stories per category, Spain as the primary subject.
- No story older than 7 days.
- New stories replace the oldest entries first; if fewer than 20 new relevant
  stories are found, only what was found is added.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests
from dateutil import parser as dateparser

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

COUNTRY = "Spain"
OUTPUT_DIR = "docs"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "spain_news.json")
MAX_PER_CATEGORY = 20
MAX_AGE_DAYS = 7
MAX_ENTRIES_PER_FEED = 30
CATEGORIES = ["Diplomacy", "Military", "Energy", "Economy", "Local Events"]
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# national=True  -> domestic/national section; uncategorized stories that name Spain default to Local Events.
# national=False -> general feed; only kept if it matches a category keyword.
# strict=True   -> regional feed; story must explicitly mention Spain.
# All other stories must mention Spain or at least not be clearly about another country.
FEEDS = [
    {"source": "El País", "url": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/espana/portada", "national": True},
    {"source": "El País", "url": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/economia/portada", "national": True},
    {"source": "El País", "url": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/internacional/portada", "national": False},
    {"source": "El Mundo", "url": "https://e00-elmundo.uecdn.es/elmundo/rss/espana.xml", "national": True},
    {"source": "El Mundo", "url": "https://e00-elmundo.uecdn.es/elmundo/rss/economia.xml", "national": True},
    {"source": "ABC", "url": "https://www.abc.es/rss/2.0/espana/", "national": True},
    {"source": "ABC", "url": "https://www.abc.es/rss/2.0/economia/", "national": True},
    {"source": "La Vanguardia", "url": "https://www.lavanguardia.com/rss/politica.xml", "national": True},
    {"source": "La Vanguardia", "url": "https://www.lavanguardia.com/rss/economia.xml", "national": True},
    {"source": "RTVE", "url": "https://api2.rtve.es/rss/temas_espana.xml", "national": True},
    {"source": "RTVE", "url": "https://api2.rtve.es/rss/temas_economia.xml", "national": True},
    {"source": "Europa Press", "url": "https://www.europapress.es/rss/rss.aspx?ch=00066", "national": True},
    {"source": "Europa Press", "url": "https://www.europapress.es/rss/rss.aspx?ch=00136", "national": True},
    {"source": "20minutos", "url": "https://www.20minutos.es/rss/nacional/", "national": True},
    {"source": "elDiario.es", "url": "https://www.eldiario.es/rss/", "national": False},
    {"source": "Expansión", "url": "https://e00-expansion.uecdn.es/rss/portada.xml", "national": True},
    {"source": "Infodefensa", "url": "https://www.infodefensa.com/rss", "national": False},
    {"source": "El Periódico de la Energía", "url": "https://elperiodicodelaenergia.com/feed/", "national": False},
]

# English terms (post-translation) that mark Spain as the subject.
COUNTRY_TERMS = [
    "spain", "spanish", "spaniards", "madrid", "barcelona", "catalonia",
    "catalan", "valencia", "andalusia", "basque", "galicia", "seville",
    "bilbao", "malaga", "zaragoza", "canary islands", "balearic", "sánchez",
    "sanchez", "feijóo", "feijoo", "abascal", "díaz", "moncloa",
    "congress of deputies", "psoe", "pp", "vox", "sumar", "podemos",
    "junts", "albares", "robles", "montero", "cuerpo", "civil guard",
    "guardia civil", "national police", "bank of spain", "ibex", "iberdrola", "repsol",
    "endesa", "naturgy", "telefónica", "santander", "bbva", "generalitat",
    "xunta",
]

# Terms that signal a story is mainly about another country.
FOREIGN_TERMS = [
    "united states", "u.s.", "america", "american", "washington", "trump",
    "white house", "china", "chinese", "beijing", "russia", "russian",
    "moscow", "putin", "ukraine", "ukrainian", "kyiv", "zelensky", "israel",
    "israeli", "gaza", "netanyahu", "iran", "iranian", "tehran", "india",
    "indian", "japan", "japanese", "north korea", "south korea", "germany",
    "german", "berlin", "france", "french", "paris", "britain", "british",
    "london", "venezuela", "brazil", "argentina", "mexico", "canada",
    "australia", "pakistan", "syria", "lebanon", "turkey", "egypt",
    "saudi", "qatar", "taiwan", "spain", "spanish", "madrid", "italy",
    "italian", "rome", "poland", "polish", "warsaw", "thailand", "thai",
    "bangkok", "uae", "emirates", "dubai", "abu dhabi", "chile", "peru",
    "colombia", "cuba", "greece", "greek", "hungary", "hungarian", "orban",
    "austria", "switzerland", "netherlands", "dutch", "belgium", "sweden",
    "norway", "finland", "denmark", "portugal", "nigeria", "south africa",
    "kenya", "sudan", "yemen", "iraq", "jordan", "kuwait", "bahrain",
    "oman", "morocco", "algeria", "tunisia", "libya", "cambodia", "myanmar",
    "vietnam", "malaysia", "indonesia", "philippines", "singapore",
    "south korea", "seoul", "tokyo", "new york", "afghanistan",
]
FOREIGN_TERMS = [t for t in FOREIGN_TERMS if t not in COUNTRY_TERMS]

# Off-topic stories to drop (sports, entertainment, lifestyle).
EXCLUDE_TERMS = [
    "football", "soccer", "champions league", "europa league", "serie a",
    "la liga", "laliga", "ekstraklasa", "premier league", "tennis", "golf",
    "formula 1", "f1", "motogp", "grand prix", "basketball", "volleyball",
    "boxing", "ufc", "muay thai", "cricket", "olympic", "goalkeeper",
    "striker", "coach", "match", "derby", "horoscope", "zodiac", "recipe",
    "celebrity", "actor", "actress", "singer", "concert", "film", "movie",
    "tv series", "netflix", "fashion", "lottery", "lotto", "gossip",
    "influencer", "reality show", "big brother", "uefa", "fifa", "nba",
        "atp", "wta", "messi", "sinner", "alcaraz", "nadal", "ronaldo",
        "real madrid", "fc barcelona", "juventus", "transfer window",
]

CATEGORY_KEYWORDS = {
    "Diplomacy": [
        "diplomacy", "diplomatic", "diplomat", "foreign minister",
        "foreign ministry", "foreign affairs", "foreign policy", "embassy",
        "ambassador", "consul", "consulate", "envoy", "treaty", "bilateral",
        "multilateral", "summit", "united nations", "un general assembly",
        "security council", "european union", "eu", "european commission",
        "brussels", "asean", "gcc", "arab league", "g7", "g20", "sanctions",
        "state visit", "official visit", "visits", "talks with", "agreement with",
        "memorandum", "mou", "relations with", "ties with", "cooperation with",
        "visa", "migration pact", "mediation", "ceasefire", "peace talks",
        "peace plan", "foreign leaders", "state visit",
        "albares", "latin america", "gibraltar", "morocco",
    ],
    "Military": [
        "military", "army", "navy", "naval", "air force", "armed forces",
        "defence", "defense", "defence minister", "defense minister",
        "troops", "soldier", "soldiers", "weapon", "weapons", "missile",
        "drone", "drones", "fighter jet", "f-35", "tank", "tanks",
        "submarine", "warship", "frigate", "nato", "military exercise",
        "drill", "border clash", "artillery", "ammunition", "conscription",
        "general staff", "airspace", "air defense",
        "air defence", "military base", "arms deal", "war", "warfare",
        "terrorism", "terrorist", "insurgent", "security forces", "coast guard",
        "robles", "rota", "navantia", "indra",
    ],
    "Energy": [
        "energy", "electricity", "power grid", "power plant", "blackout",
        "oil", "crude", "gas", "natural gas", "lng", "pipeline", "fuel",
        "petrol", "diesel", "gasoline", "renewable", "solar", "wind farm",
        "wind power", "offshore wind", "hydro", "hydrogen", "nuclear power",
        "nuclear plant", "reactor", "coal", "emissions", "decarbon",
        "energy transition", "energy price", "energy bill", "tariff",
        "refinery", "opec", "battery", "electric vehicle",
        "iberdrola", "repsol", "endesa", "naturgy", "red eléctrica", "redeia", "light bill", "electricity bill",
    ],
    "Economy": [
        "economy", "economic", "gdp", "inflation", "interest rate",
        "central bank", "budget", "fiscal", "tax", "taxes", "vat",
        "unemployment", "employment", "jobs", "labor", "labour", "wage",
        "wages", "salary", "pension", "recession", "growth", "trade",
        "exports", "imports", "tariffs", "investment", "investor",
        "stock", "stocks", "stock exchange", "shares", "bond", "bonds",
        "debt", "deficit", "bank", "banks", "banking", "finance minister",
        "ministry of finance", "treasury", "company", "companies", "firm",
        "business", "industry", "industrial", "manufacturing", "retail",
        "consumer", "prices", "cost of living", "housing market", "real estate",
        "property", "tourism", "tourists", "startup", "merger", "acquisition",
        "ipo", "profit", "revenue", "credit rating", "imf", "world bank",
        "ecofin", "excise", "spread", "yields", "stock market", "markets",
        "euro", "dollar", "gold", "exchange rate", "currency",
        "ibex", "cuerpo", "montero", "ine", "santander", "bbva", "telefónica",
    ],
    "Local Events": [
        "police", "court", "trial", "judge", "arrest", "arrested", "crime",
        "murder", "killed", "dead", "death", "injured", "accident", "crash",
        "fire", "flood", "floods", "storm", "earthquake", "heatwave",
        "weather", "rain", "drought", "wildfire", "landslide", "protest",
        "protesters", "strike", "demonstration", "rally", "election",
        "elections", "vote", "parliament", "mayor", "governor", "city",
        "municipal", "regional", "province", "village", "hospital",
        "health", "school", "university", "students", "teachers",
        "transport", "train", "railway", "airport", "metro", "road",
        "traffic", "housing", "migrants", "migration", "immigration",
        "government", "minister", "opposition", "party", "coalition",
        "corruption", "scandal", "festival", "church", "mosque", "temple",
        "autonomous community", "town hall", "dana", "council", "generalitat",
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def strip_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"&[a-z]+;|&#\d+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def has_term(text, terms):
    for term in terms:
        if re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", text):
            return True
    return False


def count_terms(text, terms):
    return sum(
        1 for term in terms
        if re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", text)
    )


def looks_english(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return True
    ascii_letters = sum(1 for c in letters if c.isascii())
    return ascii_letters / len(letters) > 0.97 and False


TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
MAX_CHUNK_CHARS = 1800  # characters of source text per request
stats = {"requests": 0, "fallbacks": 0, "failures": 0}


def _translate_call(text):
    """One request to Google Translate's free web endpoint (no key), with backoff."""
    for attempt in range(4):
        try:
            stats["requests"] += 1
            resp = requests.post(
                TRANSLATE_URL,
                params={"client": "gtx", "sl": "auto", "tl": "en", "dt": "t"},
                data={"q": text},
                headers=HEADERS,
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                return "".join(seg[0] for seg in data[0] if seg and seg[0])
            log.warning("Translation HTTP %s, retrying", resp.status_code)
        except Exception as exc:
            log.warning("Translation error, retrying: %s", str(exc)[:80])
        time.sleep(5 * (attempt + 1))
    stats["failures"] += 1
    return None


def _translate_chunk(lines):
    """Translate a list of single-line strings in one request (newline-joined).
    Splits into smaller chunks if the line count comes back different."""
    if not lines:
        return []
    if stats["failures"] >= 5:
        return lines  # translation service unreachable; keep originals
    result = _translate_call("\n".join(lines))
    if result is None:
        return lines
    out = [l.strip() for l in result.split("\n")]
    if len(out) == len(lines):
        return out
    if len(lines) == 1:
        return [result.strip()]
    stats["fallbacks"] += 1
    mid = len(lines) // 2
    return _translate_chunk(lines[:mid]) + _translate_chunk(lines[mid:])


def translate_all(texts):
    """Translate many strings to English using as few requests as possible."""
    results = list(texts)
    todo = [(i, t) for i, t in enumerate(texts) if t and not looks_english(t)]
    chunk, size = [], 0
    for item in todo + [None]:
        cost = len(item[1]) + 1 if item else 0
        if item is None or size + cost > MAX_CHUNK_CHARS:
            if chunk:
                translated = _translate_chunk([t for _, t in chunk])
                for (i, _), tr in zip(chunk, translated):
                    results[i] = tr or results[i]
                time.sleep(1)
            chunk, size = [], 0
        if item is not None:
            chunk.append((item[0], item[1][:1400]))
            size += cost
    log.info("Translated %d snippets in %d requests (%d chunk splits, %d failed)",
             len(todo), stats["requests"], stats["fallbacks"], stats["failures"])
    return results


def parse_date(entry):
    for key in ("published", "updated", "created", "dc_date"):
        raw = entry.get(key)
        if raw:
            try:
                dt = dateparser.parse(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                pass
    for key in ("published_parsed", "updated_parsed"):
        struct = entry.get(key)
        if struct:
            return datetime(*struct[:6], tzinfo=timezone.utc)
    return None


# On a tie, the more specific category wins.
TIE_BREAK = ["Energy", "Military", "Economy", "Diplomacy", "Local Events"]


def classify(text):
    scores = {cat: count_terms(text, kws) for cat, kws in CATEGORY_KEYWORDS.items()}
    best = max(TIE_BREAK, key=lambda c: scores[c])
    return best if scores[best] > 0 else None


def is_about_country(text, feed):
    if has_term(text, COUNTRY_TERMS):
        return True
    if feed.get("strict"):
        return False  # regional/pan-national feed: must name the country
    return not has_term(text, FOREIGN_TERMS)


def norm_title(title):
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_feed(feed, known_urls, cutoff):
    """Fetch one feed and return raw (untranslated) recent entries."""
    source, url = feed["source"], feed["url"]
    try:
        resp = requests.get(url, headers=HEADERS, timeout=25)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
    except Exception as exc:
        log.warning("FEED FAILED  %-28s %s (%s)", source, url, str(exc)[:60])
        return []
    if not parsed.entries:
        log.warning("FEED EMPTY   %-28s %s", source, url)
        return []

    raw = []
    now = datetime.now(timezone.utc)
    for entry in parsed.entries[:MAX_ENTRIES_PER_FEED]:
        link = (entry.get("link") or "").strip()
        if not link or link in known_urls:
            continue
        pub = parse_date(entry)
        if pub is None or pub < cutoff or pub > now + timedelta(hours=6):
            continue
        title = strip_html(entry.get("title", ""))
        if not title:
            continue
        known_urls.add(link)
        raw.append({
            "feed": feed,
            "url": link,
            "pub": pub,
            "title_raw": title,
            "desc_raw": strip_html(entry.get("summary", ""))[:160],
        })
    log.info("FEED OK      %-28s %3d entries, %3d new & recent  %s",
             source, len(parsed.entries), len(raw), url)
    return raw


def build_stories(raw):
    """Translate raw entries, then filter and categorize them."""
    texts = []
    for r in raw:
        texts.append(r["title_raw"])
        texts.append(r["desc_raw"])
    translated = translate_all(texts)

    stories = []
    for idx, r in enumerate(raw):
        title, desc = translated[2 * idx], translated[2 * idx + 1]
        text = (title + " " + desc).lower()
        if has_term(text, EXCLUDE_TERMS):
            continue
        if not is_about_country(text, r["feed"]):
            continue
        category = classify(text)
        if category is None:
            # Uncategorized domestic stories count as Local Events only if they
            # clearly name the country, a city/region, or a national figure.
            if not (r["feed"].get("national") and has_term(text, COUNTRY_TERMS)):
                continue
            category = "Local Events"
        stories.append({
            "title": title,
            "source": r["feed"]["source"],
            "url": r["url"],
            "published_date": r["pub"].isoformat(),
            "category": category,
        })
    return stories


# ---------------------------------------------------------------------------
# Load / merge / write
# ---------------------------------------------------------------------------

def load_existing():
    if not os.path.exists(OUTPUT_FILE):
        return []
    try:
        with open(OUTPUT_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("stories", []) if isinstance(data, dict) else data
    except Exception:
        return []


def merge(existing, fresh, cutoff):
    grouped = {cat: [] for cat in CATEGORIES}
    seen_urls, seen_titles = set(), set()
    for story in fresh + existing:
        cat = story.get("category")
        if cat not in grouped:
            continue
        try:
            pub = dateparser.parse(story["published_date"]).astimezone(timezone.utc)
        except Exception:
            continue
        if pub < cutoff:
            continue
        key_t = norm_title(story.get("title"))
        if story.get("url") in seen_urls or key_t in seen_titles:
            continue
        seen_urls.add(story.get("url"))
        seen_titles.add(key_t)
        grouped[cat].append(story)

    for cat in CATEGORIES:
        # Newest first; anything beyond 20 (the oldest) is dropped.
        grouped[cat].sort(key=lambda s: s["published_date"], reverse=True)
        grouped[cat] = grouped[cat][:MAX_PER_CATEGORY]
    return grouped


def write_output(grouped):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stories = [s for cat in CATEGORIES for s in grouped[cat]]
    out = {
        "country": COUNTRY,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "story_count": len(stories),
        "stories": stories,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    log.info("Wrote %d stories to %s", len(stories), OUTPUT_FILE)


def main():
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    existing = load_existing()
    known_urls = {s.get("url") for s in existing if s.get("url")}
    log.info("Loaded %d existing stories", len(existing))

    raw = []
    for feed in FEEDS:
        raw.extend(fetch_feed(feed, known_urls, cutoff))
        time.sleep(0.5)
    log.info("Collected %d new entries; translating ...", len(raw))
    fresh = build_stories(raw)
    log.info("Found %d new relevant stories", len(fresh))

    grouped = merge(existing, fresh, cutoff)
    log.info("Category totals: %s", {c: len(grouped[c]) for c in CATEGORIES})
    write_output(grouped)


if __name__ == "__main__":
    main()
