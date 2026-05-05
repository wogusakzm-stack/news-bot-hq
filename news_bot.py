import os
import re
import json
import time
import html
import logging
import asyncio
import aiohttp
import gspread
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, List, Dict, Set
from difflib import SequenceMatcher
from urllib.parse import urlparse, urlsplit, urlunsplit, parse_qsl, urlencode

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

# 구글 시트 연동을 위한 환경 변수 (GitHub Secrets에서 가져옴)
GOOGLE_SHEETS_JSON = os.getenv("GOOGLE_SHEETS_JSON")
SHEET_ID = os.getenv("SHEET_ID")

SEEN_FILE = "seen_urls.json"

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
    "thelec.kr": 1, "biz.chosun.com": 1,
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

client = genai.Client(api_key=GEMINI_API_KEY)
logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s | %(levelname)s | %(message)s", 
    force=True,
    handlers=[logging.StreamHandler(), logging.FileHandler("news_bot.log", encoding="utf-8")]
)
logger = logging.getLogger(__name__)

semaphore = asyncio.Semaphore(5)

# -----------------------------
# 공통 유틸리티
# -----------------------------
def require_env() -> None:
    missing = []
    values = {
        "NAVER_CLIENT_ID": NAVER_CLIENT_ID, "NAVER_CLIENT_SECRET": NAVER_CLIENT_SECRET,
        "GEMINI_API_KEY": GEMINI_API_KEY, "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
    }
    for key, value in values.items():
        if not value: missing.append(key)
    if missing:
        raise RuntimeError(f"환경변수 누락: {', '.join(missing)}")

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

def parse_pub_date(pub_date: str):
    if not pub_date: return None
    try:
        dt = parsedate_to_datetime(pub_date)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def is_recent(pub_date: str, max_days: int = MAX_ARTICLE_AGE_DAYS) -> bool:
    dt = parse_pub_date(pub_date)
    return (datetime.now(timezone.utc) - dt).days <= max_days if dt else False

def get_domain(url: str) -> str:
    try: return urlparse(url).netloc.lower().replace("www.", "")
    except Exception: return ""

def canonicalize_url(url: str) -> str:
    try:
        split = urlsplit(url)
        keep_keys = {"news_id", "articleid", "idxno", "idx", "id", "wr_id", "news_no", "no", "article_id", "pnttsn"}
        query_items = [(k, v) for k, v in parse_qsl(split.query, keep_blank_values=True) if k.lower() in keep_keys]
        return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query_items), ""))
    except Exception: return url

def domain_score(url: str) -> int:
    return TRUSTED_DOMAIN_SCORES.get(get_domain(url), 0)

def is_globally_blocked(title: str, url: str) -> bool:
    if get_domain(url) in GLOBAL_BLOCKED_DOMAINS: return True
    norm_title = normalize_text(title)
    return any(kw.lower() in norm_title for kw in GLOBAL_BLOCKED_TITLE_KEYWORDS)

# -----------------------------
# 구글 시트 저장 유틸리티
# -----------------------------
def save_to_google_sheet(topic_name: str, summary: str, articles: list[dict]):
    """수집된 요약본을 구글 시트에 기록합니다."""
    try:
        if not GOOGLE_SHEETS_JSON or not SHEET_ID:
            logger.warning("구글 시트 환경 변수 누락. 시트 저장을 건너뜁니다.")
            return
        
        creds_dict = json.loads(GOOGLE_SHEETS_JSON)
        gc = gspread.service_account_from_dict(creds_dict)
        sh = gc.open_by_key(SHEET_ID)
        worksheet = sh.get_worksheet(0)
        
        # 한국 시간(KST) 기록 - 강제 연산 공식
        now_str = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")
        
        # 기사 제목들 합치기 (분석용)
        titles = " | ".join([a.get('title', '') for a in articles])
        
        # 새 행으로 추가: [기록시간, 분야, 요약내용, 참고기사제목들]
        worksheet.append_row([now_str, topic_name, summary, titles])
        logger.info(f"[{topic_name}] 구글 시트 저장 완료! (๑>ᴗ<๑)")
        
    except Exception as e:
        logger.error(f"구글 시트 저장 실패: {e}")

# -----------------------------
# 파일 캐시 처리
# -----------------------------
def load_seen() -> dict[str, list[str]]:
    if not os.path.exists(SEEN_FILE): return {"urls": [], "titles": []}
    with open(SEEN_FILE, "r", encoding="utf-8") as f: data = json.load(f)
    if isinstance(data, list): return {"urls": [x for x in data if isinstance(x, str)], "titles": []}
    if isinstance(data, dict):
        urls, titles = data.get("urls", []), data.get("titles", [])
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

def update_seen(seen_data: dict[str, list[str]], articles: list[dict]) -> None:
    urls, titles = set(seen_data.get("urls", [])), list(seen_data.get("titles", []))
    for article in articles:
        url, title = article.get("canonical_url", "").strip(), article.get("normalized_title", "").strip()
        if url: urls.add(url)
        if title and not is_similar_title(title, titles): titles.append(title)
    seen_data["urls"], seen_data["titles"] = list(urls), titles

# -----------------------------
# 비동기 데이터 수집 모듈
# -----------------------------
async def fetch_json(session: aiohttp.ClientSession, url: str, headers: dict = None, params: dict = None) -> Any:
    async with semaphore:
        try:
            async with session.get(url, headers=headers, params=params, timeout=10) as response:
                if response.status == 200: return await response.json()
        except Exception as e:
            logger.error(f"요청 에러 {url}: {e}")
        return None

async def fetch_naver_news_async(session: aiohttp.ClientSession, query: str) -> List[Dict]:
    url = "https://openapi.naver.com/v1/search/news.json"
    headers = {"X-Naver-Client-Id": NAVER_CLIENT_ID, "X-Naver-Client-Secret": NAVER_CLIENT_SECRET}
    params = {"query": query, "display": PER_KEYWORD_DISPLAY, "sort": "date"}
    data = await fetch_json(session, url, headers=headers, params=params)
    items = []
    if data:
        for item in data.get("items", []):
            title = strip_html(item.get("title", ""))
            link = (item.get("originallink") or item.get("link") or "").strip()
            pub_date = item.get("pubDate", "")
            if not title or not link or not is_recent(pub_date) or is_globally_blocked(title, link): continue
            items.append({
                "title": title, "description": strip_html(item.get("description", "")),
                "url": link, "canonical_url": canonicalize_url(link), "domain": get_domain(link),
                "published_at": pub_date, "published_dt": parse_pub_date(pub_date),
                "normalized_title": normalize_title(title), "short_title": shorten_title(title),
                "matched_query": query, "source": "Naver"
            })
    return items

async def fetch_gnews_async(session: aiohttp.ClientSession, query: str) -> List[Dict]:
    if not GNEWS_API_KEY: return []
    url = "https://gnews.io/api/v4/search"
    params = {"q": query, "token": GNEWS_API_KEY, "lang": "en", "max": 5}
    data = await fetch_json(session, url, params=params)
    items = []
    if data:
        for item in data.get("articles", []):
            title = item.get("title", "")
            link = item.get("url", "")
            if not title or not link: continue
            items.append({
                "title": title, "description": item.get("description", ""),
                "url": link, "canonical_url": canonicalize_url(link), "domain": get_domain(link),
                "published_at": item.get("publishedAt", ""), "published_dt": parse_pub_date(item.get("publishedAt", "")),
                "normalized_title": normalize_title(title), "short_title": shorten_title(title),
                "matched_query": query, "source": "GNews"
            })
    return items

async def fetch_policy_briefing_async(session: aiohttp.ClientSession, query: str) -> List[Dict]:
    if not GOV_API_KEY or not GOV_ENDPOINT: return []
    params = {"serviceKey": GOV_API_KEY, "searchWrd": query, "returnType": "json", "numOfRows": 5, "pageNo": 1}
    data = await fetch_json(session, GOV_ENDPOINT, params=params)
    items = []
    if data:
        raw_items = data.get("response", {}).get("body", {}).get("items", [])
        if isinstance(raw_items, dict): raw_items = [raw_items]
        for item in raw_items:
            title = item.get("title") or item.get("newsTitle", "")
            link = item.get("link") or item.get("newsId", "")
            if not title or not link: continue
            url_str = link if link.startswith("http") else f"https://www.korea.kr/news/policyNewsView.do?newsId={link}"
            items.append({
                "title": strip_html(title), "description": strip_html(item.get("contents", "")[:200]),
                "url": url_str, "canonical_url": canonicalize_url(url_str), "domain": "korea.kr",
                "published_at": item.get("regDate", ""), "published_dt": parse_pub_date(item.get("regDate", "")),
                "normalized_title": normalize_title(title), "short_title": shorten_title(title),
                "matched_query": query, "source": "PolicyBriefing"
            })
    return items

def dedupe_candidate_pool(items: list[dict]) -> list[dict]:
    result, seen_urls, seen_titles = [], set(), []
    items = sorted(items, key=lambda x: x.get("published_dt") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    for item in items:
        url, title = item.get("canonical_url", ""), item.get("normalized_title", "")
        if (url and url in seen_urls) or (title and is_similar_title(title, seen_titles)): continue
        if url: seen_urls.add(url)
        if title: seen_titles.append(title)
        result.append(item)
    return result

# -----------------------------
# 필터링 및 점수화
# -----------------------------
def score_article_for_topic(topic_name: str, article: dict, cfg: dict) -> int:
    title, desc = normalize_text(article.get("title", "")), normalize_text(article.get("description", ""))
    full = f"{title} {desc}".strip()
    if article.get("domain", "") in cfg.get("blocked_domains", set()): return -999

    score = domain_score(article.get("url", ""))
    
    if article.get("source") == "PolicyBriefing": score += 15 
    if article.get("source") == "GNews": score += 5

    matched_query = normalize_text(article.get("matched_query", ""))
    if matched_query:
        if matched_query in title: score += 5
        elif matched_query in desc: score += 3

    for kw in cfg.get("positive_keywords", []):
        kw_norm = normalize_text(kw)
        if kw_norm in title: score += 4
        elif kw_norm in desc: score += 2

    for kw in cfg.get("negative_keywords", []):
        kw_norm = normalize_text(kw)
        if kw_norm in title: score -= 7
        elif kw_norm in desc: score -= 4

    if not any(normalize_text(kw) in full for kw in cfg.get("search_keywords", []) + cfg.get("positive_keywords", [])):
        score -= 6
    return score

def pick_best_articles_for_topic(topic_name: str, candidates: list[dict], cfg: dict, seen_data: dict, used_urls: set, used_titles: list) -> list[dict]:
    seen_urls, seen_titles = set(seen_data.get("urls", [])), list(seen_data.get("titles", []))
    scored = []
    for item in dedupe_candidate_pool(candidates):
        url, title = item.get("canonical_url", ""), item.get("normalized_title", "")
        if (url and url in used_urls) or (title and is_similar_title(title, used_titles)): continue
        if not FORCE_TEST_MODE and ((url and url in seen_urls) or (title and is_similar_title(title, seen_titles))): continue
        
        score = score_article_for_topic(topic_name, item, cfg)
        item["topic_score"] = score
        if score >= cfg.get("min_score", 7): scored.append(item)
    
    scored.sort(key=lambda x: (x.get("topic_score", 0), x.get("published_dt") or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return scored[:ARTICLES_PER_TOPIC]

# -----------------------------
# 요약 및 배송
# -----------------------------
def build_summary_prompt(topic_name: str, articles: list[dict]) -> str:
    article_blocks = [f"[기사 {i}]\n제목: {a['title']}\n설명: {a['description']}\n링크: {a['url']}\n" for i, a in enumerate(articles, 1)]
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
입력 기사:\n{chr(10).join(article_blocks)}"""

def generate_with_model(model_name: str, prompt: str) -> str:
    text = (client.models.generate_content(model=model_name, contents=prompt).text or "").strip()
    if not text: raise RuntimeError(f"{model_name} 응답 비어있음.")
    return text

async def summarize_topic_async(topic_name: str, articles: list[dict]) -> str:
    prompt = build_summary_prompt(topic_name, articles)
    loop = asyncio.get_event_loop()
    last_error = None
    for model_name in MODEL_CANDIDATES:
        for attempt in range(1, 3):
            try:
                return await loop.run_in_executor(None, generate_with_model, model_name, prompt)
            except Exception as e:
                last_error = e
                await asyncio.sleep(attempt + 0.5)
    raise last_error if last_error else RuntimeError("모델 호출 실패")

async def send_telegram_async(session: aiohttp.ClientSession, text: str) -> None:
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks, remain = [], text.strip()
    while remain:
        if len(remain) <= TELEGRAM_SAFE_LIMIT: chunks.append(remain); break
        cut = remain.rfind("\n", 0, TELEGRAM_SAFE_LIMIT)
        if cut == -1 or cut < (TELEGRAM_SAFE_LIMIT // 2): cut = TELEGRAM_SAFE_LIMIT
        chunks.append(remain[:cut].strip()); remain = remain[cut:].strip()
    
    for chunk in chunks:
        try:
            await session.post(api_url, data={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=10)
        except Exception as e:
            logger.error(f"텔레그램 전송 실패: {e}")

# -----------------------------
# 메인 비동기 워크플로우
# -----------------------------
async def run_topic_async(session, topic_name, cfg, seen_data, used_urls, used_titles) -> bool:
    logger.info(f"[{topic_name}] 수집 시작")
    tasks = []
    for q in cfg["search_keywords"]:
        tasks.append(asyncio.create_task(fetch_naver_news_async(session, q)))
        tasks.append(asyncio.create_task(fetch_gnews_async(session, q)))
        tasks.append(asyncio.create_task(fetch_policy_briefing_async(session, q)))
    
    done, pending = await asyncio.wait(tasks)
    results = [task.result() for task in done if not task.exception()]
    
    naver_count = sum(len(r) for r in results if r and r[0].get("source") == "Naver")
    gnews_count = sum(len(r) for r in results if r and r[0].get("source") == "GNews")
    gov_count = sum(len(r) for r in results if r and r[0].get("source") == "PolicyBriefing")
    logger.info(f"[{topic_name}] 수집완료 - Naver:{naver_count}, GNews:{gnews_count}, Policy:{gov_count}")

    candidates = [item for sublist in results for item in sublist]
    
    fresh_articles = pick_best_articles_for_topic(topic_name, candidates, cfg, seen_data, used_urls, used_titles)
    if not fresh_articles: 
        logger.info(f"[{topic_name}] 전송할 새 기사 없음")
        return False

    summary = await summarize_topic_async(topic_name, fresh_articles)
    links_section = "\n원문\n" + "\n".join([f"{i}. {html.escape(a['short_title'])}\n{a['url']}\n" for i, a in enumerate(fresh_articles[:4], 1)])
    
    # 한국 시간(KST) 기록 - 강제 연산 공식 (텔레그램용)
    now_kst = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%m-%d %H:%M")
    
    message = f"📰 <b>{html.escape(topic_name)}</b>\n{now_kst}\n\n{html.escape(summary)}\n{links_section}"
    
    # 텔레그램 배송
    await send_telegram_async(session, message)
    
    # 구글 시트에 데이터 기록 (기억의 도서관!)
    save_to_google_sheet(topic_name, summary, fresh_articles)
    
    for article in fresh_articles:
        if u := article.get("canonical_url"): used_urls.add(u)
        if t := article.get("normalized_title"):
            if not is_similar_title(t, used_titles): used_titles.append(t)
    if not FORCE_TEST_MODE: update_seen(seen_data, fresh_articles)
    return True

async def main_async() -> None:
    require_env()
    seen_data = load_seen()
    used_urls, used_titles = set(), []
    
    async with aiohttp.ClientSession() as session:
        topic_tasks = [asyncio.create_task(run_topic_async(session, name, cfg, seen_data, used_urls, used_titles)) for name, cfg in TOPIC_CONFIGS.items()]
        done, pending = await asyncio.wait(topic_tasks)
        
        sent_count = 0
        for task in done:
            if task.exception():
                logger.exception(f"오류: {task.exception()}")
            elif task.result() is True:
                sent_count += 1

    save_seen(seen_data)
    print(f"전체 완료 | 전송된 분야 수: {sent_count}")

if __name__ == "__main__":
    asyncio.run(main_async())
