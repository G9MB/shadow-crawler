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
from datetime import datetime

# ================================================================= #
# ⭐ [유저 설정 구역] 관리용 키워드 리스트
# ================================================================= #
# 1. 토스 관련 키워드 (포함할 단어 / 제외할 단어)
INCLUDE_TOSS = ["토스"]
EXCLUDE_TOSS = ["토스트", "팀플"]

# 2. 네이버 페이 관련 키워드 (기본 포함 단어 / 매칭될 필수 단어)
INCLUDE_NAVER = ["네이버"]
MATCH_NAVER = ["180", "100"]

# 3. 댓글에서 제외할 단순 인사성 단어 목록
EXCLUDE_COMMENTS = ["감사", "고맙", "추천", "ㅊㅊ", "ㄱㅅ"]

# 4. 파일 경로 설정 (크론탭 환경 대응 절대 경로)
LOG_FILE_PATH = "/home/swkim/shadow-crawler/crawler.log"
DB_FILE = "/home/swkim/shadow-crawler/sent_posts.txt"
# ================================================================= #

# 로그 시스템 설정 (하루 단위 로테이션 및 자정 자동 파기)
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
    """파일에서 {글번호: 최초발송시간} 구조의 딕셔너리를 읽어옵니다."""
    sent_dict = {}
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or "," not in line:
                    continue
                time_str, post_no = line.split(",", 1)
                try:
                    sent_dict[post_no] = datetime.strptime(
                        time_str, "%Y-%m-%d %H:%M:%S"
                    )
                except ValueError:
                    continue
    return sent_dict


def save_sent_post(post_no):
    """새로 발송한 게시글의 글 번호를 현재 시간과 함께 파일에 기록합니다."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(DB_FILE, "a", encoding="utf-8") as f:
        f.write(f"{now_str},{post_no}\n")


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


def get_detail_content(post_url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        response = requests.get(post_url, headers=headers)
        if response.status_code != 200:
            return "본문 페이지 접속 실패", []

        soup = BeautifulSoup(response.text, "html.parser")
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
        if len(content_text) > 250:
            content_text = content_text[:250] + "...(지면상 생략)"

        comment_elements = soup.select(
            '.comment_memo, .comment_text, .comment-content, div[class*="comment_"]'
        )
        comments = []

        for reply in comment_elements:
            if len(comments) >= 5:
                break

            reply_text = reply.get_text().strip()
            reply_text = re.sub(r"\s+", " ", reply_text)

            if reply_text and len(reply_text) > 1:
                # 🎯 상단에 정의한 EXCLUDE_COMMENTS 리스트 조건 대조 및 필터링
                if any(keyword in reply_text for keyword in EXCLUDE_COMMENTS):
                    continue
                comments.append(f"- {reply_text}")

        return content_text, comments
    except Exception as e:
        logger.error(f"❌ 상세 페이지 분석 에러: {e}")
        return "본문 로딩 실패", []


def check_ppomppu_coupon():
    sent_posts = load_sent_posts()

    url = "https://m.ppomppu.co.kr/new/bbs_list.php?id=coupon&extref=1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            logger.error("❌ 뽐뿌 서버 접속 실패")
            return

        soup = BeautifulSoup(response.text, "html.parser")
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
            post_url = (
                f"https://m.ppomppu.co.kr/new/bbs_view.php?id=coupon&no={post_no}"
            )

            # 🎯 any() 문법을 적용하여 축약된 대괄호 리스트 키워드 필터링
            is_toss_valid = any(w in title_text for w in INCLUDE_TOSS) and not any(
                w in title_text for w in EXCLUDE_TOSS
            )
            is_naver_valid = any(w in title_text for w in INCLUDE_NAVER) and any(
                w in title_text for w in MATCH_NAVER
            )

            if is_toss_valid or is_naver_valid:

                # 🎯 최초 발송 기록 대조 및 20분(1200초) 타임아웃 락 검사
                if post_no in sent_posts:
                    first_sent_time = sent_posts[post_no]
                    time_passed = datetime.now() - first_sent_time

                    if time_passed.total_seconds() > 1200:
                        continue

                content, comments_list = get_detail_content(post_url)
                comments_str = (
                    "\n".join(comments_list)
                    if comments_list
                    else "등록된 댓글이 없습니다."
                )

                category = "🚨 " if is_toss_valid else "💚 "

                # 팝업 배너 최적화 메시지 생성
                alert_msg = (
                    f"{category}{title_text}\n" f"📄 본문: {content.strip()[:100]}\n"
                )

                if comments_list:
                    alert_msg += f"💬 댓글요약:\n{comments_str}\n"
                else:
                    alert_msg += "💬 댓글: 등록된 댓글이 없습니다.\n"

                alert_msg += f"🔗 링크: {post_url}"

                send_telegram_message(alert_msg)

                # 중복 및 20분 검사용 고유 글 번호 저장
                save_sent_post(post_no)
                time.sleep(1.5)

    except Exception as e:
        logger.error(f"❌ 크롤링 중 에러 발생: {e}")


if __name__ == "__main__":
    logger.info("🚀 [Crontab] 30분 주기 트리거 발동. 8회 집중 정찰을 시작합니다.")

    for attempt in range(1, 9):
        logger.info(f"🕵️‍♂️ [{attempt}/8 번째 정찰 수행 중...]")
        check_ppomppu_coupon()

        if attempt == 8:
            break

        next_sleep = random.randint(25, 35)
        logger.info(
            f"⏳ 보안 우회 및 밀착 감시를 위해 {next_sleep}초 대기 후 다음 스캔..."
        )
        time.sleep(next_sleep)

    logger.info("✅ 8회 집중 밀착 정찰 완료. 작업을 마칩니다.")
