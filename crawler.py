import os
import time
import re
import json
import logging
from logging.handlers import TimedRotatingFileHandler
import requests
import socketio
from dotenv import load_dotenv

load_dotenv()

# ================================================================= #
# ⭐ [유저 설정 구역] 
# ================================================================= #
# 1. 퀴즈 감지 키워드
QUIZ_START_KEYWORDS = ["토퀴"]  # 퀴즈 시작 알림용 키워드

# 2. 파일 경로
LOG_FILE_PATH = "/home/swkim/shadow-crawler/chat_crawler.log"

# 3. 채팅 서버 주소
SOCKET_URL = "https://luckyquizchat.duckdns.org"

# 4. 퀴즈 세션 유지 시간 (초) - 예: 600초 = 10분 동안 올라오는 정답 채집
SESSION_TIMEOUT = 600
# ================================================================= #

logger = logging.getLogger("ChatCrawlerLogger")
logger.setLevel(logging.INFO)
os.makedirs(os.path.dirname(LOG_FILE_PATH), exist_ok=True)

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

sio = socketio.Client(
    reconnection=True, 
    reconnection_attempts=0, 
    reconnection_delay=3,
    logger=False,
    engineio_logger=False
)

# ================================================================= #
# 🧠 실시간 퀴즈 세션 메모리 관리
# active_session = {
#     "telegram_msg_id": 12345,
#     "start_time": 1710000000,
#     "title_user": "작성자",
#     "title_text": "토퀴 시작",
#     "answers": ["123", "456"], # 감지된 정답 목록
#     "raw_history": []          # 발송 내역 중복 방지
# }
# ================================================================= #
active_session = None


def send_telegram_message(text):
    """새 텔레그램 메시지 발송 후 message_id 반환"""
    if not TOKEN or not CHAT_ID:
        logger.error("❌ 텔레그램 토큰/CHAT_ID 미설정")
        return None
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        res = requests.post(url, json=payload, timeout=10).json()
        if res.get("ok"):
            return res["result"]["message_id"]
    except Exception as e:
        logger.error(f"❌ 텔레그램 전송 실패: {e}")
    return None


def edit_telegram_message(msg_id, text):
    """기존 텔레그램 메시지 내용 수정"""
    if not TOKEN or not CHAT_ID or not msg_id:
        return
    url = f"https://api.telegram.org/bot{TOKEN}/editMessageText"
    payload = {
        "chat_id": CHAT_ID, 
        "message_id": msg_id, 
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logger.error(f"❌ 텔레그램 수정 실패: {e}")


def is_potential_answer(text):
    """단순 수다와 정답/숫자 제보 구분 로직"""
    # 1. 숫자 포함 여부 (퀴즈 정답은 대부분 숫자를 포함)
    has_digit = bool(re.search(r"\d", text))
    
    # 2. 정답 관련 단어 포함 여부
    ans_keywords = ["정답", "답", "ㅂ", "ㄷ", "임", "인듯", "같음", "확인"]
    has_ans_kw = any(kw in text for kw in ans_keywords)

    # 3. 너무 긴 수다글(40자 이상)은 제외
    is_short = len(text) <= 40

    return (has_digit or has_ans_kw) and is_short


@sio.event
def connect():
    logger.info("✅ luckyquizchat 웹소켓 서버에 성공적으로 연결되었습니다.")


@sio.event
def disconnect():
    logger.warning("⚠️ 웹소켓 연결이 끊어졌습니다. 자동 재연결 시도...")


@sio.on("*")
def catch_all(event_name, *args):
    global active_session
    try:
        data = args[0] if args else {}
        msg_text = ""
        user_name = "익명"

        if isinstance(data, dict):
            msg_text = str(data.get("msg") or data.get("text") or data.get("message") or "").strip()
            user_name = str(data.get("name") or data.get("user") or data.get("nickname") or "익명").strip()
        elif isinstance(data, str):
            msg_text = data.strip()

        if not msg_text:
            return

        now = time.time()

        # -------------------------------------------------------------
        # 1. 세션 만료 체크 (설정 시간이 지나면 활성 세션 종료)
        # -------------------------------------------------------------
        if active_session and (now - active_session["start_time"] > SESSION_TIMEOUT):
            logger.info("⏰ 퀴즈 세션이 만료되었습니다. 다음 토퀴를 대기합니다.")
            active_session = None

        # -------------------------------------------------------------
        # 2. '토퀴' 키워드 감지 -> 새로운 퀴즈 세션 생성 및 알림 발송
        # -------------------------------------------------------------
        if any(kw in msg_text for kw in QUIZ_START_KEYWORDS):
            # 이전 세션이 있더라도 새 토퀴가 오면 세션 새로 교체
            initial_text = (
                f"🚨 <b>[토퀴 퀴즈 제보 발생!]</b>\n\n"
                f"👤 작성자: {user_name}\n"
                f"💬 내용: {msg_text}\n\n"
                f"⏳ <i>후속 정답 채팅을 감시 중입니다...</i>"
            )
            msg_id = send_telegram_message(initial_text)

            if msg_id:
                active_session = {
                    "telegram_msg_id": msg_id,
                    "start_time": now,
                    "title_user": user_name,
                    "title_text": msg_text,
                    "answers": [],
                    "raw_history": [msg_text]
                }
                logger.info(f"📢 [새 토퀴 세션 시작] ID: {msg_id} | {msg_text}")
            return

        # -------------------------------------------------------------
        # 3. 활성 세션 진행 중일 때 후속 정답 채팅 감지 및 메시지 수정
        # -------------------------------------------------------------
        if active_session:
            # 중복 채팅 스킵
            if msg_text in active_session["raw_history"]:
                return

            # 정답 후보 메시지인지 필터링
            if is_potential_answer(msg_text):
                active_session["raw_history"].append(msg_text)
                active_session["answers"].append(f"• <b>{user_name}</b>: {msg_text}")

                # 정답 목록 텍스트 조립
                answers_formatted = "\n".join(active_session["answers"])

                updated_text = (
                    f"🚨 <b>[토퀴 퀴즈 실시간 업데이트]</b>\n\n"
                    f"📌 <b>제보:</b> {active_session['title_text']} ({active_session['title_user']})\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"💡 <b>수신된 정답/채팅 목록:</b>\n"
                    f"{answers_formatted}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🔄 <i>실시간 업데이트 중... ({int((SESSION_TIMEOUT - (now - active_session['start_time']))//60)}분 남음)</i>"
                )

                # 기존 텔레그램 메시지 수정 발송
                edit_telegram_message(active_session["telegram_msg_id"], updated_text)
                logger.info(f"✏️ [텔레그램 메세지 수정 완료] 추가된 내용: ({user_name}) {msg_text}")

    except Exception as e:
        logger.error(f"❌ 메시지 처리 중 에러: {e}")


def start_crawler():
    logger.info("🚀 토퀴 정답 추적 실시간 크롤러 시작")
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/138.0.0.0",
        "Referer": "https://luckyquiz3.blogspot.com/"
    }

    while True:
        try:
            if not sio.connected:
                sio.connect(
                    SOCKET_URL,
                    socketio_path="socket.io",
                    headers=headers,
                    transports=["websocket", "polling"],
                    wait_timeout=10
                )
                sio.wait()
        except Exception as e:
            logger.error(f"❌ 소켓 연결 오류: {e}")
            time.sleep(5)


if __name__ == "__main__":
    start_crawler()
