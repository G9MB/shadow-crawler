import os
import re
import sys
import time
import threading
import requests
import socketio
from bs4 import BeautifulSoup
from datetime import datetime

# ==========================================
# 1. 설정 정보 (환경변수)
# ==========================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

BOT_NICKNAME = "희미한범고래"
DEVICE_ID = f"dev_killerwhale_{int(time.time() * 1000)}"

# 프로그램 시작 시각 기록 (재시작 시간 확인용)
START_TIME_STR = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ==========================================
# 2. 전역 상태 관리
# ==========================================
class QuizState:
    def __init__(self):
        self.is_active = False
        self.start_time = 0
        self.telegram_msg_id = None
        self.last_sent_text = ""
        self.answers = []
        self.timer = None
        self.lock = threading.Lock()

quiz_state = QuizState()
sio = socketio.Client(logger=False, engineio_logger=False)

# ==========================================
# 3. 텔레그램 API 연동 함수
# ==========================================
def send_telegram_msg(text):
    """새 텔레그램 메시지 발송 (메시지 ID 반환)"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"⚠️ [텔레그램 미설정] 발송 스킵:\n{text}")
        return None
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        res = requests.post(url, json=payload, timeout=5).json()
        if res.get("ok"):
            return res["result"]["message_id"]
        else:
            print(f"⚠️ 텔레그램 발송 응답 실패: {res}")
    except Exception as e:
        print(f"⚠️ 텔레그램 발송 예외 발생: {e}")
    return None

def edit_telegram_msg(msg_id, text):
    """기존 텔레그램 메시지 실시간 수정"""
    if not msg_id or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "message_id": msg_id,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"⚠️ 텔레그램 수정 실패: {e}")

# ==========================================
# 4. 텔레그램 메시지 감지 (상태 확인 및 재시작)
# ==========================================
def restart_program():
    """크롤러 프로세스를 직접 셀프 재시작하는 함수"""
    print("🔄 [텔레그램 명령] 크롤러를 재시작합니다...")
    if quiz_state.timer:
        quiz_state.timer.cancel()
    try:
        if sio.connected:
            sio.disconnect()
    except Exception:
        pass
    
    time.sleep(1)
    # 현재 실행 중인 파이썬 프로세스를 자기 자신으로 새로 덮어씌워 재실행
    python = sys.executable
    os.execl(python, python, *sys.argv)

def poll_telegram_messages():
    """텔레그램에서 명령어가 들어오는지 확인 후 상태 응답 및 재시작 처리"""
    if not TELEGRAM_BOT_TOKEN:
        return

    last_update_id = 0
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"

    # 시작 시 과거 오프라인 메시지 무시
    try:
        res = requests.get(url, params={"timeout": 0, "offset": -1}, timeout=10).json()
        if res.get("ok") and res.get("result"):
            last_update_id = res["result"][-1]["update_id"]
    except Exception:
        pass

    while True:
        try:
            params = {"timeout": 10, "offset": last_update_id + 1}
            res = requests.get(url, params=params, timeout=15).json()

            if res.get("ok") and res.get("result"):
                for update in res["result"]:
                    last_update_id = update["update_id"]
                    msg = update.get("message") or update.get("channel_post")
                    
                    if not msg:
                        continue

                    text = msg.get("text", "").strip()

                    # 1. 텔레그램 상태 확인 명령어 (/ping, ping, !상태, 상태 등)
                    if text in ["/ping", "ping", "!상태", "!점검", "상태"]:
                        now_str = datetime.now().strftime("%H:%M:%S")
                        
                        if sio.connected:
                            status_msg = f"🟢 웹 채팅방 수신 정상 ({now_str})\n⏱️ 시작 일시: {START_TIME_STR}"
                        else:
                            status_msg = f"🔴 ⚠️ 웹 채팅방 연결 끊김 ({now_str})\n⏱️ 시작 일시: {START_TIME_STR}"
                            
                        send_telegram_msg(status_msg)
                        print(f"🔔 [상태 확인 수신] 응답 발송 완료")

                    # 2. 텔레그램 재시작 명령어 (/재시작, 재시작, !재시작)
                    elif text in ["/재시작", "재시작", "!재시작", "리셋"]:
                        send_telegram_msg("🔄 크롤러를 재시작합니다...")
                        print("🔔 [텔레그램 명령어] 재시작 요청 수신")
                        time.sleep(1)
                        restart_program()

        except Exception as e:
            time.sleep(3)

        time.sleep(1)

# ==========================================
# 5. 텍스트 정제 및 정답 검출 로직
# ==========================================
def clean_html(text):
    if not text:
        return ""
    return BeautifulSoup(text, "html.parser").get_text().strip()

def format_time(ts):
    if not ts:
        return datetime.now().strftime("%H:%M:%S")
    try:
        return datetime.fromtimestamp(ts / 1000.0).strftime("%H:%M:%S")
    except Exception:
        return str(ts)

PREFIX_PATTERN = re.compile(
    r'^(토스\s*퀴즈\s*정답|토스\s*정답|토퀴\s*정답|퀴즈\s*정답|퀴즈\s*답|토퀴\s*답|토스\s*퀴즈|토스퀴즈|토퀴|토스|정답|답)[:\s=\-]*',
    re.IGNORECASE
)

EXCLUDE_KEYWORDS = ["감사", "고맙", "있어요", "있음", "나옴", "시작", "안녕", "반가"]
EXCLUDE_EXACT = ["토퀴", "토스퀴즈", "토스 퀴즈", "토스", "정답", "답"]

def extract_answer(content):
    if not content:
        return None

    if any(keyword in content for keyword in EXCLUDE_KEYWORDS):
        return None

    text = content.strip()

    if text in EXCLUDE_EXACT:
        return None

    match = PREFIX_PATTERN.search(text)
    if match:
        extracted = text[match.end():].strip()
        extracted = extracted.split('\n')[0].strip()
        
        if extracted and len(extracted) <= 10 and extracted not in EXCLUDE_EXACT:
            return extracted
        return None

    if len(text) <= 10 and not text.startswith("http") and not re.match(r'^(ㅋ|ㅎ|ㅠ|ㅜ)+$', text):
        return text

    return None

# ==========================================
# 6. 실시간 텔레그램 렌더링 및 마감 처리
# ==========================================
def build_message_text(status_header, answers_list):
    lines = [status_header]
    if not answers_list:
        lines.append("⏳ <i>정답 수집 중... (제보 대기)</i>")
    else:
        for ans in answers_list:
            lines.append(f"• <b>{ans}</b>")
    
    return "\n".join(lines)

def finish_quiz_collection():
    with quiz_state.lock:
        quiz_state.is_active = False
        msg_id = quiz_state.telegram_msg_id
        answers = list(quiz_state.answers)
    
    final_text = build_message_text("✅ <b>[토스 퀴즈 수집 마감]</b>", answers)
    edit_telegram_msg(msg_id, final_text)
    print("⏰ [타이머 마감] 5분 퀴즈 정답 수집이 마감되었습니다.")

# ==========================================
# 7. Socket.IO 이벤트 핸들러
# ==========================================
@sio.event
def connect():
    print(f"✅ [소켓 연결 완료] '{BOT_NICKNAME}' 로 참가 등록(join) 전송...")
    sio.emit("join", {"nick": BOT_NICKNAME, "deviceId": DEVICE_ID})
    print("🚀 [모니터링 시작] 퀴즈 키워드 수신 대기 중...\n" + "="*50)

@sio.on("new_msg")
def on_new_message(data):
    if not isinstance(data, dict):
        return
        
    nick = data.get("u") or data.get("nick") or "익명"
    content = clean_html(data.get("c") or data.get("rawContent") or "")
    time_str = format_time(data.get("t"))
    
    print(f"💬 [{time_str}] {nick}: {content}")

    if any(kw in content for kw in ["감사", "고맙"]):
        return

    is_quiz_trigger = any(kw in content for kw in ["토퀴", "토스 퀴즈", "토스퀴즈"])
    
    if is_quiz_trigger:
        should_start = False
        now = time.time()

        with quiz_state.lock:
            if not quiz_state.is_active:
                if quiz_state.timer:
                    quiz_state.timer.cancel()
                
                quiz_state.is_active = True
                quiz_state.start_time = now
                quiz_state.answers.clear()
                quiz_state.last_sent_text = ""
                should_start = True

        if should_start:
            initial_ans = extract_answer(content)
            if initial_ans:
                quiz_state.answers.append(initial_ans)

            initial_text = build_message_text("🚨 <b>[토스 퀴즈 감지!]</b>", quiz_state.answers)
            msg_id = send_telegram_msg(initial_text)
            
            with quiz_state.lock:
                quiz_state.telegram_msg_id = msg_id
                quiz_state.last_sent_text = initial_text
                
                quiz_state.timer = threading.Timer(300.0, finish_quiz_collection)
                quiz_state.timer.start()
                
            print(f"🚨 [퀴즈 감지] 5분 정답 수집 시작 (Msg ID: {msg_id})")
            return

    with quiz_state.lock:
        is_active = quiz_state.is_active
        msg_id = quiz_state.telegram_msg_id

    if is_active:
        candidate = extract_answer(content)
        if candidate:
            should_update = False
            updated_text = ""

            with quiz_state.lock:
                if candidate not in quiz_state.answers:
                    quiz_state.answers.append(candidate)
                    updated_text = build_message_text("🚨 <b>[토스 퀴즈 감지!]</b>", quiz_state.answers)
                    should_update = True

            if should_update and msg_id:
                edit_telegram_msg(msg_id, updated_text)
                print(f"🎯 [새 정답 추가 및 텔레그램 수정] '{candidate}'")

@sio.on("*")
def catch_all(event_name, *args):
    pass

# ==========================================
# 8. 실행부
# ==========================================
headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/138.0.0.0",
    "Referer": "https://luckyquiz3.blogspot.com/"
}

if __name__ == "__main__":
    try:
        # 텔레그램 메시지 감지 스레드 시작
        tg_thread = threading.Thread(target=poll_telegram_messages, daemon=True)
        tg_thread.start()

        sio.connect(
            "https://luckyquizchat.duckdns.org",
            socketio_path="socket.io",
            headers=headers,
            transports=["websocket", "polling"]
        )
        sio.wait()
    except KeyboardInterrupt:
        print("\n프로그램을 종료합니다.")
        if quiz_state.timer:
            quiz_state.timer.cancel()
        sio.disconnect()
    except Exception as e:
        print(f"❌ 접속 오류: {e}")
