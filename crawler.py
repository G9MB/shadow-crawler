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
# 1. 감시할 키워드 (포함할 단어 / 제외할 단어)
INCLUDE_KEYWORDS = ["토퀴"]
EXCLUDE_KEYWORDS = ["종료", "마감"]

# 2. 파일 경로 설정 (크론탭/배포 환경 절대 경로)
LOG_FILE_PATH = "/home/swkim/shadow-crawler/chat_crawler.log"
DB_FILE = "/home/swkim/shadow-crawler/sent_chats.txt"

# 3. 채팅 서버 주소 및 파라미터
SOCKET_URL = "https://luckyquizchat.duckdns.org"
SOCKET_PARAMS = {
    "clientRef": "https://luckyquiz3.blogspot.com/",
    "inIframe": "true",
    "EIO": "4",
    "transport": "websocket"
}
# ================================================================= #

# 로그 시스템 설정
logger = logging.getLogger("ChatCrawlerLogger")
logger.setLevel(logging.INFO)

# 로그 파일 생성 디렉토리 체크
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

# Socket.IO 클라이언트 생성 (자동 재연결 설정)
sio = socketio.Client(
    reconnection=True, 
    reconnection_attempts=0, # 무한 재시도
    reconnection_delay=3,
    logger=False,
    engineio_logger=False
)


def load_sent_chats():
    """발송한 메시지 식별자/내용 중복 체크용 로드"""
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return json.loads(content)
        except Exception as e:
            logger.error(f"❌ DB 파일 읽기 실패: {e}")
    return []


def save_sent_chats(chats_list):
    """발송한 메시지 식별자/내용 중복 저장"""
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(chats_list, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"❌ DB 파일 저장 실패: {e}")


def send_telegram_message(text):
    if not TOKEN or not CHAT_ID:
        logger.error("❌ 텔레그램 토큰 또는 CHAT_ID가 설정되지 않았습니다.")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logger.error(f"❌ 텔레그램 발송 에러: {e}")


# ================================================================= #
# Socket.IO 이벤트 핸들러
# ================================================================= #
@sio.event
def connect():
    logger.info("✅ luckyquizchat 웹소켓 서버에 성공적으로 연결되었습니다.")


@sio.event
def disconnect():
    logger.warning("⚠️ 서버와 웹소켓 연결이 끊어졌습니다. 자동 재연결을 시도합니다...")


@sio.event
def connect_error(data):
    logger.error(f"❌ 웹소켓 연결 실패: {data}")


# 모든 수신 이벤트를 포착하는 와일드카드 핸들러
@sio.on("*")
def catch_all(event_name, *args):
    try:
        sent_chats = load_sent_chats()
        
        # 데이터 구조 추출
        data = args[0] if args else {}
        
        msg_text = ""
        user_name = "익명"
        msg_id = ""

        if isinstance(data, dict):
            msg_text = str(data.get("msg") or data.get("text") or data.get("message") or "").strip()
            user_name = str(data.get("name") or data.get("user") or data.get("nickname") or "익명").strip()
            msg_id = str(data.get("id") or data.get("_id") or "")
        elif isinstance(data, str):
            msg_text = data.strip()

        if not msg_text:
            return

        # 메시지 고유 식별값 (ID가 없으면 '작성자+내용'으로 생성)
        chat_identifier = msg_id if msg_id else f"{user_name}:{msg_text}"

        # 이미 발송된 메시지면 스킵
        if chat_identifier in sent_chats:
            return

        # [키워드 검사]
        is_valid = any(kw in msg_text for kw in INCLUDE_KEYWORDS) and not any(
            ex in msg_text for ex in EXCLUDE_KEYWORDS
        )

        if is_valid:
            alert_msg = (
                f"💬 [채팅 실시간 키워드 감지]\n\n"
                f"👤 작성자: {user_name}\n"
                f"💬 내용: {msg_text}\n\n"
                f"🔗 출처: https://luckyquiz3.blogspot.com/"
            )

            send_telegram_message(alert_msg)
            logger.info(f"📢 [신규 채팅 알림 발송] ({user_name}) {msg_text}")

            # DB에 저장 (최근 500개만 유지)
            sent_chats.append(chat_identifier)
            if len(sent_chats) > 500:
                sent_chats = sent_chats[-500:]
            save_sent_chats(sent_chats)

    except Exception as e:
        logger.error(f"❌ 이벤트({event_name}) 처리 중 에러: {e}")


# ================================================================= #
# 실행 구역
# ================================================================= #
def start_crawler():
    logger.info("🚀 luckyquizchat 실시간 웹소켓 크롤러 시작")
    
    # HTTP 헤더 설정 (Referer 필수)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        "Referer": "https://luckyquiz3.blogspot.com/"
    }

    while True:
        try:
            if not sio.connected:
                # 쿼리 파라미터 및 헤더 전달하며 접속
                sio.connect(
                    SOCKET_URL,
                    socketio_path="socket.io",
                    headers=headers,
                    transports=["websocket", "polling"],
                    wait_timeout=10
                )
                sio.wait()  # 프로세스가 종료되지 않고 계속 수신 대기
        except Exception as e:
            logger.error(f"❌ 소켓 서버 연결 중 예외 발생: {e}")
            time.sleep(5)  # 5초 후 재접속 시도


if __name__ == "__main__":
    start_crawler()
