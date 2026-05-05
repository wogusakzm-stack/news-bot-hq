import os
import re
import json
import html
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, List, Dict, Optional
from difflib import SequenceMatcher
from urllib.parse import urlparse, urlsplit, urlunsplit, parse_qsl, urlencode
from zoneinfo import ZoneInfo

import aiohttp
import gspread
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google import genai

# -----------------------------
# 환경 설정 및 상수
# -----------------------------
load_dotenv()

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")
GOV_API_KEY = os.getenv("GOV_API_KEY")
GOV_ENDPOINT = os.getenv("GOV_ENDPOINT")

# 구글 시트 연동을 위한 환경 변수
GOOGLE_SHEETS_JSON = os.getenv("GOOGLE_SHEETS_JSON")
SHEET_ID = os.getenv("SHEET_ID")

SEEN_FILE = "seen_urls.json"

KST = ZoneInfo("Asia/Seoul")
UTC = timezone.utc

MODEL_CANDIDATES = [
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash-lite",
]

FORCE_TEST_MODE = False
MAX_ARTICLE_AGE_DAYS = 3
PER_KEYWORD_DISPLAY = 8
ARTICLES_PER_TOPIC = 4
TELEGRAM_SAFE_LIMIT = 3200
SIMILARITY_THRESHOLD = 0.84
HTTP_TIMEOUT_SECONDS = 12
HTTP_CONCURRENCY = 5

GLOBAL_BLOCKED_DOMAINS = {
    "breaknews.com", "lecturernews.com", "tongilnews.com",
}

GLOBAL_BLOCKED_TITLE_KEYWORDS = [
    "기고", "칼럼", "사설", "오피니언", "특강", "강연", "저자를 만나다",
]

TRUSTED_DOMAIN_SCORES = {
    "yna.co.kr": 3, "news1.kr": 3, "sbs.co.kr": 3, "kbs.co.kr": 3,
    "imbc.com": 3, "ytn.co.kr": 3, "mk.co.kr": 2, "hankyung.com": 2,
    "sedaily.com": 2, "edaily.co.kr": 2, "newsis.com": 2, "joongang.co.kr": 2,
    "chosun.com": 2, "donga.com": 2, "khan.co.kr": 2, "fnnews.com": 2,
    "etnews.com": 2, "zdnet.co.kr": 2, "ddaily.co.kr": 2, "bloter.net": 1,
    "thelec.kr": 1, "biz.chosun.com": 1, "korea.kr": 3,
}

TOPIC_CONFIGS = {
    "경제": {
        "search_keywords": ["증시", "환율", "금리", "물가", "부동산", "고용", "수출", "한국은행", "연준", "원달러", "stock market", "interest rate"],
        "positive_keywords": ["코스피", "코스닥", "국채", "채권", "집값", "분양", "인플레이션", "경기침체", "경상수지", "환율", "기준금리", "부동산", "수출", "고용"],
        "negative_keywords": ["대선", "총선", "국회", "대통령실", "여야", "외교", "도핑", "선수", "강연", "특강", "교수", "포럼", "전시", "공연", "수목원"],
        "blocked_domains": set(),
        "min_score": 7,
    },
    "정치": {
        "search_keywords": ["국회", "대통령실", "여야", "정당", "총리", "장관", "대선", "총선", "정치권", "청문회"],
        "positive_keywords": ["법안", "의결", "개각", "지지율", "당대표", "원내대표", "선거", "공천", "외교안보", "정부", "대통령", "국회", "정당"],
        "negative_keywords": ["증시", "환율", "금리", "물가", "부동산", "비트코인", "도핑", "선수", "강연", "특강", "교수", "미디어학부", "수목원", "공연", "전시", "스마트팩토리"],
        "blocked_domains": set(),
        "min_score": 7,
    },
    "사회·생활문화": {
        "search_keywords": ["사건", "법원", "검찰", "경찰", "교육", "건강", "복지", "육아", "생활문화", "전시", "공연"],
        "positive_keywords": ["사고", "판결", "기소", "수사", "병원", "보건", "양육", "문화", "도서관", "축제", "교육", "복지", "생활"],
        "negative_keywords": ["증시", "환율", "금리", "물가", "부동산", "비트코인", "연준", "대선", "총선", "대통령실", "외교", "제재", "AI 모델", "오픈AI", "엔비디아", "러시아산 원유", "도핑", "선수"],
        "blocked_domains": set(),
        "min_score": 7,
    },
    "세계": {
        "search_keywords": ["국제", "미국", "중국", "일본", "유럽", "중동", "우크라이나", "러시아", "EU", "대만", "international news", "world politics"],
        "positive_keywords": ["백악관", "중동", "휴전", "제재", "정상회담", "외신", "나토", "가자", "이스라엘", "트럼프", "바이든", "국제", "전쟁", "외교"],
        "negative_keywords": ["증시", "환율", "금리", "물가", "부동산", "비트코인", "주가", "휘발윳값", "도핑", "선수", "윔블던", "야구", "축구", "농구", "강연", "특강", "교수", "수목원", "공연", "전시"],
        "blocked_domains": set(),
        "min_score": 8,
    },
    "IT·과학": {
        "search_keywords": ["AI", "인공지능", "반도체", "오픈AI", "엔비디아", "로봇", "양자", "우주", "테크", "스마트팩토리", "artificial intelligence", "semiconductor"],
        "positive_keywords": ["챗GPT", "LLM", "GPU", "파운드리", "삼성전자", "SK하이닉스", "자율주행", "생성형 AI", "모델", "데이터센터", "로보틱스", "반도체", "과학", "우주"],
        "negative_keywords": ["총선", "대선", "국회", "여야", "외교", "도핑", "선수", "수목원", "전시", "공연", "저자를 만나다", "특강", "기고", "칼럼", "정치"],
        "blocked_domains": set(),
        "min_score": 8,
    },
}


# -----------------------------
# 로깅
# -----------------------------
class KSTFormatter(logging.Formatter):
    """logging.Formatter의 시간을 한국시간으로 고정한다."""

    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        dt = datetime.fromtimestamp(record.created, KST)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S")


def setup_logging() -> logging.Logger:
    formatter = KSTFormatter("%(asctime)s KST | %(levelname)s | %(message)s")
    stream_handler = logging.StreamHandler()
    file_handler = logging.FileHandler("news_bot.log", encoding="utf-8")

    stream_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)

    logging.basicConfig(
        level=logging.INFO,
        force=True,
        handlers=[stream_handler, file_handler],
    )
    return logging.getLogger(__name__)


logger = setup_logging()
_genai_client: Optional[genai.Client] = None


# -----------------------------
# 시간 유틸리티
# -----------------------------
def now_kst() -> datetime:
    return datetime.now(KST)


def now_utc() -> datetime:
    return datetime.now(UTC)


def fmt_kst(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return now_kst().strftime(fmt)


# -----------------------------
# 공통 유틸리티
# -----------------------------
def require_env() -> None:
    required = {
        "NAVER_CLIENT_ID": NAVER_CLIENT_ID,
        "NAVER_CLIENT_SECRET": NAVER_CLIENT_SECRET,
        "GEMINI_API_KEY": GEMINI_API_KEY,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"환경변수 누락: {', '.join(missing)}")


def get_genai_client() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client(api_key=GEMINI_API_KEY)
    return _genai_client


def strip_html(text: str) -> str:
    return BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True)


def normalize_text(text: str) -> str:
    text = strip_html(text)
    text = re.sub(r"[\"'“”‘’]", "", text)
    text = re.sub(r"[^\w\s가-힣·:/-]", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def normalize_title(title: str) -> str:
    title = strip_html(title)
    title = re.sub(r"\[(속보|단독|오피셜|사진|영상)\]", "", title)
    title = re.sub(r"\((종합|상보|1보|2보|3보)\)", "", title)
    return normalize_text(title)


def shorten_title(title: str, limit: int = 28) -> str:
    title = re.sub(r"\s+", " ", strip_html(title)).strip()
    return title if len(title) <= limit else title[:limit].rstrip() + "…"


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def is_similar_title(title: str, title_list: List[str], threshold: float = SIMILARITY_THRESHOLD) -> bool:
    return any(similarity(title, existing) >= threshold for existing in title_list)


def parse_pub_date(pub_date: str) -> Optional[datetime]:
    """RFC 2822, ISO 8601, YYYY-MM-DD 계열 날짜를 UTC datetime으로 변환한다."""
    if not pub_date:
        return None

    text = str(pub_date).strip()
    if not text:
        return None

    # 1) Naver pubDate: Tue, 05 May 2026 09:28:00 +0900
    try:
        dt = parsedate_to_datetime(text)
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
    except Exception:
        pass

    # 2) GNews: 2026-05-05T00:28:00Z / 2026-05-05T00:28:00+00:00
    try:
        iso_text = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso_text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        pass

    # 3) PolicyBriefing류: 2026-05-05 / 20260505 / 2026.05.05
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d", "%Y.%m.%d", "%Y.%m.%d %H:%M"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=KST)
            return dt.astimezone(UTC)
        except Exception:
            continue

    return None


def is_recent(pub_date: str, max_days: int = MAX_ARTICLE_AGE_DAYS) -> bool:
    dt = parse_pub_date(pub_date)
    if not dt:
        return False
    return now_utc() - dt <= timedelta(days=max_days)


def get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def canonicalize_url(url: str) -> str:
    try:
        split = urlsplit(url)
        keep_keys = {
            "news_id", "articleid", "idxno", "idx", "id", "wr_id",
            "news_no", "no", "article_id", "pnttsn",
        }
        query_items = [
            (k, v)
            for k, v in parse_qsl(split.query, keep_blank_values=True)
            if k.lower() in keep_keys
        ]
        return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query_items), ""))
    except Exception:
        return url


def domain_score(url: str) -> int:
    return TRUSTED_DOMAIN_SCORES.get(get_domain(url), 0)


def is_globally_blocked(title: str, url: str) -> bool:
    if get_domain(url) in GLOBAL_BLOCKED_DOMAINS:
        return True
    norm_title = normalize_text(title)
    return any(kw.lower() in norm_title for kw in GLOBAL_BLOCKED_TITLE_KEYWORDS)


# -----------------------------
# 구글 시트 저장 유틸리티
# -----------------------------
def parse_google_sheets_json(raw: str) -> dict:
    """GitHub Secret에 저장한 JSON을 dict로 변환한다."""
    value = raw.strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        # Secret에 줄바꿈이 이스케이프된 문자열 형태로 들어간 경우 보정
        return json.loads(value.replace("\\n", "\n"))


def save_to_google_sheet(topic_name: str, summary: str, articles: list[dict]) -> None:
    try:
        if not GOOGLE_SHEETS_JSON or not SHEET_ID:
            logger.warning("구글 시트 환경 변수 누락. 시트 저장을 건너뜁니다.")
            return

        creds_dict = parse_google_sheets_json(GOOGLE_SHEETS_JSON)
        gc = gspread.service_account_from_dict(creds_dict)
        sh = gc.open_by_key(SHEET_ID)
        worksheet = sh.get_worksheet(0)

        now_str = fmt_kst("%Y-%m-%d %H:%M:%S")
        titles = " | ".join([a.get("title", "") for a in articles])

        worksheet.append_row([now_str, topic_name, summary, titles])
        logger.info(f"[{topic_name}] 구글 시트 저장 완료! (๑>ᴗ<๑)")

    except Exception as e:
        logger.exception(f"[{topic_name}] 구글 시트 저장 실패: {e}")


# -----------------------------
# 파일 캐시 처리
# -----------------------------
def load_seen() -> dict[str, list[str]]:
    if not os.path.exists(SEEN_FILE):
        return {"urls": [], "titles": []}

    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        logger.warning(f"{SEEN_FILE} 읽기 실패. 새 캐시로 시작합니다.")
        return {"urls": [], "titles": []}

    if isinstance(data, list):
        return {"urls": [x for x in data if isinstance(x, str)], "titles": []}

    if isinstance(data, dict):
        urls = data.get("urls", [])
        titles = data.get("titles", [])
        return {
            "urls": [x for x in (urls if isinstance(urls, list) else []) if isinstance(x, str)],
            "titles": [x for x in (titles if isinstance(titles, list) else []) if isinstance(x, str)],
        }

    return {"urls": [], "titles": []}


def save_seen(data: dict[str, list[str]]) -> None:
    urls = sorted(set(data.get("urls", [])))[-3000:]
    titles = sorted(set(data.get("titles", [])))[-3000:]

    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump({"urls": urls, "titles": titles}, f, ensure_ascii=False, indent=2)

    logger.info(f"캐시 저장 완료 | urls={len(urls)}, titles={len(titles)}")


def update_seen(seen_data: dict[str, list[str]], articles: list[dict]) -> None:
    urls = set(seen_data.get("urls", []))
    titles = list(seen_data.get("titles", []))

    for article in articles:
        url = article.get("canonical_url", "").strip()
        title = article.get("normalized_title", "").strip()

        if url:
            urls.add(url)
        if title and not is_similar_title(title, titles):
            titles.append(title)

    seen_data["urls"] = list(urls)
    seen_data["titles"] = titles


# -----------------------------
# 비동기 데이터 수집 모듈
# -----------------------------
async def fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
) -> Any:
    try:
        async with session.get(url, headers=headers, params=params, timeout=HTTP_TIMEOUT_SECONDS) as response:
            if response.status == 200:
                return await response.json(content_type=None)

            body = await response.text()
            logger.warning(f"HTTP {response.status} | {url} | {body[:180]}")
            return None

    except asyncio.TimeoutError:
        logger.warning(f"요청 시간 초과 | {url}")
        return None
    except Exception as e:
        logger.warning(f"요청 실패 | {url} | {e}")
        return None


async def fetch_naver_news_async(session: aiohttp.ClientSession, query: str, semaphore: asyncio.Semaphore) -> List[Dict]:
    async with semaphore:
        url = "https://openapi.naver.com/v1/search/news.json"
        headers = {
            "X-Naver-Client-Id": NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
        }
        params = {"query": query, "display": PER_KEYWORD_DISPLAY, "sort": "date"}

        data = await fetch_json(session, url, headers=headers, params=params)
        items = []

        if not data:
            return items

        for item in data.get("items", []):
            title = strip_html(item.get("title", ""))
            link = (item.get("originallink") or item.get("link") or "").strip()
            pub_date = item.get("pubDate", "")

            if not title or not link:
                continue
            if not is_recent(pub_date):
                continue
            if is_globally_blocked(title, link):
                continue

            items.append({
                "title": title,
                "description": strip_html(item.get("description", "")),
                "url": link,
                "canonical_url": canonicalize_url(link),
                "domain": get_domain(link),
                "published_at": pub_date,
                "published_dt": parse_pub_date(pub_date),
                "normalized_title": normalize_title(title),
                "short_title": shorten_title(title),
                "matched_query": query,
                "source": "Naver",
            })

        return items


async def fetch_gnews_async(session: aiohttp.ClientSession, query: str, semaphore: asyncio.Semaphore) -> List[Dict]:
    async with semaphore:
        if not GNEWS_API_KEY:
            return []

        url = "https://gnews.io/api/v4/search"
        params = {"q": query, "token": GNEWS_API_KEY, "lang": "en", "max": 5}
        data = await fetch_json(session, url, params=params)
        items = []

        if not data:
            return items

        for item in data.get("articles", []):
            title = item.get("title", "")
            link = item.get("url", "")
            pub_date = item.get("publishedAt", "")

            if not title or not link:
                continue
            if pub_date and not is_recent(pub_date):
                continue
            if is_globally_blocked(title, link):
                continue

            items.append({
                "title": title,
                "description": item.get("description", ""),
                "url": link,
                "canonical_url": canonicalize_url(link),
                "domain": get_domain(link),
                "published_at": pub_date,
                "published_dt": parse_pub_date(pub_date),
                "normalized_title": normalize_title(title),
                "short_title": shorten_title(title),
                "matched_query": query,
                "source": "GNews",
            })

        return items


async def fetch_policy_briefing_async(session: aiohttp.ClientSession, query: str, semaphore: asyncio.Semaphore) -> List[Dict]:
    async with semaphore:
        if not GOV_API_KEY or not GOV_ENDPOINT:
            return []

        params = {
            "serviceKey": GOV_API_KEY,
            "searchWrd": query,
            "returnType": "json",
            "numOfRows": 5,
            "pageNo": 1,
        }

        data = await fetch_json(session, GOV_ENDPOINT, params=params)
        items = []

        if not data:
            return items

        raw_items = data.get("response", {}).get("body", {}).get("items", [])
        if isinstance(raw_items, dict):
            raw_items = [raw_items]

        for item in raw_items:
            title = item.get("title") or item.get("newsTitle", "")
            link = item.get("link") or item.get("newsId", "")
            pub_date = item.get("regDate", "")

            if not title or not link:
                continue

            url_str = link if str(link).startswith("http") else f"https://www.korea.kr/news/policyNewsView.do?newsId={link}"

            if pub_date and not is_recent(pub_date):
                continue
            if is_globally_blocked(title, url_str):
                continue

            items.append({
                "title": strip_html(title),
                "description": strip_html(str(item.get("contents", ""))[:200]),
                "url": url_str,
                "canonical_url": canonicalize_url(url_str),
                "domain": "korea.kr",
                "published_at": pub_date,
                "published_dt": parse_pub_date(pub_date),
                "normalized_title": normalize_title(title),
                "short_title": shorten_title(title),
                "matched_query": query,
                "source": "PolicyBriefing",
            })

        return items


def dedupe_candidate_pool(items: list[dict]) -> list[dict]:
    result = []
    seen_urls = set()
    seen_titles: List[str] = []

    items = sorted(
        items,
        key=lambda x: x.get("published_dt") or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )

    for item in items:
        url = item.get("canonical_url", "")
        title = item.get("normalized_title", "")

        if (url and url in seen_urls) or (title and is_similar_title(title, seen_titles)):
            continue

        if url:
            seen_urls.add(url)
        if title:
            seen_titles.append(title)

        result.append(item)

    return result


# -----------------------------
# 필터링 및 점수화
# -----------------------------
def score_article_for_topic(topic_name: str, article: dict, cfg: dict) -> int:
    title = normalize_text(article.get("title", ""))
    desc = normalize_text(article.get("description", ""))
    full = f"{title} {desc}".strip()

    if article.get("domain", "") in cfg.get("blocked_domains", set()):
        return -999

    score = domain_score(article.get("url", ""))

    if article.get("source") == "PolicyBriefing":
        score += 15
    if article.get("source") == "GNews":
        score += 5

    matched_query = normalize_text(article.get("matched_query", ""))
    if matched_query:
        if matched_query in title:
            score += 5
        elif matched_query in desc:
            score += 3

    for kw in cfg.get("positive_keywords", []):
        kw_norm = normalize_text(kw)
        if kw_norm in title:
            score += 4
        elif kw_norm in desc:
            score += 2

    for kw in cfg.get("negative_keywords", []):
        kw_norm = normalize_text(kw)
        if kw_norm in title:
            score -= 7
        elif kw_norm in desc:
            score -= 4

    topic_words = cfg.get("search_keywords", []) + cfg.get("positive_keywords", [])
    if not any(normalize_text(kw) in full for kw in topic_words):
        score -= 6

    return score


def pick_best_articles_for_topic(
    topic_name: str,
    candidates: list[dict],
    cfg: dict,
    seen_data: dict,
    used_urls: set,
    used_titles: list,
) -> list[dict]:
    seen_urls = set(seen_data.get("urls", []))
    seen_titles = list(seen_data.get("titles", []))
    scored = []

    for item in dedupe_candidate_pool(candidates):
        url = item.get("canonical_url", "")
        title = item.get("normalized_title", "")

        if (url and url in used_urls) or (title and is_similar_title(title, used_titles)):
            continue

        if not FORCE_TEST_MODE:
            if (url and url in seen_urls) or (title and is_similar_title(title, seen_titles)):
                continue

        score = score_article_for_topic(topic_name, item, cfg)
        item["topic_score"] = score

        if score >= cfg.get("min_score", 7):
            scored.append(item)

    scored.sort(
        key=lambda x: (
            x.get("topic_score", 0),
            x.get("published_dt") or datetime.min.replace(tzinfo=UTC),
        ),
        reverse=True,
    )

    return scored[:ARTICLES_PER_TOPIC]


# -----------------------------
# 요약 및 배송
# -----------------------------
def build_summary_prompt(topic_name: str, articles: list[dict]) -> str:
    article_blocks = [
        f"[기사 {i}]\n제목: {a['title']}\n설명: {a.get('description', '')}\n링크: {a['url']}\n"
        for i, a in enumerate(articles, 1)
    ]

    return f"""역할: 텔레그램 뉴스 브리핑 편집자
목표: {topic_name} 관련 기사 15~20초 분량 요약
문체: 한국어 브리핑체, 짧고 선명하게, 인삿말 금지.
형식:
한눈에
- 핵심 흐름 4줄

주요 이슈
1) 소제목
한 문장 설명
...
금지: '브리핑입니다', 장문 복붙, 링크 출력.
입력 기사:
{chr(10).join(article_blocks)}"""


def generate_with_model(model_name: str, prompt: str) -> str:
    client = get_genai_client()
    response = client.models.generate_content(model=model_name, contents=prompt)
    text = (getattr(response, "text", "") or "").strip()

    if not text:
        raise RuntimeError(f"{model_name} 응답 비어있음.")

    return text


async def summarize_topic_async(topic_name: str, articles: list[dict]) -> str:
    prompt = build_summary_prompt(topic_name, articles)
    loop = asyncio.get_running_loop()
    last_error = None

    for model_name in MODEL_CANDIDATES:
        for attempt in range(1, 3):
            try:
                logger.info(f"[{topic_name}] 요약 시도 | model={model_name}, attempt={attempt}")
                return await loop.run_in_executor(None, generate_with_model, model_name, prompt)

            except Exception as e:
                last_error = e
                logger.warning(f"[{topic_name}] 요약 실패 | model={model_name}, attempt={attempt}, error={e}")
                await asyncio.sleep(attempt + 0.5)

    raise last_error if last_error else RuntimeError("모델 호출 실패")


def build_links_section(articles: list[dict]) -> str:
    lines = ["", "원문"]
    for i, article in enumerate(articles[:4], 1):
        lines.append(f"{i}. {html.escape(article.get('short_title', ''))}")
        lines.append(article.get("url", ""))
    return "\n".join(lines)


async def send_telegram_async(session: aiohttp.ClientSession, text: str) -> None:
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    chunks = []
    remain = text.strip()

    while remain:
        if len(remain) <= TELEGRAM_SAFE_LIMIT:
            chunks.append(remain)
            break

        cut = remain.rfind("\n", 0, TELEGRAM_SAFE_LIMIT)
        if cut == -1 or cut < (TELEGRAM_SAFE_LIMIT // 2):
            cut = TELEGRAM_SAFE_LIMIT

        chunks.append(remain[:cut].strip())
        remain = remain[cut:].strip()

    for idx, chunk in enumerate(chunks, 1):
        try:
            async with session.post(
                api_url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=HTTP_TIMEOUT_SECONDS,
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    logger.error(f"텔레그램 전송 실패 | chunk={idx}/{len(chunks)} | HTTP {response.status} | {body[:300]}")
                else:
                    logger.info(f"텔레그램 전송 완료 | chunk={idx}/{len(chunks)}")

        except Exception as e:
            logger.exception(f"텔레그램 전송 예외 | chunk={idx}/{len(chunks)} | {e}")


def build_telegram_message(topic_name: str, summary: str, articles: list[dict]) -> str:
    links_section = build_links_section(articles)
    message_time = fmt_kst("%m-%d %H:%M")

    logger.info(f"[{topic_name}] 텔레그램 메시지 조립 시간 확인: {message_time} KST")

    return (
        f"📰 <b>{html.escape(topic_name)}</b>\n"
        f"{message_time}\n\n"
        f"{html.escape(summary)}"
        f"{links_section}"
    )


# -----------------------------
# 메인 비동기 워크플로우
# -----------------------------
async def collect_topic_candidates(session: aiohttp.ClientSession, topic_name: str, cfg: dict, semaphore: asyncio.Semaphore) -> list[dict]:
    logger.info(f"[{topic_name}] 수집 시작")

    tasks = []
    for q in cfg["search_keywords"]:
        tasks.append(fetch_naver_news_async(session, q, semaphore))
        tasks.append(fetch_gnews_async(session, q, semaphore))
        tasks.append(fetch_policy_briefing_async(session, q, semaphore))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    candidates = []
    source_counts = {"Naver": 0, "GNews": 0, "PolicyBriefing": 0}

    for result in results:
        if isinstance(result, Exception):
            logger.warning(f"[{topic_name}] 수집 태스크 실패: {result}")
            continue

        for item in result:
            candidates.append(item)
            source = item.get("source", "Unknown")
            source_counts[source] = source_counts.get(source, 0) + 1

    logger.info(
        f"[{topic_name}] 수집완료 - "
        f"Naver:{source_counts.get('Naver', 0)}, "
        f"GNews:{source_counts.get('GNews', 0)}, "
        f"Policy:{source_counts.get('PolicyBriefing', 0)}, "
        f"Total:{len(candidates)}"
    )

    return candidates


async def run_topic_async(
    session: aiohttp.ClientSession,
    topic_name: str,
    cfg: dict,
    seen_data: dict,
    used_urls: set,
    used_titles: list,
    semaphore: asyncio.Semaphore,
) -> bool:
    candidates = await collect_topic_candidates(session, topic_name, cfg, semaphore)

    fresh_articles = pick_best_articles_for_topic(
        topic_name=topic_name,
        candidates=candidates,
        cfg=cfg,
        seen_data=seen_data,
        used_urls=used_urls,
        used_titles=used_titles,
    )

    if not fresh_articles:
        logger.info(f"[{topic_name}] 전송할 새 기사 없음")
        return False

    logger.info(
        f"[{topic_name}] 선정 기사: "
        + " / ".join([f"{a.get('short_title')}({a.get('topic_score')})" for a in fresh_articles])
    )

    summary = await summarize_topic_async(topic_name, fresh_articles)
    message = build_telegram_message(topic_name, summary, fresh_articles)

    await send_telegram_async(session, message)
    save_to_google_sheet(topic_name, summary, fresh_articles)

    for article in fresh_articles:
        url = article.get("canonical_url")
        title = article.get("normalized_title")

        if url:
            used_urls.add(url)
        if title and not is_similar_title(title, used_titles):
            used_titles.append(title)

    if not FORCE_TEST_MODE:
        update_seen(seen_data, fresh_articles)

    return True


async def main_async() -> None:
    require_env()

    logger.info("=" * 60)
    logger.info("뉴스봇 시작")
    logger.info(f"UTC 현재시각: {now_utc().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    logger.info(f"KST 현재시각: {now_kst().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    logger.info("=" * 60)

    seen_data = load_seen()
    used_urls: set = set()
    used_titles: List[str] = []
    semaphore = asyncio.Semaphore(HTTP_CONCURRENCY)

    timeout = aiohttp.ClientTimeout(total=45)
    headers = {
        "User-Agent": "news-bot/1.0 (+https://github.com)",
    }

    sent_count = 0

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        # Gemini 호출이 동시에 몰리면 503/쿼터 문제가 나기 쉬워서 주제별로 순차 실행한다.
        # 수집 자체는 각 주제 안에서 비동기로 병렬 처리된다.
        for topic_name, cfg in TOPIC_CONFIGS.items():
            try:
                sent = await run_topic_async(
                    session=session,
                    topic_name=topic_name,
                    cfg=cfg,
                    seen_data=seen_data,
                    used_urls=used_urls,
                    used_titles=used_titles,
                    semaphore=semaphore,
                )
                if sent:
                    sent_count += 1

            except Exception as e:
                logger.exception(f"[{topic_name}] 처리 중 오류: {e}")

    save_seen(seen_data)

    logger.info(f"전체 완료 | 전송된 분야 수: {sent_count}")
    print(f"전체 완료 | 전송된 분야 수: {sent_count}")


if __name__ == "__main__":
    asyncio.run(main_async())
