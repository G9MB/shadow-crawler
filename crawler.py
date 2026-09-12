import os
import re
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

# ==========================================
# 2. 전역 상태 관리
# ==========================================
class QuizState:
    def __init__(self):
        self.is_active = False
        self.start_time = 0
        self.telegram_msg_id = None
        self.last_sent_text = ""
        self.answers = []  # 순서대로 저장할 정답 리스트 (중복 제외)
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
    except Exception as e:
        print(f"⚠️ 텔레그램 발송 실패: {e}")
    return None

def edit_telegram_msg(msg_id, text):
    """기존 텔레그램 메시지 실시간 수정"""
    if not msg_id or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    with quiz_state.lock:
        if quiz_state.last_sent_text == text:
            return  # 내용 변화 없으면 API 호출하지 않음
        quiz_state.last_sent_text = text

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
# 4. 텍스트 정제 및 정답 검출 로직
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
    r'^(토스\s*퀴즈\s*정답|토스\s*정답|토퀴\s*정답|퀴즈\s*정답|퀴즈\s*답|토퀴\s*답|정답|토퀴|답)[:\s=\-]*',
    re.IGNORECASE
)

def extract_answer(content):
    """정답 후보 추출 로직"""
    if not content:
        return None

    # 인사말/감사 표현 포함 시 예외 없이 통째로 스킵
    if any(keyword in content for keyword in ["감사", "고맙"]):
        return None

    text = content.strip()

    # 1) 접두어가 붙은 경우 ("정답 1234", "토퀴 5678" 등)
    match = PREFIX_PATTERN.search(text)
    if match:
        extracted = text[match.end():].strip()
        extracted = extracted.split('\n')[0].strip()
        if extracted and len(extracted) <= 10:
            return extracted

    # 2) 접두어 없이 단독 언급 (10자 이하)
    if len(text) <= 10 and not text.startswith("http") and not re.match(r'^(ㅋ|ㅎ|ㅠ|ㅜ)+$', text):
        return text

    return None

# ==========================================
# 5. 실시간 텔레그램 렌더링 및 마감 처리
# ==========================================
def render_quiz_message(status_header="🚨 <b>[토스 퀴즈 감지!]</b>"):
    """수집된 정답 목록 텔레그램 텍스트 생성"""
    with quiz_state.lock:
        lines = [f"{status_header}\n"]
        
        if not quiz_state.answers:
            lines.append("⏳ <i>정답 수집 중... (제보 대기)</i>")
        else:
            lines.append("<b>📝 올라온 정답 목록:</b>")
            for ans in quiz_state.answers:
                lines.append(f"• <b>{ans}</b>")
        
        start_str = datetime.fromtimestamp(quiz_state.start_time).strftime('%H:%M:%S')
        lines.append(f"\n⏱️ 수집 시작: {start_str} (5분간 자동 업데이트)")
        return "\n".join(lines)

def finish_quiz_collection():
    """5분 만료 시 수집 종료"""
    with quiz_state.lock:
        quiz_state.is_active = False
        msg_id = quiz_state.telegram_msg_id
    
    final_text = render_quiz_message("✅ <b>[토스 퀴즈 수집 마감 (5분 경과)]</b>")
    edit_telegram_msg(msg_id, final_text)
    print("⏰ [타이머 마감] 5분 퀴즈 정답 수집이 마감되었습니다.")

# ==========================================
# 6. Socket.IO 이벤트 핸들러
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
    
    # 터미널 실시간 출력
    print(f"💬 [{time_str}] {nick}: {content}")

    # 1. 퀴즈 트리거 감지 (토퀴 / 토스 퀴즈 / 토스퀴즈)
    is_quiz_trigger = any(kw in content for kw in ["토퀴", "토스 퀴즈", "토스퀴즈"])
    
    if is_quiz_trigger:
        with quiz_state.lock:
            now = time.time()
            # 3분 이내 연속 키워드는 동일 퀴즈로 처리
            if not quiz_state.is_active or (now - quiz_state.start_time > 180):
                if quiz_state.timer:
                    quiz_state.timer.cancel()
                
                quiz_state.is_active = True
                quiz_state.start_time = now
                quiz_state.answers.clear()
                quiz_state.last_sent_text = ""
                
                initial_text = render_quiz_message("🚨 <b>[토스 퀴즈 감지!]</b>")
                quiz_state.telegram_msg_id = send_telegram_msg(initial_text)
                quiz_state.last_sent_text = initial_text
                
                # 5분(300초) 타이머 시작
                quiz_state.timer = threading.Timer(300.0, finish_quiz_collection)
                quiz_state.timer.start()
                
                print(f"🚨 [퀴즈 감지] 5분 정답 수집 시작 (Msg ID: {quiz_state.telegram_msg_id})")

    # 2. 5분 동안 채팅창에 새 정답이 올라오면 리스트에 추가 후 텔레그램 수정
    with quiz_state.lock:
        is_active = quiz_state.is_active
        msg_id = quiz_state.telegram_msg_id

    if is_active:
        candidate = extract_answer(content)
        if candidate:
            with quiz_state.lock:
                # 새로운 정답인 경우에만 추가 (중복 방지)
                if candidate not in quiz_state.answers:
                    quiz_state.answers.append(candidate)
                    updated_text = render_quiz_message()
                    edit_telegram_msg(msg_id, updated_text)
                    print(f"🎯 [새 정답 추가] '{candidate}'")

@sio.on("*")
def catch_all(event_name, *args):
    pass

# ==========================================
# 7. 실행부
# ==========================================
headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/138.0.0.0",
    "Referer": "https://luckyquiz3.blogspot.com/"
}

if __name__ == "__main__":
    try:
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
