"""Retrieval: MeowDB MapleStory Classic guides (primary) + fallback sources.

Primary : meowdb.com/msclassic/guides (disk-cached, see guides.py)
Fallback: Nexon MapleStory news CMS (Classic World posts preferred), maplestorywiki.net (MediaWiki API,
          original MapleStory - may differ from Classic), StrategyWiki.
(maplestory.wiki.gg is behind a bot wall and returns 403, so it is not used.)
"""
import asyncio
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

from guides import GuideStore
from textutil import clean_ws, keywords, tokens

log = logging.getLogger("maplebot.retrieval")
UA = "MapleStoryClassicAssistantBot/1.0 (Discord Q&A bot; cites sources)"
TIMEOUT = httpx.Timeout(12.0)

WIKI_API = "https://maplestorywiki.net/api.php"
WIKI_PAGE = "https://maplestorywiki.net/w/"
STRAT_API = "https://strategywiki.org/w/api.php"
STRAT_PAGE = "https://strategywiki.org/wiki/"
NEWS_API = "https://g.nexonstatic.com/maplestory/cms/v1/news"
NEWS_PAGE = "https://www.nexon.com/maplestory/news/{category}/{id}"

NEWS_WORDS = {"patch", "update", "event", "events", "maintenance", "news", "latest", "recent", "notes", "release",
              "launch", "known", "issue", "issues", "nerf", "buff", "changes", "changelog", "debt", "sale", "v.271", "when"}


@dataclass
class Snippet:
    source: str   # short label
    title: str
    url: str
    text: str


class Retriever:
    def __init__(self, store: GuideStore):
        self.store = store
        self._cache: dict[str, tuple[float, object]] = {}
        self._news_feed: tuple[float, list] = (0, [])
        self._http = httpx.AsyncClient(headers={"User-Agent": UA}, timeout=TIMEOUT, follow_redirects=True)

    async def aclose(self):
        await self._http.aclose()

    # ------------- helpers -------------
    async def _get_json(self, url, params):
        key = url + "?" + urllib.parse.urlencode(sorted(params.items()))
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < 1800:
            return hit[1]
        r = await self._http.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        if len(self._cache) > 500:
            self._cache.clear()
        self._cache[key] = (time.time(), data)
        return data

    @staticmethod
    def _html_to_text(html: str, q_tokens: set[str], budget: int) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["script", "style", "sup", "img", "figure", "noscript"]):
            t.decompose()
        for t in soup.select(".toc, .navbox, .mw-editsection, .mswiki-subpage-tabs, .metadata, .noprint"):
            t.decompose()
        for tr in soup.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            tr.replace_with(soup.new_string("\n" + " | ".join(c for c in cells if c) + "\n"))
        lines = [l.strip() for l in soup.get_text("\n").split("\n") if len(l.strip()) > 2]
        if sum(len(l) for l in lines) <= budget:
            return "\n".join(lines)
        keep = set(range(min(6, len(lines))))
        scored = sorted(range(len(lines)), key=lambda i: -len(q_tokens & set(tokens(lines[i]))))
        used = sum(len(lines[i]) for i in keep)
        for i in scored:
            if i in keep or not (q_tokens & set(tokens(lines[i]))):
                continue
            if used + len(lines[i]) > budget:
                continue
            keep.add(i)
            used += len(lines[i])
        return "\n".join(lines[i] for i in sorted(keep))

    # ------------- MediaWiki (maplestorywiki.net / strategywiki) -------------
    async def _mw_pages(self, api: str, page_base: str, label: str, question: str, limit: int, budget: int,
                        title_prefix: str = "") -> list[Snippet]:
        kws = keywords(question)[:10]
        if not kws:
            return []
        qt = set(tokens(question))
        # candidate titles: title-cased 1-3 grams
        cands: list[tuple[int, str]] = []
        for n in (3, 2, 1):
            for i in range(len(kws) - n + 1):
                gram = " ".join(w.capitalize() for w in kws[i:i + n])
                cands.append((n, (title_prefix + gram) if title_prefix else gram))
        titles, seen = [], set()
        for n, t in cands:
            if t not in seen:
                seen.add(t)
                titles.append((n, t))
        titles = titles[:40]
        found: list[tuple[float, str]] = []
        try:
            if not title_prefix:
                data = await self._get_json(api, {"action": "query", "format": "json", "redirects": "1",
                                                  "titles": "|".join(t for _, t in titles)})
                norm_to_n = {t: n for n, t in titles}
                q = data.get("query", {})
                back = {}
                for lst in ("normalized", "redirects"):
                    for m in q.get(lst, []):
                        back[m["to"]] = m["from"]
                for p in q.get("pages", {}).values():
                    if "missing" in p or "invalid" in p:
                        continue
                    orig = p["title"]
                    while orig in back:
                        orig = back[orig]
                    found.append((10 * norm_to_n.get(orig, 1) + len(orig) / 4, p["title"]))
            search_q = (title_prefix.strip() + " " if title_prefix else "") + " ".join(kws)
            data = await self._get_json(api, {"action": "query", "format": "json", "list": "search", "srlimit": "6",
                                              "srnamespace": "0", "srsearch": search_q, "srprop": "size"})
            for i, s in enumerate(data.get("query", {}).get("search", [])):
                if s.get("size", 0) < 400:
                    continue
                if title_prefix and not s["title"].startswith(title_prefix):
                    continue
                found.append((3 - i * 0.3, s["title"]))
        except Exception as e:
            log.warning("%s search failed: %s", label, e)
            return []
        best: dict[str, float] = {}
        for sc, t in found:
            best[t] = best.get(t, 0) + sc
        top = [t for t, _ in sorted(best.items(), key=lambda x: -x[1])[:limit]]

        async def fetch(title):
            try:
                d = await self._get_json(api, {"action": "parse", "format": "json", "page": title, "prop": "text",
                                               "redirects": "1", "disablelimitreport": "1"})
                html = d["parse"]["text"]["*"]
                text = self._html_to_text(html, qt, budget)
                if len(text) < 80:
                    return None
                return Snippet(label, title, page_base + urllib.parse.quote(title.replace(" ", "_")), text)
            except Exception as e:
                log.warning("%s parse %s failed: %s", label, title, e)
                return None

        return [s for s in await asyncio.gather(*(fetch(t) for t in top)) if s]

    # ------------- Nexon news -------------
    async def _news(self, question: str, want_news: bool) -> list[Snippet]:
        try:
            feed = self._news_feed
            if time.time() - feed[0] > 600:
                r = await self._http.get(NEWS_API)
                r.raise_for_status()
                feed = (time.time(), r.json())
                self._news_feed = feed
            items = feed[1]
        except Exception as e:
            log.warning("news feed failed: %s", e)
            return []
        qt = set(tokens(question))
        scored = []
        for rank, it in enumerate(items[:200]):
            toks = set(tokens(it["name"] + " " + it.get("summary", "")))
            sc = len(qt & toks) * 2
            if want_news:
                sc += max(0, 3 - rank * 0.3)
                if it.get("isMSCW"):
                    sc += 3  # Classic World posts preferred
            if sc >= (2 if want_news else 4):
                scored.append((sc, it))
        scored.sort(key=lambda x: -x[0])
        picks = [it for _, it in scored[:2 if want_news else 1]]

        async def fetch(it):
            try:
                r = await self._http.get(f"{NEWS_API}/{it['id']}")
                r.raise_for_status()
                body = r.json().get("body", "")
                text = clean_ws(BeautifulSoup(body, "html.parser").get_text("\n"))[:3000]
                tag = "Classic World" if it.get("isMSCW") else "MapleStory"
                return Snippet("Nexon news", f"[{tag}] {it['name']} ({it['liveDate'][:10]})",
                               NEWS_PAGE.format(category=it["category"], id=it["id"]),
                               f"{it.get('summary', '')}\n{text}")
            except Exception as e:
                log.warning("news %s failed: %s", it.get("id"), e)
                return None

        return [s for s in await asyncio.gather(*(fetch(i) for i in picks)) if s]

    # ------------- public -------------
    async def retrieve(self, question: str) -> list[Snippet]:
        qwords = set(question.lower().replace("?", " ").split())
        want_news = bool(qwords & NEWS_WORDS)
        secs, top = self.store.search(question)
        snippets: list[Snippet] = []
        by_guide: dict[str, list] = {}
        for s in secs:
            by_guide.setdefault(s.guide_slug, []).append(s)
        for slug, ss in by_guide.items():
            body = "\n\n".join(f"## {s.heading}\n{s.text}" for s in ss)
            snippets.append(Snippet("MeowDB MapleStory Classic guide", ss[0].guide_title, ss[0].url.split("#")[0]
                                    if len(ss) > 1 else ss[0].url, body))
        strong = top >= 8 and len(secs) >= 2
        wiki_budget = 2500 if strong else 4000
        coros = [self._news(question, want_news),
                 self._mw_pages(WIKI_API, WIKI_PAGE, "MapleStory Wiki (maplestorywiki.net, original MapleStory - may differ from Classic)",
                                question, 1 if strong else 3, wiki_budget)]
        if not strong:
            coros.append(self._mw_pages(STRAT_API, STRAT_PAGE, "StrategyWiki", question, 1, 3000, title_prefix="MapleStory"))
        results = await asyncio.gather(*coros, return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                snippets.extend(r)
            else:
                log.warning("retrieval part failed: %r", r)
        return snippets


def format_context(snippets: list[Snippet]) -> str:
    if not snippets:
        return "(No reference material could be retrieved for this question.)"
    parts = []
    for i, s in enumerate(snippets, 1):
        parts.append(f"[{i}] {s.source} - {s.title}\nURL: {s.url}\n{s.text}")
    return "\n\n---\n\n".join(parts)
