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
# ⭐ [유저 설정 구역] 관리용 키워드 리스트
# ================================================================= #
# 1. 토스 관련 키워드 (포함할 단어 / 제외할 단어)
INCLUDE_TOSS = ["토스"]
EXCLUDE_TOSS = ["토스트", "알바"]

# 2. 네이버 페이 관련 키워드 (기본 포함 단어 / 매칭될 필수 단어)
INCLUDE_NAVER = ["네이버"]
MATCH_NAVER = ["180", "100", "120"]

# 3. 필터링할 댓글 제외 단어 목록
EXCLUDE_COMMENTS = ["감사", "고맙", "추천", "ㅊㅊ", "ㄱㅅ", "종료"]

# 4. 파일 경로 설정 (크론탭 환경 대응 절대 경로)
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
    """파일에서 {글번호: {"time": "시간", "comments": [...]}} 구조를 읽어옵니다."""
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return json.loads(content)
        except Exception as e:
            logger.error(f"❌ DB 파일 읽기 실패 (새로 생성합니다): {e}")
    return {}


def save_all_posts(posts_dict):
    """전체 데이터 딕셔너리를 JSON 형태로 파일에 저장합니다."""
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(posts_dict, f, ensure_ascii=False, indent=2)
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
            if len(comments) >= 10:
                break
            reply_text = reply.get_text().strip()
            reply_text = re.sub(r"\s+", " ", reply_text)
            if reply_text and len(reply_text) > 1:
                comments.append(reply_text)

        return content_text, comments
    except Exception as e:
        logger.error(f"❌ 상세 페이지 분석 에러: {e}")
        return "본문 로딩 실패", []


def check_ppomppu_coupon():
    # 데이터 구조: { "글번호": {"time": "2026-06-11 21:00:00", "comments": ["댓1", "댓2"]} }
    sent_posts = load_sent_posts()
    db_updated = False

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

            # [조건 1] 제목 키워드 검사
            is_toss_valid = any(w in title_text for w in INCLUDE_TOSS) and not any(
                w in title_text for w in EXCLUDE_TOSS
            )
            is_naver_valid = any(w in title_text for w in INCLUDE_NAVER) and any(
                w in title_text for w in MATCH_NAVER
            )

            if is_toss_valid or is_naver_valid:

                is_first_time = post_no not in sent_posts

                # 🎯 [추가] 20분 시간 타임아웃 락 검사
                if not is_first_time:
                    first_sent_time_str = sent_posts[post_no]["time"]
                    first_sent_time = datetime.strptime(
                        first_sent_time_str, "%Y-%m-%d %H:%M:%S"
                    )
                    time_passed = datetime.now() - first_sent_time

                    # 최초 발송 후 20분(1200초)이 경과했다면 댓글 변화조차 체크하지 않고 무조건 제외
                    if time_passed.total_seconds() > 1200:
                        continue

                # 본문 및 최신 댓글 수집
                content, current_comments = get_detail_content(post_url)

                should_send = False

                if is_first_time:
                    # [조건 1] 최초 글 발견 시 무조건 알림 승인
                    should_send = True
                    display_comments = current_comments[:5]
                else:
                    # 재방문인 경우 (20분 이내 내부 변화 추적)
                    old_comments = sent_posts[post_no]["comments"]

                    # [조건 3] 새로 추가된 댓글만 필터링
                    new_comments = [
                        c for c in current_comments if c not in old_comments
                    ]

                    if not new_comments:
                        # [조건 2] 추가된 댓글이 없다면 알림 제외
                        continue

                    # [조건 4] 추가된 댓글 전부가 제외 단어인 경우 알림 자체를 제외
                    all_new_are_exclude = all(
                        any(k in nc for k in EXCLUDE_COMMENTS) for nc in new_comments
                    )
                    if all_new_are_exclude:
                        # 파일 내부의 댓글 데이터만 최신화하고 알림은 스킵
                        sent_posts[post_no]["comments"] = current_comments
                        db_updated = True
                        continue

                    # [조건 5] 감사 댓글은 빼고 실질적인 정보성 새 댓글만 추출
                    valid_new_comments = [
                        nc
                        for nc in new_comments
                        if not any(k in nc for k in EXCLUDE_COMMENTS)
                    ]

                    if valid_new_comments:
                        should_send = True
                        display_comments = valid_new_comments[:5]
                    else:
                        # 필터링 후 남은 새 댓글이 없다면 알림 제외
                        sent_posts[post_no]["comments"] = current_comments
                        db_updated = True
                        continue

                if should_send:
                    formatted_comments = [f"- {r}" for r in display_comments]
                    comments_str = (
                        "\n".join(formatted_comments)
                        if formatted_comments
                        else "표시할 새 댓글이 없습니다."
                    )

                    category = "🚨 " if is_toss_valid else "💚 "
                    title_prefix = (
                        category if is_first_time else f"💬[추가댓글] {category}"
                    )

                    alert_msg = (
                        f"{title_prefix}{title_text}\n"
                        f"📄 본문: {content.strip()[:100]}\n"
                    )

                    if is_first_time:
                        alert_msg += f"💬 댓글요약:\n{comments_str}\n"
                    else:
                        alert_msg += f"💬 새로 달린 댓글:\n{comments_str}\n"

                    alert_msg += f"🔗 링크: {post_url}"

                    send_telegram_message(alert_msg)

                    # 🎯 최초 발송인 경우에만 시간 기록을 유지하고, 댓글만 최신 상태로 기록 보존
                    if is_first_time:
                        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        sent_posts[post_no] = {
                            "time": now_str,
                            "comments": current_comments,
                        }
                    else:
                        sent_posts[post_no]["comments"] = current_comments

                    db_updated = True
                    time.sleep(1.5)

        if db_updated:
            save_all_posts(sent_posts)

    except Exception as e:
        logger.error(f"❌ 크롤링 중 에러 발생: {e}")


if __name__ == "__main__":
    logger.info("🚀 45회/10분 스캔 시작")

    for attempt in range(1, 46):
        logger.info(f"🕵️‍♂️ [{attempt}/45 번째 스캔 중...]")
        check_ppomppu_coupon()

        if attempt == 45:
            break

        next_sleep = random.uniform(10.0, 15.0)
        logger.info(
            f"⏳ {next_sleep}초 대기 후 다음 스캔..."
        )
        time.sleep(next_sleep)

    logger.info("✅ 45회/10분 스캔 완료. 작업을 마칩니다.")
    