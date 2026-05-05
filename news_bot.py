import os
import re
import json
import html
import logging
import asyncio
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, List, Dict, Optional, Tuple
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

# API 과호출 방지 설정
HTTP_TIMEOUT_SECONDS = 12
HTTP_CONCURRENCY = 3

NAVER_KEYWORD_LIMIT = 10

# GNews는 무료 한도가 작으므로 세계/IT에서만 핵심 키워드 2개 호출
GNEWS_TOPICS = {"세계", "IT·과학"}
GNEWS_KEYWORD_LIMIT = 2

# 정책브리핑 API는 검색어 API가 아니라 날짜 목록 API라서 분야별 1회만 호출 후 로컬 필터
POLICY_TOPICS = {"경제", "정치", "사회·생활문화"}
POLICY_RESULT_LIMIT = 8

# 한 분야가 한 언론사로 도배되는 것을 방지
MAX_DOMAIN_PER_TOPIC = 2

GLOBAL_BLOCKED_DOMAINS = {
    "breaknews.com",
    "lecturernews.com",
    "tongilnews.com",
    "newstown.co.kr",
}

GLOBAL_BLOCKED_TITLE_KEYWORDS = [
    "기고", "칼럼", "사설", "오피니언", "특강", "강연", "저자를 만나다",
    "시평", "진단", "기자수첩", "데스크", "취재수첩", "만평",
    "인터뷰", "대담", "해설", "논평", "寄稿", "아침을 열며", "기자의 눈",
    "오늘과 내일", "세상읽기", "시론", "특별기고", "전문가 칼럼", "기획칼럼", "광장", "여론",
    "NZSI INDEX", "오늘의 경제뉴스", "관전기", "딴지일보", "논객", "의견", "주장",
]

TRUSTED_DOMAIN_SCORES = {
    "yna.co.kr": 3,
    "news1.kr": 3,
    "sbs.co.kr": 3,
    "kbs.co.kr": 3,
    "imbc.com": 3,
    "ytn.co.kr": 3,
    "mk.co.kr": 2,
    "hankyung.com": 2,
    "sedaily.com": 2,
    "edaily.co.kr": 2,
    "newsis.com": 2,
    "joongang.co.kr": 2,
    "chosun.com": 2,
    "donga.com": 2,
    "khan.co.kr": 2,
    "fnnews.com": 2,
    "etnews.com": 2,
    "zdnet.co.kr": 2,
    "ddaily.co.kr": 2,
    "bloter.net": 1,
    "thelec.kr": 1,
    "biz.chosun.com": 1,
    "korea.kr": 3,
}

# 신뢰도/대표성이 약하거나 특정 커뮤니티성·저품질 기사로 자주 잡히는 도메인 감점
DOMAIN_PENALTIES = {
    "sjsori.com": -8,
    "choicenews.co.kr": -3,
    "bntnews.co.kr": -3,
    "tokenpost.kr": -2,
    "ddanzi.com": -8,
}

# 같은 사건이 제목만 바뀌어 여러 번 뽑히는 문제를 줄이기 위한 스토리 클러스터
# min_hits 이상 단어가 맞으면 같은 사건으로 보고, 한 클러스터에서는 기본적으로 1개만 선택한다.
STORY_CLUSTERS = {
    "경제": [
        {"id": "korea_stock_index", "terms": ["코스피", "코스닥", "7000", "6900", "증시", "NZSI INDEX"], "min_hits": 2, "max_items": 1},
        {"id": "us_rate_oil", "terms": ["트럼프", "금리", "인하", "유가", "원유", "인플레이션"], "min_hits": 2, "max_items": 1},
    ],
    "정치": [
        {"id": "han_donghoon_special", "terms": ["한동훈", "특검", "출국금지", "선거개입", "수원지검"], "min_hits": 2, "max_items": 1},
        {"id": "busan_bukgap_unification", "terms": ["부산 북갑", "하정우", "박민식", "한동훈", "단일화"], "min_hits": 2, "max_items": 1},
        {"id": "education_superintendent", "terms": ["교육감", "예비후보", "단일화", "충북"], "min_hits": 2, "max_items": 1},
    ],
    "사회·생활문화": [
        {"id": "youth_savings", "terms": ["청년내일저축계좌", "청년", "저축", "신청"], "min_hits": 2, "max_items": 1},
        {"id": "labor_union_reform", "terms": ["노조", "노동", "교섭", "조례", "시민 보호"], "min_hits": 2, "max_items": 1},
        {"id": "court_patient_safety", "terms": ["환자", "병원", "추락", "책임", "판결"], "min_hits": 2, "max_items": 1},
    ],
    "세계": [
        {"id": "hormuz_korean_ship", "terms": ["호르무즈", "한국 선박", "한국 화물선", "韓 선박", "트럼프", "이란", "대열", "피격", "박살"], "min_hits": 2, "max_items": 1},
        {"id": "us_iran_pressure", "terms": ["미국", "이란", "휴전", "압박", "국방", "전쟁"], "min_hits": 2, "max_items": 1},
        {"id": "russia_ukraine_truce", "terms": ["러시아", "우크라이나", "휴전", "전승절"], "min_hits": 2, "max_items": 1},
        {"id": "south_china_sea", "terms": ["남중국해", "필리핀", "중국", "모래톱", "자국기"], "min_hits": 2, "max_items": 1},
    ],
    "IT·과학": [
        {"id": "openai_musk", "terms": ["오픈AI", "머스크", "법정", "지분", "비영리"], "min_hits": 2, "max_items": 1},
        {"id": "ai_chip_supply", "terms": ["AI 칩", "엔비디아", "삼성", "TSMC", "셀레브라스", "GPU"], "min_hits": 2, "max_items": 1},
        {"id": "ai_model_policy", "terms": ["AI 모델", "모델 테스트", "사이버보안", "백악관", "주정부"], "min_hits": 2, "max_items": 1},
    ],
}

# -----------------------------
# 주제별 키워드 교차검증 설정
# -----------------------------
# 설계 원칙:
# 1) search_keywords: API 검색용. 넓게 잡되 과호출 방지를 위해 상위 일부만 사용.
# 2) core_keywords: 이 주제라고 판단할 최소 핵심어. 최소 1개 이상 필요.
# 3) positive_keywords/crosscheck_keywords: 점수 보강 및 교차검증용.
# 4) negative_keywords: 점수 감점용.
# 5) hard_negative_keywords: 발견 시 해당 주제에서 즉시 제외.
#    예: "우주 아빠" 야구 기사 → IT·과학 hard_negative의 이닝/투수/야구로 제외.
TOPIC_CONFIGS = {
    "경제": {
        "search_keywords": [
            "증시", "환율", "금리", "물가", "부동산", "고용", "수출",
            "한국은행", "연준", "원달러", "stock market", "interest rate",
        ],
        "core_keywords": [
            "경제", "증시", "코스피", "코스닥", "환율", "원달러", "금리",
            "기준금리", "국채", "채권", "물가", "소비자물가", "인플레이션",
            "부동산", "집값", "분양", "고용", "실업", "수출", "수입",
            "무역", "경상수지", "유가", "원유", "한국은행", "연준",
        ],
        "positive_keywords": [
            "시장", "금융", "은행", "채권시장", "외환시장", "투자", "외국인",
            "기관", "순매수", "경기침체", "경기", "성장률", "소비", "세수",
            "관세", "무역수지", "분양가", "대출", "부채", "가계대출",
        ],
        "crosscheck_keywords": [
            "상승", "하락", "급등", "급락", "변동성", "마감", "전망",
            "압박", "둔화", "회복", "침체", "인하", "인상", "동결",
        ],
        "negative_keywords": [
            "국회", "대통령실", "여야", "외교", "강연", "특강", "교수",
            "포럼", "전시", "공연", "수목원",
            "NZSI INDEX", "오늘의 경제뉴스", "신청 방법", "극저신용대출",
            "복지 서비스", "지원 정책",
        ],
        "hard_negative_keywords": [
            "대선", "총선", "공천", "경선", "후보", "지지율", "오세훈",
            "한동훈", "이재명", "장동혁", "특검", "선거개입", "선수",
            "야구", "축구", "농구", "이닝", "홈런", "투수", "도핑",
        ],
        "blocked_domains": set(),
        "min_score": 8,
        "min_keyword_hits": 2,
    },
    "정치": {
        "search_keywords": [
            "국회", "대통령실", "여야", "정당", "총리", "장관",
            "대선", "총선", "정치권", "청문회",
        ],
        "core_keywords": [
            "국회", "대통령", "대통령실", "정부", "총리", "장관", "여야",
            "민주당", "국민의힘", "개혁신당", "조국혁신당", "정당",
            "당대표", "원내대표", "법안", "의결", "개각", "청문회",
            "특검", "선관위", "공천", "후보", "선거", "대선", "총선",
            "지방선거", "지지율", "정치권",
        ],
        "positive_keywords": [
            "표결", "발의", "정책", "공약", "출마", "단일화", "탈당",
            "입당", "수사", "탄핵", "권력", "인사", "외교안보",
            "서울", "부산", "광주", "인천", "대구", "대전", "울산",
            "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남",
        ],
        "crosscheck_keywords": [
            "논란", "반발", "합의", "갈등", "요구", "비판", "입장",
            "발표", "추진", "검토", "처리", "통과",
        ],
        "negative_keywords": [
            "증시", "환율", "금리", "물가", "부동산", "비트코인",
            "강연", "특강", "교수", "미디어학부", "수목원", "공연",
            "전시", "스마트팩토리", "국회 관전기", "딴지일보",
        ],
        "hard_negative_keywords": [
            "루마니아", "인디애나", "미국 공화당", "미국 민주당",
            "미 대선", "미국 경선", "유럽 연정", "트럼프 경선",
            "선수", "야구", "축구", "농구", "이닝", "홈런", "도핑",
        ],
        "blocked_domains": set(),
        "min_score": 8,
        "min_keyword_hits": 2,
    },
    "사회·생활문화": {
        "search_keywords": [
            "사건", "사고", "법원", "검찰", "경찰", "교육", "건강",
            "복지", "노동", "산재", "재난", "화재", "교통사고", "보건",
        ],
        "core_keywords": [
            "사건", "사고", "법원", "대법원", "검찰", "경찰", "수사",
            "기소", "판결", "재판", "구속", "피해", "사망", "부상",
            "병원", "보건", "의료", "건강", "복지", "교육", "학교",
            "노동", "노조", "산재", "재난", "화재", "교통사고", "범죄",
        ],
        "positive_keywords": [
            "안전", "입원", "환자", "장애", "아동", "청년", "노인",
            "돌봄", "지원", "조례", "제도", "시민", "생활", "주거",
            "노동자", "사업장", "위험", "예방", "공공", "센터",
        ],
        "crosscheck_keywords": [
            "발생", "적발", "조사", "제기", "추진", "지원", "개선",
            "강화", "확대", "논의", "대응", "보호",
        ],
        "negative_keywords": [
            "증시", "환율", "금리", "물가", "부동산", "비트코인",
            "연준", "외교", "제재", "AI 모델", "오픈AI", "엔비디아", "장관상",
            "수상", "표창", "공로", "원장", "협약식", "기념식", "러시아산 원유",
            "윤석열", "김건희", "전 대통령", "대통령 부부", "특검", "정치수사",
            "메릴랜드", "뉴욕시", "뉴욕", "미국", "주정부", "퍼플 스타 스쿨",
            "마일", "달러", "카운티",
        ],
        "hard_negative_keywords": [
            "어린이날", "대축제", "문화제", "축사", "개최", "참여",
            "페스티벌", "박람회", "공연", "전시", "가요", "아이돌",
            "후보", "공약", "시장 후보", "출마 선언", "선거운동",
            "윤석열", "김건희", "대통령 부부", "정치수사",
            "야구", "축구", "농구", "이닝", "홈런", "투수", "타자",
        ],
        "blocked_domains": set(),
        "min_score": 8,
        "min_keyword_hits": 2,
    },
    "세계": {
        "search_keywords": [
            "미국", "중국", "일본", "유럽", "중동", "우크라이나",
            "러시아", "EU", "대만", "국제", "world politics",
        ],
        "core_keywords": [
            "미국", "중국", "일본", "유럽", "중동", "우크라이나", "러시아",
            "EU", "대만", "북한", "이란", "이스라엘", "가자", "나토",
            "백악관", "트럼프", "바이든", "시진핑", "푸틴", "정상회담",
            "외교", "제재", "전쟁", "휴전", "분쟁", "국경", "국제사회",
        ],
        "positive_keywords": [
            "외신", "안보리", "유엔", "협상", "압박", "군사", "핵",
            "미사일", "관세", "동맹", "무역분쟁", "호르무즈", "원유",
            "공습", "회담", "대사", "정부", "의회",
        ],
        "crosscheck_keywords": [
            "발언", "경고", "합의", "갈등", "긴장", "압박", "대응",
            "봉쇄", "공격", "철수", "파병", "제안", "비판",
        ],
        "negative_keywords": [
            "증시", "환율", "금리", "물가", "부동산", "비트코인",
            "주가", "휘발윳값", "강연", "특강", "교수", "수목원",
            "공연", "전시", "스포츠 투자", "축구단", "골프", "F1",
            "월드컵 유치", "구단",
        ],
        "hard_negative_keywords": [
            "박람회", "원예", "치유", "태안", "축제", "행사", "기업",
            "수목원", "어린이날", "문화제", "야구", "축구", "농구",
            "이닝", "홈런", "투수", "선수", "도핑",
        ],
        "blocked_domains": set(),
        "min_score": 9,
        "min_keyword_hits": 2,
    },
    "IT·과학": {
        "search_keywords": [
            "AI", "인공지능", "반도체", "오픈AI", "엔비디아", "로봇",
            "양자", "우주", "테크", "artificial intelligence", "semiconductor",
        ],
        "core_keywords": [
            "AI", "인공지능", "생성형 AI", "챗GPT", "오픈AI", "LLM",
            "모델", "데이터센터", "GPU", "엔비디아", "반도체", "파운드리",
            "삼성전자", "SK하이닉스", "로봇", "로보틱스", "자율주행",
            "양자", "우주", "위성", "항공우주", "과학", "연구", "기술",
            "바이오", "의료 AI", "UNIST", "KAIST",
        ],
        "positive_keywords": [
            "개발", "연구진", "논문", "상용화", "출시", "도입", "성능",
            "알고리즘", "칩", "서버", "클라우드", "빅테크", "보안",
            "데이터", "소프트웨어", "하드웨어", "스타트업", "투자 유치",
        ],
        "crosscheck_keywords": [
            "자동화", "학습", "추론", "설계", "검증", "실험", "분석",
            "플랫폼", "서비스", "시스템", "공개", "발표", "협력",
        ],
        "negative_keywords": [
            "총선", "대선", "국회", "여야", "외교", "수목원", "전시",
            "공연", "저자를 만나다", "특강", "기고", "칼럼", "정치",
            "증시", "주가", "코스피", "코스닥", "파업", "임금", "노조",
            "실적", "영업이익", "매출", "투자자", "하락 전환",
            "특구 유치", "메가특구", "춘천시", "지역전략", "지자체",
        ],
        "hard_negative_keywords": [
            "야구", "축구", "농구", "배구", "골프", "투수", "타자",
            "이닝", "홈런", "선발", "구단", "감독", "선수", "리그",
            "시즌", "타율", "방어율", "우주 아빠", "도핑",
            "노사", "파업", "임금협상", "주가", "증시", "코스피", "코스닥",
        ],
        "blocked_domains": set(),
        "min_score": 9,
        "min_keyword_hits": 2,
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


def compact_text(text: str, limit: int = 900) -> str:
    text = strip_html(text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def first_value(data: dict, keys: list[str], default: str = "") -> str:
    for key in keys:
        value = data.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def xml_text(element: Optional[ET.Element], tag_name: str, default: str = "") -> str:
    if element is None:
        return default

    found = element.find(tag_name)
    if found is not None and found.text:
        return found.text.strip()

    # namespace가 붙어 오는 경우 대비
    for child in list(element):
        if child.tag.endswith(tag_name) and child.text:
            return child.text.strip()

    return default


def source_label(article: dict) -> str:
    source = article.get("source", "")
    source_name = article.get("source_name", "")
    domain = article.get("domain", "")
    country = article.get("source_country", "")

    parts = [x for x in [source, source_name, domain, country] if x]
    return " / ".join(parts)


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
    """여러 뉴스 API 날짜 문자열을 UTC datetime으로 변환한다."""
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

    # 3) PolicyBriefing류: 2026-05-05 / 20260505 / 2026.05.05 / 09/27/2021 17:48:00
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y%m%d%H%M%S",
        "%Y%m%d",
        "%Y.%m.%d %H:%M",
        "%Y.%m.%d",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
    ):
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
    domain = get_domain(url)
    return TRUSTED_DOMAIN_SCORES.get(domain, 0) + DOMAIN_PENALTIES.get(domain, 0)


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

        if worksheet is None:
            raise RuntimeError("구글시트 첫 번째 워크시트를 찾지 못했습니다.")

        logger.info(f"[{topic_name}] 시트 대상 확인 | spreadsheet={sh.title}, worksheet={worksheet.title}")

        now_str = fmt_kst("%Y-%m-%d %H:%M:%S")
        titles = " | ".join([a.get("title", "") for a in articles])
        urls = " | ".join([a.get("url", "") for a in articles])
        sources = " | ".join([source_label(a) for a in articles])

        # 저장 열: 시각 | 분야 | 요약 | 기사제목들 | URL들 | 출처들
        worksheet.append_row(
            [now_str, topic_name, summary, titles, urls, sources],
            value_input_option="USER_ENTERED",
        )
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
    retries: int = 1,
) -> Any:
    for attempt in range(retries + 1):
        try:
            async with session.get(url, headers=headers, params=params, timeout=HTTP_TIMEOUT_SECONDS) as response:
                if response.status == 200:
                    return await response.json(content_type=None)

                body = await response.text()
                logger.warning(f"HTTP {response.status} | {url} | {body[:240]}")

                if response.status in {429, 500, 502, 503, 504} and attempt < retries:
                    await asyncio.sleep(1.5 + attempt)
                    continue

                return None

        except asyncio.TimeoutError:
            logger.warning(f"요청 시간 초과 | {url}")
            if attempt < retries:
                await asyncio.sleep(1.5 + attempt)
                continue
            return None

        except Exception as e:
            logger.warning(f"요청 실패 | {url} | {e}")
            if attempt < retries:
                await asyncio.sleep(1.5 + attempt)
                continue
            return None

    return None


async def fetch_naver_news_async(
    session: aiohttp.ClientSession,
    query: str,
    semaphore: asyncio.Semaphore,
) -> List[Dict]:
    async with semaphore:
        url = "https://openapi.naver.com/v1/search/news.json"
        headers = {
            "X-Naver-Client-Id": NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
        }
        params = {"query": query, "display": PER_KEYWORD_DISPLAY, "sort": "date"}

        data = await fetch_json(session, url, headers=headers, params=params, retries=1)
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

            description = compact_text(item.get("description", ""), limit=700)

            items.append({
                "title": title,
                "description": description,
                "content": description,
                "url": link,
                "canonical_url": canonicalize_url(link),
                "domain": get_domain(link),
                "published_at": pub_date,
                "published_dt": parse_pub_date(pub_date),
                "normalized_title": normalize_title(title),
                "short_title": shorten_title(title),
                "matched_query": query,
                "source": "Naver",
                "source_name": "",
                "source_country": "kr",
            })

        return items


async def fetch_gnews_async(
    session: aiohttp.ClientSession,
    query: str,
    semaphore: asyncio.Semaphore,
) -> List[Dict]:
    async with semaphore:
        if not GNEWS_API_KEY:
            return []

        url = "https://gnews.io/api/v4/search"
        from_dt = (now_utc() - timedelta(days=MAX_ARTICLE_AGE_DAYS)).isoformat().replace("+00:00", "Z")
        to_dt = now_utc().isoformat().replace("+00:00", "Z")

        params = {
            "q": query,
            "apikey": GNEWS_API_KEY,
            "lang": "en",
            "max": 10,
            "in": "title,description,content",
            "from": from_dt,
            "to": to_dt,
            "sortby": "publishedAt",
        }

        data = await fetch_json(session, url, params=params, retries=0)
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

            raw_description = item.get("description", "") or ""
            raw_content = item.get("content", "") or ""
            source_info = item.get("source") or {}
            if not isinstance(source_info, dict):
                source_info = {}

            source_name = source_info.get("name", "") or ""
            source_country = source_info.get("country", "") or ""

            combined_description = compact_text(
                " ".join([raw_description, raw_content]).strip(),
                limit=1000,
            )

            items.append({
                "title": strip_html(title),
                "description": combined_description,
                "content": compact_text(raw_content, limit=1000),
                "url": link,
                "canonical_url": canonicalize_url(link),
                "domain": get_domain(link),
                "published_at": pub_date,
                "published_dt": parse_pub_date(pub_date),
                "normalized_title": normalize_title(title),
                "short_title": shorten_title(title),
                "matched_query": query,
                "source": "GNews",
                "source_name": source_name,
                "source_country": source_country,
            })

        return items


def policy_date_range(days: int = MAX_ARTICLE_AGE_DAYS) -> tuple[str, str]:
    end_dt = now_kst()
    start_dt = end_dt - timedelta(days=days)
    return start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d")


def policy_item_to_article(item: ET.Element, topic_name: str, cfg: dict) -> Optional[dict]:
    # 정책브리핑 정책뉴스 API XML 주요 필드
    title = xml_text(item, "Title")
    subtitle1 = xml_text(item, "SubTitle1")
    subtitle2 = xml_text(item, "SubTitle2")
    subtitle3 = xml_text(item, "SubTitle3")
    content = xml_text(item, "DataContents")
    department = xml_text(item, "MinisterCode")
    approve_date = xml_text(item, "ApproveDate")
    original_url = xml_text(item, "OriginalUrl")
    news_id = xml_text(item, "NewsItemId")

    if not title:
        return None

    url_str = original_url
    if not url_str and news_id:
        url_str = f"https://www.korea.kr/news/policyNewsView.do?newsId={news_id}&call_from=openData"

    if not url_str:
        return None

    combined_description = compact_text(
        " ".join([subtitle1, subtitle2, subtitle3, department, content]).strip(),
        limit=1200,
    )

    searchable = normalize_text(f"{title} {combined_description}")
    topic_words = cfg.get("search_keywords", []) + cfg.get("positive_keywords", [])

    # 정책브리핑 API는 검색어가 아니라 날짜 목록 API라서 받아온 뒤 로컬에서 분야 키워드 필터링
    if not any(normalize_text(word) in searchable for word in topic_words):
        return None

    matched_query = ""
    for word in topic_words:
        if normalize_text(word) in searchable:
            matched_query = word
            break

    return {
        "title": strip_html(title),
        "description": combined_description,
        "content": compact_text(content, limit=1200),
        "url": url_str,
        "canonical_url": canonicalize_url(url_str),
        "domain": "korea.kr",
        "published_at": approve_date,
        "published_dt": parse_pub_date(approve_date),
        "normalized_title": normalize_title(title),
        "short_title": shorten_title(title),
        "matched_query": matched_query or topic_name,
        "source": "PolicyBriefing",
        "source_name": department,
        "source_country": "kr",
    }


async def fetch_policy_briefing_async(
    session: aiohttp.ClientSession,
    topic_name: str,
    cfg: dict,
    semaphore: asyncio.Semaphore,
) -> List[Dict]:
    async with semaphore:
        if not GOV_API_KEY or not GOV_ENDPOINT:
            logger.warning(
                f"PolicyBriefing 환경변수 누락 | "
                f"GOV_API_KEY={'있음' if GOV_API_KEY else '없음'}, "
                f"GOV_ENDPOINT={'있음' if GOV_ENDPOINT else '없음'}"
            )
            return []

        start_date, end_date = policy_date_range(MAX_ARTICLE_AGE_DAYS)

        params = {
            "serviceKey": GOV_API_KEY,
            "startDate": start_date,
            "endDate": end_date,
        }

        try:
            async with session.get(
                GOV_ENDPOINT,
                params=params,
                timeout=HTTP_TIMEOUT_SECONDS,
            ) as response:
                body = await response.text()

                if response.status != 200:
                    logger.warning(
                        f"PolicyBriefing HTTP {response.status} | "
                        f"date={start_date}-{end_date} | body={body[:240]}"
                    )
                    return []

                try:
                    root = ET.fromstring(body)
                except ET.ParseError as e:
                    logger.warning(f"PolicyBriefing XML 파싱 실패 | {e} | body={body[:240]}")
                    return []

                result_code = root.findtext(".//resultCode") or root.findtext(".//returnReasonCode") or ""
                result_msg = root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg") or ""

                if result_code and result_code not in {"0", "00"}:
                    logger.warning(
                        f"PolicyBriefing API 오류 | code={result_code}, msg={result_msg}, "
                        f"date={start_date}-{end_date}"
                    )
                    return []

                raw_items = root.findall(".//item")
                if not raw_items:
                    raw_items = [
                        elem for elem in root.iter()
                        if str(elem.tag).lower().endswith("item")
                    ]

                logger.info(
                    f"[{topic_name}] PolicyBriefing 응답 확인 | "
                    f"items={len(raw_items)}, date={start_date}-{end_date}"
                )

                items: List[Dict] = []

                for raw_item in raw_items:
                    article = policy_item_to_article(raw_item, topic_name, cfg)
                    if not article:
                        continue

                    if article.get("published_at") and not is_recent(article["published_at"]):
                        continue

                    if is_globally_blocked(article.get("title", ""), article.get("url", "")):
                        continue

                    items.append(article)

                items.sort(
                    key=lambda x: x.get("published_dt") or datetime.min.replace(tzinfo=UTC),
                    reverse=True,
                )
                return items[:POLICY_RESULT_LIMIT]

        except asyncio.TimeoutError:
            logger.warning(f"PolicyBriefing 요청 시간 초과 | date={start_date}-{end_date}")
            return []

        except Exception as e:
            logger.warning(f"PolicyBriefing 요청 실패 | {e}")
            return []


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
def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    norm = normalize_text(text)
    hits = []
    for kw in keywords:
        kw_norm = normalize_text(kw)
        if kw_norm and kw_norm in norm:
            hits.append(kw)
    return hits


def keyword_hits_in_title_and_desc(article: dict, keywords: list[str]) -> tuple[list[str], list[str]]:
    title = article.get("title", "")
    desc = article.get("description", "")
    return keyword_hits(title, keywords), keyword_hits(desc, keywords)


def unique_hit_count(*hit_lists: list[str]) -> int:
    merged = []
    for hits in hit_lists:
        merged.extend(hits)
    return len({normalize_text(x) for x in merged if normalize_text(x)})


def story_cluster_id(topic_name: str, article: dict) -> Optional[str]:
    """같은 사건을 제목만 바꿔 중복 선정하지 않기 위한 클러스터 ID를 반환한다."""
    full = f"{article.get('title', '')} {article.get('description', '')}"

    for cluster in STORY_CLUSTERS.get(topic_name, []):
        terms = cluster.get("terms", [])
        min_hits = int(cluster.get("min_hits", 2))
        hits = keyword_hits(full, terms)
        if unique_hit_count(hits) >= min_hits:
            return str(cluster.get("id", "unknown_cluster"))

    return None


def story_cluster_limit(topic_name: str, cluster_id: str) -> int:
    for cluster in STORY_CLUSTERS.get(topic_name, []):
        if str(cluster.get("id")) == cluster_id:
            return int(cluster.get("max_items", 1))
    return 1


def topic_gate(topic_name: str, article: dict, cfg: dict) -> tuple[bool, str]:
    """단어 하나만 맞았다고 통과시키지 않고, 핵심어와 보강어를 교차검증한다."""
    title = article.get("title", "")
    desc = article.get("description", "")
    full = f"{title} {desc}"

    # 글로벌 차단은 기사 제목/도메인 기준으로 선제 배제
    if is_globally_blocked(title, article.get("url", "")):
        return False, "global_blocked"

    hard_hits = keyword_hits(full, cfg.get("hard_negative_keywords", []))
    if hard_hits:
        return False, f"hard_negative:{','.join(hard_hits[:3])}"

    title_core, desc_core = keyword_hits_in_title_and_desc(article, cfg.get("core_keywords", []))
    title_pos, desc_pos = keyword_hits_in_title_and_desc(article, cfg.get("positive_keywords", []))
    title_cross, desc_cross = keyword_hits_in_title_and_desc(article, cfg.get("crosscheck_keywords", []))

    core_count = unique_hit_count(title_core, desc_core)
    total_count = unique_hit_count(title_core, desc_core, title_pos, desc_pos, title_cross, desc_cross)
    min_hits = int(cfg.get("min_keyword_hits", 2))

    if core_count == 0:
        return False, "no_core_keyword"

    if total_count < min_hits:
        return False, f"weak_crosscheck:{total_count}/{min_hits}"

    # 검색어가 너무 넓은 주제 보정
    # 세계: '국제' 하나만으로 국내 행사/박람회가 들어오는 문제 방지
    if topic_name == "세계":
        strong_world_hits = keyword_hits(
            full,
            [
                "미국", "중국", "일본", "유럽", "중동", "우크라이나", "러시아",
                "EU", "대만", "북한", "이란", "이스라엘", "가자", "나토",
                "백악관", "트럼프", "바이든", "시진핑", "푸틴", "정상회담",
                "외교", "제재", "전쟁", "휴전", "분쟁", "호르무즈", "유엔",
            ],
        )
        if not strong_world_hits:
            return False, "world_without_strong_anchor"

    # IT: '우주' 같은 다의어는 과학/기술 보강어가 있어야 통과
    if topic_name == "IT·과학":
        sports_noise = keyword_hits(full, ["야구", "투수", "타자", "이닝", "홈런", "구단", "감독", "선수", "리그"])
        if sports_noise:
            return False, f"it_sports_noise:{','.join(sports_noise[:3])}"

        broad_only_hits = keyword_hits(full, ["우주", "모델", "기술"])
        tech_anchor_hits = keyword_hits(
            full,
            [
                "AI", "인공지능", "챗GPT", "오픈AI", "LLM", "반도체", "GPU",
                "엔비디아", "로봇", "양자", "위성", "항공우주", "연구",
                "논문", "개발", "데이터센터", "알고리즘", "바이오", "의료 AI",
            ],
        )
        if broad_only_hits and not tech_anchor_hits:
            return False, "it_broad_keyword_only"

    # 정치: 국내 정치 브리핑이므로 해외 정당/선거만 있는 기사는 세계로 보내는 게 낫다
    if topic_name == "정치":
        domestic_hits = keyword_hits(
            full,
            [
                "한국", "국회", "대통령실", "대통령", "정부", "총리", "장관",
                "민주당", "국민의힘", "개혁신당", "조국혁신당", "선관위",
                "특검", "서울", "부산", "광주", "인천", "대구", "대전",
                "울산", "경기", "강원", "충북", "충남", "전북", "전남",
                "경북", "경남", "제주",
            ],
        )
        foreign_only_hits = keyword_hits(full, ["루마니아", "인디애나", "미국", "유럽", "트럼프", "공화당", "민주당 경선"])
        if foreign_only_hits and not domestic_hits:
            return False, "foreign_politics"

    return True, "ok"


def score_article_for_topic(topic_name: str, article: dict, cfg: dict) -> int:
    title = normalize_text(article.get("title", ""))
    desc = normalize_text(article.get("description", ""))
    full = f"{title} {desc}".strip()

    if article.get("domain", "") in cfg.get("blocked_domains", set()):
        return -999

    passed, reason = topic_gate(topic_name, article, cfg)
    article["topic_gate"] = reason
    if not passed:
        return -999

    score = domain_score(article.get("url", ""))

    cluster_id = story_cluster_id(topic_name, article)
    if cluster_id:
        article["story_cluster"] = cluster_id

    if article.get("source") == "PolicyBriefing":
        score += 12

    if article.get("source") == "GNews":
        score += 9 if topic_name in GNEWS_TOPICS else 4

    core_title_hits, core_desc_hits = keyword_hits_in_title_and_desc(article, cfg.get("core_keywords", []))
    pos_title_hits, pos_desc_hits = keyword_hits_in_title_and_desc(article, cfg.get("positive_keywords", []))
    cross_title_hits, cross_desc_hits = keyword_hits_in_title_and_desc(article, cfg.get("crosscheck_keywords", []))

    score += len(core_title_hits) * 6
    score += len(core_desc_hits) * 3
    score += len(pos_title_hits) * 4
    score += len(pos_desc_hits) * 2
    score += len(cross_title_hits) * 2
    score += len(cross_desc_hits) * 1

    matched_query = normalize_text(article.get("matched_query", ""))
    if matched_query:
        if matched_query in title:
            score += 4
        elif matched_query in desc:
            score += 2

    for kw in cfg.get("negative_keywords", []):
        kw_norm = normalize_text(kw)
        if kw_norm in title:
            score -= 7
        elif kw_norm in desc:
            score -= 4

    # 제목에 핵심어가 있으면 우선도 상승. 설명에만 스치듯 있으면 과도한 상승 방지.
    if core_title_hits:
        score += 4

    article["topic_debug"] = (
        f"core={core_title_hits + core_desc_hits[:3]}, "
        f"pos={pos_title_hits + pos_desc_hits[:3]}, "
        f"cross={cross_title_hits + cross_desc_hits[:3]}"
    )

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
    rejected_reasons = defaultdict(int)

    for item in dedupe_candidate_pool(candidates):
        url = item.get("canonical_url", "")
        title = item.get("normalized_title", "")

        if (url and url in used_urls) or (title and is_similar_title(title, used_titles)):
            rejected_reasons["used_duplicate"] += 1
            continue

        if not FORCE_TEST_MODE:
            if (url and url in seen_urls) or (title and is_similar_title(title, seen_titles)):
                rejected_reasons["seen_duplicate"] += 1
                continue

        passed, gate_reason = topic_gate(topic_name, item, cfg)
        if not passed:
            rejected_reasons[gate_reason] += 1
            continue

        score = score_article_for_topic(topic_name, item, cfg)
        item["topic_score"] = score
        item["topic_gate"] = gate_reason

        if score >= cfg.get("min_score", 7):
            scored.append(item)
        else:
            rejected_reasons["low_score"] += 1

    if rejected_reasons:
        logger.info(
            f"[{topic_name}] 필터 제외 요약 | "
            + ", ".join([f"{k}:{v}" for k, v in sorted(rejected_reasons.items())[:8]])
        )

    scored.sort(
        key=lambda x: (
            x.get("topic_score", 0),
            x.get("published_dt") or datetime.min.replace(tzinfo=UTC),
        ),
        reverse=True,
    )

    picked = []
    domain_counts = defaultdict(int)
    cluster_counts = defaultdict(int)

    for item in scored:
        domain = item.get("domain", "") or "unknown"

        if domain_counts[domain] >= MAX_DOMAIN_PER_TOPIC:
            rejected_reasons[f"domain_limit:{domain}"] += 1
            continue

        cluster_id = item.get("story_cluster") or story_cluster_id(topic_name, item)
        if cluster_id:
            limit = story_cluster_limit(topic_name, cluster_id)
            if cluster_counts[cluster_id] >= limit:
                rejected_reasons[f"story_duplicate:{cluster_id}"] += 1
                continue

        picked.append(item)
        domain_counts[domain] += 1
        if cluster_id:
            cluster_counts[cluster_id] += 1

        if len(picked) >= ARTICLES_PER_TOPIC:
            break

    if rejected_reasons:
        story_or_domain_reasons = {
            k: v for k, v in rejected_reasons.items()
            if k.startswith("story_duplicate") or k.startswith("domain_limit")
        }
        if story_or_domain_reasons:
            logger.info(
                f"[{topic_name}] 선정 단계 제외 요약 | "
                + ", ".join([f"{k}:{v}" for k, v in sorted(story_or_domain_reasons.items())[:8]])
            )

    return picked


# -----------------------------
# 요약 및 배송
# -----------------------------
def build_summary_prompt(topic_name: str, articles: list[dict]) -> str:
    article_blocks = []

    for i, article in enumerate(articles, 1):
        article_blocks.append(
            f"[기사 {i}]\n"
            f"출처정보: {source_label(article)}\n"
            f"검색어: {article.get('matched_query', '')}\n"
            f"게시시각: {article.get('published_at', '')}\n"
            f"제목: {article.get('title', '')}\n"
            f"설명/본문요약재료: {compact_text(article.get('description', ''), 1100)}\n"
            f"링크: {article.get('url', '')}\n"
        )

    return f"""역할: 텔레그램 뉴스 브리핑 편집자

목표:
- '{topic_name}' 분야 기사들을 15~25초 안에 읽히는 한국어 뉴스 브리핑으로 요약한다.
- 독자는 바쁜 일반 사용자다. 핵심 흐름은 빠르게, 사실관계는 정확하게 전달한다.
- 단순히 제목을 다시 쓰는 것이 아니라, 입력 기사들이 말하는 핵심 변화를 짧게 압축한다.

가장 중요한 원칙:
- 입력 기사에 없는 사실, 숫자, 인명, 기관명, 시점, 전망을 새로 만들지 않는다.
- 제목에 있는 표현이라도 설명/본문요약재료에서 뒷받침되지 않으면 강한 단정으로 쓰지 않는다.
- 여러 기사를 억지로 하나의 인과관계로 엮지 않는다.
- 같은 사건을 다룬 기사들이 여러 개 있으면 같은 내용을 반복하지 말고 대표 이슈 1개로만 정리한다.
- 기사 간 공통 흐름이 없으면 공통 흐름을 만들지 말고, 각각의 이슈로 분리한다.
- 원문에 없는 '사상 첫', '역대급', '초비상', '충격', '패닉', '눈앞', '대폭발', '대혼란' 같은 과장 표현을 쓰지 않는다.
- 수치가 있는 경우 입력 기사에 나온 수치만 사용한다.
- 수치의 단위, 시점, 비교 대상이 불명확하면 수치를 생략하거나 완화해서 쓴다.
- 불확실하거나 전망성인 내용은 '~가능성', '~전망', '~우려', '~관측'처럼 완화해서 쓴다.
- 원문이 보도자료·정책브리핑 자료라면 정부 발표라는 성격을 반영하고, 정책 효과를 확정적으로 말하지 않는다.
- GNews 등 해외 기사 재료는 한국어로 자연스럽게 번역하되, 원문에 없는 한국 영향은 덧붙이지 않는다.
- 특정 진영·기업·기관을 홍보하는 말투를 피하고, 건조한 뉴스 문장으로 쓴다.
- 기사 제목의 자극적 표현을 그대로 따라 하기보다, 사실 중심 문장으로 낮춰 쓴다.

분야별 처리 기준:
- 경제: 시장 수치, 금리, 환율, 물가, 부동산, 수출입, 고용 관련 내용은 숫자를 임의로 보강하지 않는다.
- 경제: 주가·환율·금리 움직임은 '상승', '하락', '변동성 확대'처럼 입력 근거가 있는 범위에서만 쓴다.
- 정치: 정당·정부·국회 이슈는 주체와 조치만 간결하게 쓴다. 의도 추정, 배후 추정, 승패 단정은 금지한다.
- 정치: 선거·공천·법안·외교안보 이슈는 누가 무엇을 했는지 중심으로 정리한다.
- 사회·생활문화: 단순 지역행사보다 사건, 사고, 재난, 제도 변화, 생활 영향, 교육·복지 변화를 우선한다.
- 사회·생활문화: 축제·공연·전시 기사는 전국적 의미나 생활 영향이 약하면 과장하지 않는다.
- 세계: 국제 이슈는 국가·기관·분쟁 당사자를 정확히 구분한다.
- 세계: 해외 정치인의 발언은 원문에 있는 범위에서만 요약하고, 한국에 미칠 영향은 기사에 있을 때만 언급한다.
- IT·과학: 기술 성과는 '연구', '개발', '발표', '도입', '상용화' 단계를 구분한다.
- IT·과학: AI·반도체·우주·바이오 기사는 성능이나 파급효과를 원문 이상으로 확대하지 않는다.

출력 형식:
한눈에
- 핵심 흐름 1
- 핵심 흐름 2
- 핵심 흐름 3
- 핵심 흐름 4

주요 이슈
1) 소제목
한 문장 설명

2) 소제목
한 문장 설명

3) 소제목
한 문장 설명

4) 소제목
한 문장 설명

작성 규칙:
- '한눈에'는 반드시 4줄로 작성한다.
- '한눈에' 각 줄은 서로 다른 이슈를 다룬다.
- '주요 이슈'는 기사 수가 4개 미만이면 있는 기사 수만큼만 작성한다.
- 주요 이슈의 소제목은 10~18자 정도로 짧게 쓴다.
- 각 설명은 1문장으로 작성한다.
- 각 설명은 35~75자 정도로 작성한다.
- 링크는 출력하지 않는다.
- '브리핑입니다', '다음은', '요약하면', '오늘의 뉴스입니다' 같은 인삿말은 쓰지 않는다.
- 마크다운 굵게 표시, 이모지, 해시태그는 쓰지 않는다.
- 입력 기사 제목을 그대로 복붙하지 말고, 의미만 압축한다.
- 단, 고유명사와 핵심 수치는 원문 표현을 유지한다.
- 같은 기관명이나 인물명이 반복되면 두 번째부터는 짧게 줄여도 된다.
- 입력 기사 간 관련성이 낮으면 무리하게 하나의 큰 흐름으로 묶지 않는다.
- 기사 내용이 약하거나 지역성이 강하면 '전국적 대형 이슈'처럼 포장하지 않는다.
- 모르는 내용은 추측하지 말고, 입력된 기사 범위 안에서만 쓴다.
- 주요 이슈 1~4는 반드시 입력 기사 1~4의 순서와 1:1로 대응한다.
- 각 주요 이슈 설명은 해당 번호의 기사 제목과 설명/본문요약재료에서 확인되는 내용만 사용한다.
- 다른 기사 내용이나 외부 지식을 섞어 쓰지 않는다.
- 기사 제목과 설명/본문요약재료가 서로 맞지 않으면 제목 기준으로 보수적으로 요약한다.

좋은 문장 예시:
- 한국은행의 금리 결정 경계감이 커지며 채권시장 변동성이 이어졌다.
- 정부가 청년 주거 지원 방안을 발표했지만 구체적 효과는 추가 확인이 필요하다.
- AI 반도체 설계 자동화 연구가 공개되며 개발 기간 단축 가능성이 제기됐다.

나쁜 문장 예시:
- 시장이 패닉에 빠졌다.
- 역대급 위기가 현실화됐다.
- 한국 경제 전체가 큰 충격을 받을 전망이다.
- 이번 정책으로 문제가 완전히 해결될 것으로 보인다.

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
async def collect_topic_candidates(
    session: aiohttp.ClientSession,
    topic_name: str,
    cfg: dict,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    logger.info(f"[{topic_name}] 수집 시작")

    tasks = []

    for idx, q in enumerate(cfg["search_keywords"]):
        if idx < NAVER_KEYWORD_LIMIT:
            tasks.append(fetch_naver_news_async(session, q, semaphore))

        if topic_name in GNEWS_TOPICS and idx < GNEWS_KEYWORD_LIMIT:
            tasks.append(fetch_gnews_async(session, q, semaphore))

    if topic_name in POLICY_TOPICS:
        tasks.append(fetch_policy_briefing_async(session, topic_name, cfg, semaphore))

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
        + " / ".join([
            f"{a.get('short_title')}({a.get('topic_score')}, {a.get('source')})"
            for a in fresh_articles
        ])
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
