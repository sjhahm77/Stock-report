"""최근 1주일 뉴스 / 유튜브 / 블로그 수집.

우선순위
  - 뉴스  : 네이버 검색 API(키 있을 때) + Google 뉴스 RSS(키 불필요)
  - 블로그: 네이버 블로그 검색 API (NAVER_CLIENT_ID/SECRET 필요)
  - 유튜브: YouTube Data API v3(키 있을 때) → 없으면 유튜브 검색 결과 페이지 파싱(이번 주 업로드 필터)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import feedparser
import requests

from .utils import KST, strip_html

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}


@dataclass
class MediaItem:
    kind: str  # news | youtube | blog
    title: str
    url: str
    source: str = ""
    published: datetime | None = None
    snippet: str = ""

    def as_prompt_line(self) -> str:
        when = self.published.strftime("%m/%d") if self.published else "?"
        body = f" — {self.snippet[:200]}" if self.snippet else ""
        return f"[{when}] ({self.source}) {self.title}{body}"


def _since(days: int) -> datetime:
    return datetime.now(KST) - timedelta(days=days)


def _relevant(item: MediaItem, name: str) -> bool:
    key = name.replace(" ", "")
    return key in (item.title + item.snippet).replace(" ", "")


def _dedupe(items: list[MediaItem]) -> list[MediaItem]:
    seen, out = set(), []
    for it in items:
        k = re.sub(r"\W", "", it.title)[:40]
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return sorted(out, key=lambda x: x.published or datetime.min.replace(tzinfo=KST), reverse=True)


# ------------------------------------------------------------------ 네이버
def naver_search(kind: str, query: str, client_id: str, client_secret: str, days: int = 7, display: int = 100) -> list[MediaItem]:
    resp = requests.get(
        f"https://openapi.naver.com/v1/search/{kind}.json",
        params={"query": query, "display": display, "sort": "date"},
        headers={"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret},
        timeout=15,
    )
    resp.raise_for_status()
    since = _since(days)
    out = []
    for it in resp.json().get("items", []):
        if kind == "news":
            pub = parsedate_to_datetime(it["pubDate"]).astimezone(KST) if it.get("pubDate") else None
            link = it.get("originallink") or it.get("link", "")
            source = re.sub(r"^https?://(www\.)?", "", link).split("/")[0]
        else:
            pd_ = it.get("postdate", "")
            pub = datetime.strptime(pd_, "%Y%m%d").replace(tzinfo=KST) if pd_ else None
            link, source = it.get("link", ""), it.get("bloggername", "")
        if pub and pub < since.replace(hour=0, minute=0, second=0):
            continue
        out.append(
            MediaItem(
                kind="news" if kind == "news" else "blog",
                title=strip_html(it.get("title")),
                url=link,
                source=source,
                published=pub,
                snippet=strip_html(it.get("description")),
            )
        )
    return out


# ------------------------------------------------------------------ Google 뉴스 RSS
def google_news(query: str, days: int = 7) -> list[MediaItem]:
    url = f"https://news.google.com/rss/search?q={quote(f'{query} when:{days}d')}&hl=ko&gl=KR&ceid=KR:ko"
    resp = requests.get(url, headers=UA, timeout=15)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    since = _since(days)
    out = []
    for e in feed.entries:
        pub = None
        if getattr(e, "published", None):
            try:
                pub = parsedate_to_datetime(e.published).astimezone(KST)
            except (TypeError, ValueError):
                pub = None
        if pub and pub < since:
            continue
        source = getattr(getattr(e, "source", None), "title", "") or ""
        title = strip_html(e.get("title", ""))
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        # Google 뉴스 RSS 의 summary 는 제목/언론사 링크의 반복이라 본문 요약으로 쓰지 않는다
        out.append(MediaItem("news", title, e.get("link", ""), source, pub))
    return out


# ------------------------------------------------------------------ 유튜브
def youtube_api(query: str, api_key: str, days: int = 7, max_results: int = 25) -> list[MediaItem]:
    after = (datetime.now(KST) - timedelta(days=days)).astimezone().isoformat(timespec="seconds")
    resp = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "part": "snippet",
            "q": query,
            "type": "video",
            "order": "date",
            "publishedAfter": after,
            "maxResults": max_results,
            "regionCode": "KR",
            "relevanceLanguage": "ko",
            "key": api_key,
        },
        timeout=15,
    )
    resp.raise_for_status()
    out = []
    for it in resp.json().get("items", []):
        sn = it.get("snippet", {})
        pub = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")).astimezone(KST) if sn.get("publishedAt") else None
        out.append(
            MediaItem(
                "youtube",
                strip_html(sn.get("title")),
                f"https://www.youtube.com/watch?v={it['id']['videoId']}",
                sn.get("channelTitle", ""),
                pub,
                strip_html(sn.get("description")),
            )
        )
    return out


def youtube_scrape(query: str) -> list[MediaItem]:
    """API 키 없이 유튜브 검색 페이지(업로드 날짜: 이번 주)를 파싱. 페이지 구조가 바뀌면 빈 목록을 돌려준다."""
    resp = requests.get(
        "https://www.youtube.com/results",
        params={"search_query": query, "sp": "EgIIAw=="},  # 필터: 이번 주
        headers={**UA, "Accept-Language": "ko-KR,ko;q=0.9"},
        timeout=15,
    )
    resp.raise_for_status()
    return parse_youtube_initial_data(resp.text)


def parse_youtube_initial_data(html: str) -> list[MediaItem]:
    m = re.search(r"var ytInitialData\s*=\s*(\{.*?\});\s*</script>", html, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    out: list[MediaItem] = []

    def text(node) -> str:
        if not node:
            return ""
        if "simpleText" in node:
            return node["simpleText"]
        return "".join(r.get("text", "") for r in node.get("runs", []))

    def walk(node):
        if isinstance(node, dict):
            vr = node.get("videoRenderer")
            if vr and vr.get("videoId"):
                snippet = ""
                for snip in vr.get("detailedMetadataSnippets", []) or []:
                    snippet = text(snip.get("snippetText"))
                    break
                out.append(
                    MediaItem(
                        "youtube",
                        text(vr.get("title")),
                        f"https://www.youtube.com/watch?v={vr['videoId']}",
                        text(vr.get("ownerText")),
                        None,
                        (text(vr.get("publishedTimeText")) + " · " + snippet).strip(" ·"),
                    )
                )
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return out


# ------------------------------------------------------------------ 통합
@dataclass
class MediaBundle:
    news: list[MediaItem]
    youtube: list[MediaItem]
    blog: list[MediaItem]
    errors: list[str]


def collect_media(name: str, keys: dict[str, str], days: int = 7) -> MediaBundle:
    errors: list[str] = []
    nid, nsec = keys.get("NAVER_CLIENT_ID"), keys.get("NAVER_CLIENT_SECRET")
    news: list[MediaItem] = []
    blog: list[MediaItem] = []
    yt: list[MediaItem] = []

    def attempt(label, fn, *args):
        try:
            return fn(*args)
        except Exception as e:  # 네트워크/파싱 실패는 섹션 단위로 격리
            errors.append(f"{label}: {e}")
            return []

    if nid and nsec:
        news += attempt("네이버 뉴스", naver_search, "news", f"{name} 주가", nid, nsec, days)
        news += attempt("네이버 뉴스", naver_search, "news", name, nid, nsec, days)
        blog += attempt("네이버 블로그", naver_search, "blog", f"{name} 주가", nid, nsec, days)
        blog += attempt("네이버 블로그", naver_search, "blog", f"{name} 주식", nid, nsec, days)
    else:
        errors.append("네이버 검색 API 키가 없어 블로그 검색은 생략했습니다 (뉴스는 Google 뉴스로 대체).")
    news += attempt("Google 뉴스", google_news, f'"{name}"', days)

    if keys.get("YOUTUBE_API_KEY"):
        yt = attempt("YouTube API", youtube_api, f"{name} 주가", keys["YOUTUBE_API_KEY"], days)
    if not yt:
        yt = attempt("YouTube 검색", youtube_scrape, f"{name} 주가")

    return MediaBundle(
        news=_dedupe([i for i in news if _relevant(i, name)]),
        youtube=_dedupe([i for i in yt if _relevant(i, name)]),
        blog=_dedupe([i for i in blog if _relevant(i, name)]),
        errors=errors,
    )
