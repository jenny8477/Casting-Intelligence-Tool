from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import requests
import urllib.parse
import uvicorn
import re
import os
import json
import time
import xml.etree.ElementTree as ET

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


@app.get("/")
def read_index():
    path = os.path.join(BASE_DIR, "index.html")
    return FileResponse(path) if os.path.exists(path) else {"error": "index.html not found"}


# ─────────────────────────────────────────────
# 공통 유틸
# ─────────────────────────────────────────────

def _wiki_summary(name: str, lang: str = 'en') -> dict | None:
    try:
        res = requests.get(
            f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(name)}",
            headers=HEADERS, timeout=6
        )
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        print(f"[wiki:{lang}] {e}")
    return None


# ─────────────────────────────────────────────
# 스크래퍼
# ─────────────────────────────────────────────

def scrape_profile_img(name: str) -> str | None:
    for lang in ('en', 'ko'):
        data = _wiki_summary(name, lang)
        if not data:
            continue
        orig = data.get('originalimage', {}).get('source')
        if orig:
            return orig
        thumb = data.get('thumbnail', {}).get('source')
        if thumb:
            return thumb
    return None


def _parse_view_count(s: str) -> int:
    """'조회수 1,234,567회' 또는 '1,234,567 views' → 1234567. 실패 시 0."""
    if not s:
        return 0
    m = re.search(r'[\d,]+', s)
    if not m:
        return 0
    try:
        return int(m.group(0).replace(',', ''))
    except ValueError:
        return 0


def _parse_yt_videos(html: str, limit: int = 15) -> list:
    """YouTube 검색 페이지 HTML → 영상 메타 리스트."""
    m = re.search(r'var ytInitialData = ({.*?});</script>', html, re.DOTALL)
    if not m:
        return []
    try:
        yt = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    sections = (yt.get('contents', {})
                  .get('twoColumnSearchResultsRenderer', {})
                  .get('primaryContents', {})
                  .get('sectionListRenderer', {})
                  .get('contents', []))
    videos = []
    for section in sections:
        for item in section.get('itemSectionRenderer', {}).get('contents', []):
            vid = item.get('videoRenderer')
            if not vid:
                continue
            vid_id = vid.get('videoId')
            title_runs = vid.get('title', {}).get('runs', [])
            title = ''.join(r.get('text', '') for r in title_runs).strip()
            if not vid_id or not title:
                continue
            view_full = vid.get('viewCountText', {}).get('simpleText', '')
            view_short = vid.get('shortViewCountText', {}).get('simpleText', '')
            owner_runs = vid.get('ownerText', {}).get('runs', [])
            channel = owner_runs[0].get('text', '') if owner_runs else ''
            published = vid.get('publishedTimeText', {}).get('simpleText', '')
            length = vid.get('lengthText', {}).get('simpleText', '')
            videos.append({
                'title':     title,
                'link':      f'https://www.youtube.com/watch?v={vid_id}',
                'views':     _parse_view_count(view_full or view_short),
                'view_text': view_short or view_full or '—',
                'channel':   channel,
                'published': published,
                'length':    length,
            })
            if len(videos) >= limit:
                return videos
    return videos


def scrape_youtube_top(encoded_name: str) -> list:
    """
    검색 결과 15개를 파싱해 영향력 있는 4개를 선별:
      1) 🔍 검색 1위 (알고리즘 노출 1위)
      2) 🔥 최다 조회 (조회수 최고)
      3,4) 📈 인기 영상 (나머지 고조회)
    실패 시 [].
    """
    try:
        res = requests.get(
            f"https://www.youtube.com/results?search_query={encoded_name}",
            headers=HEADERS, timeout=8
        )
        videos = _parse_yt_videos(res.text, limit=15)
    except Exception as e:
        print(f"[yt_top] {e}")
        return []

    if not videos:
        return []

    by_views = sorted(videos, key=lambda v: v['views'], reverse=True)
    result = []
    seen = set()

    # 1. 검색 알고리즘 1위
    top = videos[0]
    top['badge'] = '🔍 검색 1위'
    result.append(top)
    seen.add(top['link'])

    # 2. 최다 조회 (1위와 다른 영상)
    for v in by_views:
        if v['link'] not in seen:
            v['badge'] = '🔥 최다 조회'
            result.append(v)
            seen.add(v['link'])
            break

    # 3-4. 남은 고조회 영상
    for v in by_views:
        if len(result) >= 4:
            break
        if v['link'] in seen:
            continue
        if v['views'] <= 0:
            continue
        v['badge'] = '📈 인기 영상'
        result.append(v)
        seen.add(v['link'])

    return result


def scrape_youtube_channel(encoded_name: str) -> dict:
    """
    YouTube 채널 검색 (sp=EgIQAg%253D%253D = 채널 필터).
    반환: {"url": ... | None, "subs": "구독자 X명" | "N/A"}
    """
    result = {"url": None, "subs": "N/A"}
    try:
        res = requests.get(
            f"https://www.youtube.com/results?search_query={encoded_name}&sp=EgIQAg%253D%253D",
            headers=HEADERS, timeout=8
        )
        m = re.search(r'var ytInitialData = ({.*?});</script>', res.text, re.DOTALL)
        if not m:
            return result
        yt = json.loads(m.group(1))
        sections = (yt.get('contents', {})
                      .get('twoColumnSearchResultsRenderer', {})
                      .get('primaryContents', {})
                      .get('sectionListRenderer', {})
                      .get('contents', []))
        for section in sections:
            for item in section.get('itemSectionRenderer', {}).get('contents', []):
                ch = item.get('channelRenderer')
                if not ch:
                    continue
                channel_id = ch.get('channelId')
                # YouTube 가 필드 위치를 자주 바꿈 → 두 필드 모두 훑고 "구독자"/"subscriber" 포함된 값 채택
                sub_text = None
                for field in ('videoCountText', 'subscriberCountText'):
                    txt = ch.get(field, {}).get('simpleText', '')
                    if '구독자' in txt or 'subscriber' in txt.lower():
                        sub_text = txt
                        break
                if channel_id:
                    result["url"] = f"https://www.youtube.com/channel/{channel_id}"
                    if sub_text:
                        # "구독자 1020만명" → "1020만"
                        clean = re.sub(r'구독자\s*|\s*명$|\s*subscribers?', '', sub_text).strip()
                        result["subs"] = clean if clean else sub_text
                    return result
    except Exception as e:
        print(f"[yt_channel] {e}")
    return result


def _find_ig_username_wiki(name: str) -> str | None:
    """
    Wikipedia(ko → en) 외부 링크(extlinks) 에서 공식 IG username 추출.
    리디렉트(예: BTS→방탄소년단) 자동 처리.
    """
    excluded = {'p', 'reel', 'reels', 'explore', 'accounts', 'about',
                'direct', 'tv', 'stories', 'web', 'legal', 'directory'}
    for src in ('ko', 'en'):
        try:
            r = requests.get(
                f'https://{src}.wikipedia.org/w/api.php',
                params={
                    'action': 'query',
                    'titles': name,
                    'prop': 'extlinks',
                    'ellimit': 'max',
                    'redirects': '1',
                    'format': 'json',
                },
                headers=HEADERS, timeout=6
            )
            if r.status_code != 200:
                continue
            pages = r.json().get('query', {}).get('pages', {})
            for pid, page in pages.items():
                if pid == '-1':
                    continue
                for el in page.get('extlinks', []):
                    url = el.get('*', '')
                    m = re.search(r'instagram\.com/([a-zA-Z0-9._]+)', url)
                    if m and m.group(1).lower() not in excluded:
                        return m.group(1)
        except Exception as e:
            print(f"[ig:wiki-extlinks:{src}] {e}")
    return None


def _scrape_ig_followers(username: str) -> str | None:
    """
    IG 프로필 페이지의 og:description meta 태그에서 팔로워 수 추출.
    facebookexternalhit UA 를 쓰면 IG 가 로그인 벽 없이 OG 메타데이터 반환.
    예: 'og:description' = '33M Followers, 130 Following, 781 Posts - See ...'
    반환: '33M' | None
    """
    try:
        r = requests.get(
            f'https://www.instagram.com/{username}/',
            headers={
                'User-Agent': 'facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)',
                'Accept': 'text/html,application/xhtml+xml,*/*;q=0.9',
                'Accept-Language': 'en-US,en;q=0.9',
            },
            timeout=8,
            allow_redirects=True,
        )
        if r.status_code != 200:
            print(f"[ig:og] HTTP {r.status_code}")
            return None
        m = re.search(
            r'<meta[^>]*property=["\']og:description["\'][^>]*content=["\']([^"\']+)',
            r.text
        )
        if not m:
            return None
        og = m.group(1)
        # "33M Followers, 130 Following, 781 Posts - See Instagram photos..."
        m2 = re.match(r'^\s*([\d,.]+\s*[KMB]?)\s+Followers', og, re.IGNORECASE)
        if m2:
            return m2.group(1).replace(' ', '')
    except Exception as e:
        print(f"[ig:og] {e}")
    return None


def scrape_instagram(name: str) -> dict:
    """
    1) DuckDuckGo 로 IG username 확보 (DDG 는 202 챌린지 자주 반환 → 재시도)
    2) facebookexternalhit UA 로 IG 프로필 페이지 fetch → og:description 팔로워 수 파싱
    3) 실패 시 '조회 제한'
    """
    result = {"url": None, "followers": "N/A"}
    excluded = {'p', 'reel', 'reels', 'explore', 'accounts', 'about',
                'direct', 'tv', 'stories', 'web', 'legal'}

    # 1. Wikipedia extlinks 에서 공식 IG username 우선 조회 (안정적)
    username = _find_ig_username_wiki(name)

    # 2. 못 찾으면 DuckDuckGo 검색으로 fallback (DDG 는 봇 챌린지 202 자주 반환)
    if not username:
        q = urllib.parse.quote(f"{name} instagram")
        for attempt in range(3):
            try:
                res = requests.get(
                    f"https://html.duckduckgo.com/html/?q={q}",
                    headers=HEADERS, timeout=6
                )
                if res.status_code == 200:
                    for u in re.findall(r'instagram\.com/([a-zA-Z0-9._]+)', res.text):
                        if u.lower() not in excluded and len(u) >= 2 and not u.startswith('_'):
                            username = u
                            break
                    if username:
                        break
                else:
                    print(f"[ig:ddg] attempt {attempt+1} status={res.status_code}")
            except Exception as e:
                print(f"[ig:ddg] attempt {attempt+1} error: {e}")
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))

    if not username:
        return result

    result["url"] = f"https://www.instagram.com/{username}/"

    # 3. IG 프로필 페이지에서 팔로워 수 추출
    followers = _scrape_ig_followers(username)
    result["followers"] = followers if followers else "조회 제한"

    return result


# ─────────────────────────────────────────────
# 뉴스 / 리스크 / 글로벌
# ─────────────────────────────────────────────

def _gnews_items(query: str, limit: int = 4) -> list | None:
    try:
        url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
        res = requests.get(url, headers=HEADERS, timeout=8)
        if res.status_code != 200:
            return None
        root = ET.fromstring(res.content)
        items = []
        for item in root.findall('.//item')[:limit]:
            t = item.find('title')
            l = item.find('link')
            if t is None or not t.text:
                continue
            m = re.match(r'^(.*?)\s*-\s*([^-]+)$', t.text)
            title = (m.group(1) if m else t.text).strip()
            source = m.group(2).strip() if m else ''
            link = l.text.strip() if (l is not None and l.text) else ''
            if title and link:
                items.append({"title": title, "link": link, "source": source})
        return items
    except Exception as e:
        print(f"[gnews] {e}")
        return None


def scrape_news_effect(encoded_name: str) -> list | None:
    for suffix in ('+광고+효과', '+완판'):
        items = _gnews_items(encoded_name + suffix, limit=4)
        if items is None:
            return None
        if items:
            return items
    return []


def scrape_risk(encoded_name: str) -> dict | None:
    items = _gnews_items(encoded_name + '+논란', limit=3)
    if items is None:
        return None
    if items:
        snippet = items[0]['title'][:80]
        return {"status": "risk", "text": f"⚠️ 논란 관련 기사 {len(items)}건 감지: {snippet}"}
    return {"status": "clean", "text": "✅ 논란·사건 관련 뉴스 없음"}


def scrape_global(name: str) -> str | None:
    for lang, label in (('en', 'Wikipedia'), ('ko', '한국어 Wikipedia')):
        data = _wiki_summary(name, lang)
        if data:
            extract = data.get('extract', '').strip()
            desc = data.get('description', '')
            if extract:
                prefix = f"[{desc}] " if desc else ""
                return f"🌏 {label} 등재 확인 {prefix}— {extract[:150]}..."
    return None


# ─────────────────────────────────────────────
# 국가별 관심도 — Google Trends 우선, Wikipedia 폴백
# ─────────────────────────────────────────────

# 언어 코드 → (국기, 대표 국가/시장)
LANG_COUNTRY_MAP = {
    'en': ('🇺🇸', '미국 · 영어권'),
    'ko': ('🇰🇷', '대한민국'),
    'ja': ('🇯🇵', '일본'),
    'zh': ('🇨🇳', '중화권'),
    'es': ('🇪🇸', '스페인 · 중남미'),
    'fr': ('🇫🇷', '프랑스'),
    'de': ('🇩🇪', '독일'),
    'pt': ('🇵🇹', '브라질 · 포르투갈'),
    'ru': ('🇷🇺', '러시아'),
    'id': ('🇮🇩', '인도네시아'),
    'vi': ('🇻🇳', '베트남'),
    'th': ('🇹🇭', '태국'),
    'it': ('🇮🇹', '이탈리아'),
    'ar': ('🇸🇦', '아랍권'),
}

# Google Trends 결과의 한국어 국가명 → 국기
_COUNTRY_FLAG = {
    '대한민국': '🇰🇷', '미국': '🇺🇸', '일본': '🇯🇵', '중국': '🇨🇳', '대만': '🇹🇼',
    '홍콩': '🇭🇰', '싱가포르': '🇸🇬', '말레이시아': '🇲🇾', '인도네시아': '🇮🇩',
    '태국': '🇹🇭', '베트남': '🇻🇳', '필리핀': '🇵🇭', '브루나이': '🇧🇳', '몽골': '🇲🇳',
    '영국': '🇬🇧', '독일': '🇩🇪', '프랑스': '🇫🇷', '이탈리아': '🇮🇹', '스페인': '🇪🇸',
    '러시아': '🇷🇺', '캐나다': '🇨🇦', '호주': '🇦🇺', '인도': '🇮🇳', '브라질': '🇧🇷',
    '멕시코': '🇲🇽', '터키': '🇹🇷', '사우디아라비아': '🇸🇦', '아랍에미리트': '🇦🇪',
    '카자흐스탄': '🇰🇿', '키르기스스탄': '🇰🇬', '우즈베키스탄': '🇺🇿', '투르크메니스탄': '🇹🇲',
    '미얀마': '🇲🇲', '캄보디아': '🇰🇭', '라오스': '🇱🇦', '네덜란드': '🇳🇱', '폴란드': '🇵🇱',
}


def _fetch_langlinks(name: str) -> dict:
    """한국어→영어 Wikipedia 순으로 langlinks 조회. {'lang': 'title'} 반환."""
    for source in ('ko', 'en'):
        try:
            r = requests.get(
                f"https://{source}.wikipedia.org/w/api.php",
                params={
                    'action': 'query',
                    'titles': name,
                    'prop': 'langlinks',
                    'lllimit': '500',
                    'format': 'json',
                },
                headers=HEADERS, timeout=6
            )
            if r.status_code != 200:
                continue
            pages = r.json().get('query', {}).get('pages', {})
            for pid, page in pages.items():
                if pid == '-1':
                    continue
                titles = {source: page.get('title', name)}
                for ll in page.get('langlinks', []):
                    if ll.get('lang') and ll.get('*'):
                        titles[ll['lang']] = ll['*']
                if len(titles) > 1:
                    return titles
        except Exception as e:
            print(f"[langlinks:{source}] {e}")
    return {}


def _fetch_pageviews(lang: str, title: str, start: str, end: str) -> int:
    """해당 언어 Wikipedia 의 최근 페이지뷰 합계. 실패/빈값 시 0."""
    try:
        encoded = urllib.parse.quote(title.replace(' ', '_'), safe='')
        r = requests.get(
            f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
            f"{lang}.wikipedia/all-access/all-agents/{encoded}/daily/{start}/{end}",
            headers=HEADERS, timeout=5
        )
        if r.status_code == 200:
            return sum(i.get('views', 0) for i in r.json().get('items', []))
    except Exception as e:
        print(f"[pageviews:{lang}] {e}")
    return 0


def _try_google_trends(name: str) -> list | None:
    """
    pytrends 로 Google Trends 지역별 관심도 조회.
    성공: [{"country","flag","score"}, ...] (score 0-100)
    실패/rate-limit: None
    """
    try:
        from pytrends.request import TrendReq
    except ImportError:
        return None
    try:
        pt = TrendReq(hl='ko-KR', tz=540)
        pt.build_payload([name], timeframe='today 12-m', geo='')
        df = pt.interest_by_region(resolution='COUNTRY', inc_low_vol=False)
        if name not in df.columns:
            return None
        series = df[df[name] > 0][name].sort_values(ascending=False)
        if len(series) < 1:
            return None
        top_val = int(series.iloc[0])
        if top_val <= 0:
            return None
        result = []
        for country_name, raw in series.items():
            flag = _COUNTRY_FLAG.get(country_name, '🌐')
            score = int(round((int(raw) / top_val) * 100))
            result.append({"country": country_name, "flag": flag, "score": score})
        return result
    except Exception as e:
        print(f"[gtrends] {type(e).__name__}: {str(e)[:100]}")
        return None


def _wikipedia_interest(name: str) -> list:
    """언어별 Wikipedia 페이지뷰(최근 30일) → 정규화 0-100 점수."""
    titles = _fetch_langlinks(name)
    if not titles:
        return []

    end = (datetime.now() - timedelta(days=4)).strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=34)).strftime('%Y%m%d')

    targets = {lang: title for lang, title in titles.items() if lang in LANG_COUNTRY_MAP}
    if not targets:
        return []

    raw = []
    with ThreadPoolExecutor(max_workers=min(10, len(targets))) as ex:
        fut_map = {lang: ex.submit(_fetch_pageviews, lang, title, start, end)
                   for lang, title in targets.items()}
        for lang, fut in fut_map.items():
            views = fut.result()
            if views > 0:
                flag, country = LANG_COUNTRY_MAP[lang]
                raw.append({"country": country, "flag": flag, "views": views})

    if not raw:
        return []
    raw.sort(key=lambda x: x['views'], reverse=True)
    top = raw[0]['views']
    return [{
        "country": r["country"],
        "flag": r["flag"],
        "score": int(round((r["views"] / top) * 100)),
    } for r in raw]


def scrape_global_interest(name: str) -> dict:
    """
    Google Trends 우선 → rate-limit/실패 시 Wikipedia 페이지뷰로 폴백.
    반환: {"source": "google_trends"|"wikipedia"|None, "countries": [...]}
    각 country 는 {"country","flag","score"} — score 0-100 (상위 = 100).
    """
    gt = _try_google_trends(name)
    # GT 는 한국어 쿼리 시 1개 국가만 반환하는 경우가 많아
    # 최소 3개국 확보된 경우만 GT 채택, 아니면 Wikipedia 로 폴백
    if gt and len(gt) >= 3:
        return {"source": "google_trends", "countries": gt[:3]}

    wiki = _wikipedia_interest(name)
    if wiki:
        return {"source": "wikipedia", "countries": wiki[:3]}

    # 마지막 최후: GT 가 1-2개라도 있으면 그거라도 표시
    if gt:
        return {"source": "google_trends", "countries": gt[:3]}

    return {"source": None, "countries": []}


# ─────────────────────────────────────────────
# 요약
# ─────────────────────────────────────────────

def build_summary(name: str, news_effect: list, risk: dict | None,
                  global_info: str | None, ig: dict, yt_ch: dict) -> str:
    parts = []
    if risk:
        parts.append(
            f"{name}님은 논란·리스크 지표 양호."
            if risk['status'] == 'clean'
            else f"{name}님 최근 이슈 존재 — 캐스팅 시 주의 필요."
        )
    if news_effect:
        parts.append(f"광고·마케팅 관련 뉴스 {len(news_effect)}건 감지됨.")
    if global_info:
        parts.append("Wikipedia 등재로 글로벌 인지도 검증됨.")

    sns_bits = []
    if ig.get('followers') not in ('N/A', '조회 제한', '조회 실패', '비공개 계정', '계정 없음', None):
        sns_bits.append(f"IG {ig['followers']}")
    if yt_ch.get('subs') not in ('N/A', None):
        sns_bits.append(f"YT {yt_ch['subs']}")
    if sns_bits:
        parts.append(f"SNS 영향력 — {' · '.join(sns_bits)}.")

    return " ".join(parts) if parts else "수집된 데이터가 부족합니다."


# ─────────────────────────────────────────────
# 메인 수집 (병렬 실행)
# ─────────────────────────────────────────────

def get_detailed_data(name: str) -> dict:
    name = name.strip()
    encoded_name = urllib.parse.quote(name)
    errors: list[str] = []

    # IG 는 DDG 가 병렬 요청에 202 챌린지를 반환하므로 단독 먼저 실행
    ig = scrape_instagram(name)

    # 나머지 7개는 병렬 (서로 다른 도메인이라 간섭 없음)
    with ThreadPoolExecutor(max_workers=7) as ex:
        f_img       = ex.submit(scrape_profile_img, name)
        f_yt_top    = ex.submit(scrape_youtube_top, encoded_name)
        f_yt_ch     = ex.submit(scrape_youtube_channel, encoded_name)
        f_news      = ex.submit(scrape_news_effect, encoded_name)
        f_risk      = ex.submit(scrape_risk, encoded_name)
        f_global    = ex.submit(scrape_global, name)
        f_interest  = ex.submit(scrape_global_interest, name)

        profile_img     = f_img.result()
        top_yt          = f_yt_top.result()
        yt_channel      = f_yt_ch.result()
        news_effect     = f_news.result()
        risk_data       = f_risk.result()
        global_info     = f_global.result()
        global_interest = f_interest.result()

    if not profile_img:
        errors.append("프로필 이미지 없음 (Wikipedia 미등재)")
    if not top_yt:
        errors.append("YouTube 영상 데이터 없음")
    if news_effect is None:
        errors.append("뉴스 효과 데이터 수집 실패")
        news_effect = []
    if risk_data is None:
        errors.append("리스크 데이터 수집 실패")
    if not global_info:
        errors.append("글로벌 데이터 없음 (Wikipedia 미등재)")
    if not ig.get('url'):
        errors.append("Instagram 계정을 찾지 못했습니다")
    if not yt_channel.get('url'):
        errors.append("YouTube 채널을 찾지 못했습니다")
    if not global_interest.get('countries'):
        errors.append("국가별 관심도 데이터 없음 (Google Trends · Wikipedia 모두 미확보)")

    summary = build_summary(name, news_effect, risk_data, global_info, ig, yt_channel)

    return {
        "profile_img":     profile_img,
        "top_yt":          top_yt,
        "news_effect":     news_effect,
        "risk":            risk_data['text'] if risk_data else None,
        "global":          global_info,
        "summary":         summary,
        "ig_url":          ig.get('url'),
        "ig_followers":    ig.get('followers', 'N/A'),
        "yt_url":          yt_channel.get('url'),
        "yt_subs":         yt_channel.get('subs', 'N/A'),
        "global_interest": global_interest,
        "errors":          errors,
    }


@app.get("/analyze")
def analyze(name: str):
    details = get_detailed_data(name)
    return {
        "name":            name.strip(),
        "profile_img":     details["profile_img"],
        "summary":         details["summary"],
        "global":          details["global"],
        "risk":            details["risk"],
        "news_effect":     details["news_effect"],
        "top_yt":          details["top_yt"],
        "ig_url":          details["ig_url"],
        "ig_followers":    details["ig_followers"],
        "yt_url":          details["yt_url"],
        "yt_subs":         details["yt_subs"],
        "global_interest": details["global_interest"],
        "errors":          details["errors"],
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
