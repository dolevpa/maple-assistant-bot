"""MeowDB (NiaMeowDB) MapleStory Classic guides: fetch, parse, disk cache, periodic refresh, search.

Source: https://meowdb.com/msclassic/guides  (server-rendered HTML, no JSON API needed).
Politeness: honest User-Agent, one request at a time with a delay between requests,
conditional refresh only when the cache is older than GUIDES_REFRESH_HOURS, content is used
only as at-question-time retrieval context and always cited with its URL.
"""
import asyncio
import json
import logging
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

import config
from textutil import clean_ws, tokens

log = logging.getLogger("maplebot.guides")

BASE = "https://meowdb.com"
INDEX_URL = f"{BASE}/msclassic/guides"
CACHE_DIR = Path(__file__).resolve().parent / "cache" / "guides"
UA = "MapleStoryClassicAssistantBot/1.0 (Discord Q&A bot; cites sources; contact: bot owner)"
REQUEST_DELAY = 1.5  # seconds between requests to meowdb.com
SLUG_RE = re.compile(r"^/msclassic/guides/([a-z0-9][a-z0-9-]*)/?$")


@dataclass
class Section:
    guide_slug: str
    guide_title: str
    heading: str
    anchor: str | None
    text: str

    @property
    def url(self) -> str:
        u = f"{BASE}/msclassic/guides/{self.guide_slug}"
        return f"{u}#{self.anchor}" if self.anchor else u


def _table_to_text(table) -> str:
    rows = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def parse_guide(slug: str, html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup.body
    if main is None:
        return None
    for t in main(["script", "style", "noscript", "svg", "img", "button", "nav", "form", "iframe"]):
        t.decompose()
    for t in main.find_all("table"):
        t.replace_with(soup.new_string("\n" + _table_to_text(t) + "\n"))
    h1 = main.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else (soup.title.string or slug)
    meta = soup.find("meta", attrs={"name": "description"})
    desc = meta["content"].strip() if meta and meta.get("content") else ""
    m = re.search(r'"dateModified":"([^"]+)"', html)
    modified = m.group(1) if m else ""

    sections: list[dict] = []
    cur = {"heading": title, "anchor": None, "lines": []}
    h2 = title

    def flush():
        txt = clean_ws("\n".join(cur["lines"]))
        if len(txt) > 40:
            sections.append({"heading": cur["heading"], "anchor": cur["anchor"], "text": txt})

    # Walk headings in document order; text between headings belongs to the previous heading.
    for node in main.descendants:
        name = getattr(node, "name", None)
        if name in ("h1", "h2", "h3", "h4"):
            flush()
            htxt = node.get_text(" ", strip=True)
            if name == "h2":
                h2 = htxt
            full = htxt if name in ("h1", "h2") else f"{h2} > {htxt}"
            cur = {"heading": full, "anchor": node.get("id") or None, "lines": []}
        elif name is None:  # NavigableString
            parent = node.parent.name if node.parent else ""
            if parent in ("h1", "h2", "h3", "h4"):
                continue
            s = str(node).strip()
            if s:
                cur["lines"].append(s)
    flush()
    # Strip site chrome strings
    chrome = ("Ad blocked?", "Buy us a coffee")
    for s in sections:
        s["text"] = "\n".join(l for l in s["text"].split("\n") if not l.startswith(chrome))
    # Merge very small sections into the previous one for coherence
    merged: list[dict] = []
    for s in sections:
        if merged and len(s["text"]) < 500 and len(merged[-1]["text"]) < 3000:
            merged[-1]["text"] += f"\n{s['heading']}: {s['text']}"
        else:
            merged.append(s)
    return {"slug": slug, "title": title, "description": desc, "modified": modified,
            "fetched_at": time.time(), "sections": merged}


class GuideStore:
    def __init__(self):
        self.guides: dict[str, dict] = {}
        self._idx: list[tuple[Section, Counter, int]] = []
        self._df: Counter = Counter()
        self._avgdl = 1.0
        self._lock = asyncio.Lock()
        self.last_refresh = 0.0
        self.load_disk()

    # ---- disk cache ----
    def load_disk(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for p in CACHE_DIR.glob("*.json"):
            try:
                g = json.loads(p.read_text())
                self.guides[g["slug"]] = g
            except Exception as e:  # corrupt cache file: ignore
                log.warning("bad cache file %s: %s", p.name, e)
        if self.guides:
            self.last_refresh = max(g.get("fetched_at", 0) for g in self.guides.values())
        self._build_index()
        log.info("guide cache loaded: %d guides", len(self.guides))

    def _save(self, g: dict):
        tmp = CACHE_DIR / f"{g['slug']}.json.tmp"
        tmp.write_text(json.dumps(g, ensure_ascii=False))
        tmp.replace(CACHE_DIR / f"{g['slug']}.json")

    # ---- fetching ----
    async def refresh(self, force: bool = False) -> int:
        """Re-fetch index and any guide older than the refresh interval. Returns #guides fetched."""
        async with self._lock:
            max_age = config.guides_refresh_hours() * 3600
            fetched = 0
            async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=30, follow_redirects=True) as c:
                try:
                    r = await c.get(INDEX_URL)
                    r.raise_for_status()
                except Exception as e:
                    log.warning("guide index fetch failed: %s", e)
                    return 0
                slugs = []
                for a in BeautifulSoup(r.text, "html.parser").find_all("a", href=True):
                    m = SLUG_RE.match(a["href"].split("#")[0].split("?")[0])
                    if m and m.group(1) not in slugs:
                        slugs.append(m.group(1))
                log.info("guide index lists %d guides", len(slugs))
                for slug in slugs:
                    g = self.guides.get(slug)
                    if g and not force and time.time() - g.get("fetched_at", 0) < max_age:
                        continue
                    await asyncio.sleep(REQUEST_DELAY)
                    try:
                        r = await c.get(f"{INDEX_URL}/{slug}")
                        r.raise_for_status()
                        parsed = parse_guide(slug, r.text)
                        if parsed and parsed["sections"]:
                            self.guides[slug] = parsed
                            self._save(parsed)
                            fetched += 1
                    except Exception as e:
                        log.warning("guide %s fetch failed: %s", slug, e)
                # drop guides no longer listed
                for slug in list(self.guides):
                    if slugs and slug not in slugs:
                        self.guides.pop(slug)
                        (CACHE_DIR / f"{slug}.json").unlink(missing_ok=True)
            self.last_refresh = time.time()
            self._build_index()
            log.info("guide refresh done: fetched=%d total=%d", fetched, len(self.guides))
            return fetched

    async def refresh_loop(self):
        await asyncio.sleep(2)
        while True:
            try:
                await self.refresh()
            except Exception:
                log.exception("guide refresh crashed")
            await asyncio.sleep(max(600, config.guides_refresh_hours() * 3600 / 4))

    # ---- search (BM25 over sections) ----
    def _build_index(self):
        idx = []
        df: Counter = Counter()
        for g in self.guides.values():
            for s in g["sections"]:
                sec = Section(g["slug"], g["title"], s["heading"], s["anchor"], s["text"])
                # heading + guide title words get extra weight by repetition
                toks = tokens(sec.text) + tokens(sec.heading) * 3 + tokens(sec.guide_title) * 2
                c = Counter(toks)
                idx.append((sec, c, len(toks)))
                df.update(c.keys())
        self._idx, self._df = idx, df
        self._avgdl = (sum(n for _, _, n in idx) / len(idx)) if idx else 1.0

    def search(self, question: str, max_chars: int = 14000, max_sections: int = 8):
        """Returns (sections, top_score). Sections are the best-matching, trimmed to the char budget."""
        q = tokens(question)
        if not q or not self._idx:
            return [], 0.0
        n = len(self._idx)
        k1, b = 1.4, 0.75
        scored = []
        for sec, c, dl in self._idx:
            sc = 0.0
            for t in set(q):
                f = c.get(t, 0)
                if not f:
                    continue
                idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                sc += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / self._avgdl))
            if sc > 0:
                scored.append((sc, sec))
        scored.sort(key=lambda x: -x[0])
        out, used = [], 0
        cutoff = scored[0][0] * 0.45 if scored else 0
        for sc, sec in scored[:max_sections]:
            if sc < cutoff:
                break
            room = max_chars - used
            if room < 400:
                break
            if len(sec.text) > room or len(sec.text) > 3500:
                text = sec.text[: min(room, 3500)].rsplit("\n", 1)[0]
                sec = Section(sec.guide_slug, sec.guide_title, sec.heading, sec.anchor, text)
            out.append(sec)
            used += len(sec.text)
        return out, (scored[0][0] if scored else 0.0)
