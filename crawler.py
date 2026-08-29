import os
from dotenv import load_dotenv

load_dotenv()
import requests
from bs4 import BeautifulSoup
import time
import re
import random
import logging
from logging.handlers import TimedRotatingFileHandler
import json
from datetime import datetime

# ================================================================= #
# ⭐ [유저 설정 구역] Cloudflare Worker 및 크롤링 옵션
# ================================================================= #
# 0. Cloudflare Worker URL (본인의 서브도메인 주소로 변경하세요)
WORKER_URL = "https://ppomppu-proxy.gohanbit22.workers.dev"

# 1. 토스 관련 키워드 (포함할 단어 / 제외할 단어)
INCLUDE_TOSS = ["토스"]
EXCLUDE_TOSS = ["토스트", "알바"]

# 2. 네이버 페이 관련 키워드 (기본 포함 단어 / 매칭될 필수 단어)
INCLUDE_NAVER = ["네이버"]
MATCH_NAVER = ["180", "100", "120"]

# 3. 파일 경로 설정 (크론탭 환경 대응 절대 경로)
LOG_FILE_PATH = "/home/swkim/shadow-crawler/crawler.log"
DB_FILE = "/home/swkim/shadow-crawler/sent_posts.txt"
# ================================================================= #

# 로그 시스템 설정
logger = logging.getLogger("CrawlerLogger")
logger.setLevel(logging.INFO)

log_handler = TimedRotatingFileHandler(
    filename=LOG_FILE_PATH, when="midnight", interval=1, backupCount=1, encoding="utf-8"
)

formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
log_handler.setFormatter(formatter)
logger.addHandler(log_handler)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def load_sent_posts():
    """파일에서 이미 발송한 글번호 리스트 [ "1234", "1235" ] 구조를 읽어옵니다."""
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return json.loads(content)
        except Exception as e:
            logger.error(f"❌ DB 파일 읽기 실패 (새로 생성합니다): {e}")
    return []


def save_sent_posts(posts_list):
    """발송 완료된 글번호 리스트를 JSON 형태로 파일에 저장합니다."""
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(posts_list, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"❌ DB 파일 저장 실패: {e}")


def send_telegram_message(text):
    if not TOKEN or not CHAT_ID:
        logger.error("❌ 텔레그램 토큰 또는 CHAT_ID가 설정되지 않았습니다.")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        logger.error(f"❌ 텔레그램 발송 에러: {e}")


def fetch_via_worker(target_url):
    """Cloudflare Worker 프록시를 거쳐 대상 웹페이지 HTML을 안전하게 받아옵니다."""
    try:
        proxy_url = f"{WORKER_URL}?target={target_url}"
        response = requests.get(proxy_url, timeout=15)
        
        if response.status_code == 200:
            return response.content.decode("euc-kr", errors="replace")
        else:
            logger.error(f"❌ Worker 응답 에러 (Status: {response.status_code})")
            return None
    except Exception as e:
        logger.error(f"❌ Worker 중계 요청 실패: {e}")
        return None


def get_detail_content(post_url):
    """게시글 상세 페이지에서 본문 내용만 추출합니다 (댓글 제외)."""
    try:
        html_text = fetch_via_worker(post_url)
        if not html_text:
            return "본문 페이지 접속 실패"

        soup = BeautifulSoup(html_text, "html.parser")
        content_div = soup.select_one(
            ".board-contents, .pic_bg, .bbs_view_content, .cont"
        )

        if not content_div:
            content_div = soup.select_one("#mainContent")

        content_text = (
            content_div.get_text().strip()
            if content_div
            else "본문 내용을 파싱할 수 없는 구조입니다."
        )
        content_text = re.sub(r"\n+", "\n", content_text)
        
        # 본문이 너무 길면 200자로 자르고 생략 표시
        if len(content_text) > 200:
            content_text = content_text[:200] + "...(지면상 생략)"

        return content_text
    except Exception as e:
        logger.error(f"❌ 상세 페이지 본문 분석 에러: {e}")
        return "본문 로딩 실패"


def check_ppomppu_coupon():
    sent_posts = load_sent_posts()  # 이미 보낸 글번호 리스트
    db_updated = False

    target_list_url = "https://m.ppomppu.co.kr/new/bbs_list.php?id=coupon&extref=1"
    
    # Cloudflare Worker를 통하여 쿠폰 게시판 목록 가져오기
    html_text = fetch_via_worker(target_list_url)
    if not html_text:
        logger.error("❌ 뽐뿌 목록 페이지 수집 실패")
        return

    try:
        soup = BeautifulSoup(html_text, "html.parser")
        titles = soup.select("a.title_a, a.list_title, span.title, td.title a")

        if len(titles) == 0:
            titles = [
                a for a in soup.find_all("a") if "bbs_view.php" in a.get("href", "")
            ]

        logger.info(f"🔄 게시글 {len(titles)}개 스캔 중...")

        for item in titles:
            raw_text = item.get_text().strip()
            if not raw_text:
                continue

            lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
            if not lines:
                continue
            title_text = lines[0]
            title_text = re.sub(r"\s+", " ", title_text)

            if not title_text or len(title_text) < 3:
                continue

            raw_href = item.get("href", "")
            if not raw_href:
                continue

            no_match = re.search(r"no=(\d+)", raw_href)
            if not no_match:
                continue

            post_no = no_match.group(1)

            # 🎯 이미 보낸 글이면 스킵
            if post_no in sent_posts:
                continue

            # [조건] 제목 키워드 검사
            is_toss_valid = any(w in title_text for w in INCLUDE_TOSS) and not any(
                w in title_text for w in EXCLUDE_TOSS
            )
            is_naver_valid = any(w in title_text for w in INCLUDE_NAVER) and any(
                w in title_text for w in MATCH_NAVER
            )

            # 키워드 조건에 일치하는 신규 글이 발견된 경우
            if is_toss_valid or is_naver_valid:
                post_url = f"https://m.ppomppu.co.kr/new/bbs_view.php?id=coupon&no={post_no}"
                
                # 📄 본문 내용 추출 (신규 알림 대상일 때만 1회 호출)
                time.sleep(0.5)
                content_text = get_detail_content(post_url)

                category = "🚨 " if is_toss_valid else "💚 "

                # 📌 [제목 + 본문 내용 + 링크] 메시지 생성
                alert_msg = (
                    f"{category}{title_text}\n\n"
                    f"📄 본문:\n{content_text.strip()}\n\n"
                    f"🔗 링크: {post_url}"
                )

                send_telegram_message(alert_msg)
                logger.info(f"📢 [신규 알림 발송] {title_text}")

                # 발송 완료 목록에 추가
                sent_posts.append(post_no)
                db_updated = True
                time.sleep(1.0)

        # 새로운 발송 건이 있으면 DB 저장
        if db_updated:
            save_sent_posts(sent_posts)

    except Exception as e:
        logger.error(f"❌ 크롤링 중 에러 발생: {e}")


if __name__ == "__main__":
    logger.info("🚀 10분 모니터링 스캔 시작 (약 1분 간격으로 안전 스캔)")
    
    start_time = time.time()
    attempt = 1

    # 10분(600초) 동안 약 50~70초 사이의 랜덤 간격으로 모니터링
    while time.time() - start_time < 600:
        logger.info(f"🕵️‍♂️ [{attempt} 번째 스캔 중...]")
        check_ppomppu_coupon()
        
        next_sleep = random.uniform(50.0, 70.0)
        
        if (time.time() - start_time) + next_sleep >= 600:
            break

        logger.info(f"⏳ {next_sleep:.1f}초 대기 후 다음 스캔...")
        time.sleep(next_sleep)
        attempt += 1

    logger.info("✅ 10분 모니터링 스캔 완료. 작업을 마칩니다.")
