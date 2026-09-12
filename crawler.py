import os
import re
import time
import threading
import requests
import socketio
from bs4 import BeautifulSoup
from datetime import datetime

# ==========================================
# 1. 설정 정보 (환경변수에서 불러오기)
# ==========================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

BOT_NICKNAME = "희미한범고래"
DEVICE_ID = f"dev_killerwhale_{int(time.time() * 1000)}"

# ==========================================
# 2. 전역 상태 관리 (퀴즈 수집 전용)
# ==========================================
class QuizState:
    def __init__(self):
        self.is_active = False
        self.start_time = 0
        self.telegram_msg_id = None
        self.answer_counts = {}  # {"정답후보": 언급횟수}
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
        print(f"⚠️ [텔레그램 환경변수 미설정] 발송 스킵:\n{text}")
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

# 접두어 정규식 (긴 패턴 우선 매칭)
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

    # 1) '정답', '토퀴' 등의 접두어가 붙은 경우
    match = PREFIX_PATTERN.search(text)
    if match:
        extracted = text[match.end():].strip()
        # 접두어 뒤 첫 단어/문장 추출 (줄바꿈 기준 첫 라인)
        extracted = extracted.split('\n')[0].strip()
        if extracted and len(extracted) <= 10:
            return extracted

    # 2) 접두어 없이 정답만 언급한 경우 (단독 메시지 10글자 이하)
    # 일반 대화(예: "아닌듯", "ㅋㅋ") 방지를 위해 ㅋ,ㅎ,ㅠ,ㅜ 단독 및 URL 스킵
    if len(text) <= 10 and not text.startswith("http") and not re.match(r'^(ㅋ|ㅎ|ㅠ|ㅜ)+$', text):
        return text

    return None

# ==========================================
# 5. 5분 타이머 및 텔레그램 렌더링
# ==========================================
def render_quiz_message(status_header="🚨 [토스 퀴즈 발생!]"):
    """집계된 정답 리스트 텔레그램 텍스트 생성"""
    with quiz_state.lock:
        sorted_answers = sorted(quiz_state.answer_counts.items(), key=lambda x: x[1], reverse=True)
        
        lines = [f"<b>{status_header}</b>\n"]
        if not sorted_answers:
            lines.append("⏳ <i>정답 수집 중... (제보 대기)</i>")
        else:
            lines.append("<b>📊 실시간 집계된 정답 후보:</b>")
            for ans, count in sorted_answers[:7]:  # 상위 7개 표시
                lines.append(f"• <b>{ans}</b> ({count}회 언급)")
        
        lines.append(f"\n⏱️ 수집 시작: {datetime.fromtimestamp(quiz_state.start_time).strftime('%H:%M:%S')}")
        return "\n".join(lines)

def finish_quiz_collection():
    """5분 만료 시 수집 종료"""
    with quiz_state.lock:
        quiz_state.is_active = False
        msg_id = quiz_state.telegram_msg_id
    
    final_text = render_quiz_message("✅ [토스 퀴즈 수집 마감 (5분 경과)]")
    edit_telegram_msg(msg_id, final_text)
    print("⏰ [타이머 마감] 5분 퀴즈 정답 수집이 종료되었습니다.")

# ==========================================
# 6. Socket.IO 이벤트 핸들러
# ==========================================
@sio.event
def connect():
    print(f"✅ [소켓 연결 완료] '{BOT_NICKNAME}' 로 참가 등록(join) 전송...")
    sio.emit("join", {"nick": BOT_NICKNAME, "deviceId": DEVICE_ID})
    print("🚀 [모니터링 시작] 퀴즈 키워드(토퀴/토스 퀴즈) 수신 대기 중...\n" + "="*50)

@sio.on("new_msg")
def on_new_message(data):
    if not isinstance(data, dict):
        return
        
    nick = data.get("u") or data.get("nick") or "익명"
    content = clean_html(data.get("c") or data.get("rawContent") or "")
    time_str = format_time(data.get("t"))
    
    # 터미널 실시간 출력
    print(f"💬 [{time_str}] {nick}: {content}")

    # --------------------------------------------------
    # 로직 1: 신규 퀴즈 감지 (토퀴 / 토스 퀴즈 / 토스퀴즈)
    # --------------------------------------------------
    is_quiz_trigger = any(kw in content for kw in ["토퀴", "토스 퀴즈", "토스퀴즈"])
    
    if is_quiz_trigger:
        with quiz_state.lock:
            now = time.time()
            # 마지막 퀴즈 감지 후 3분 이내 연속 키워드는 동일 퀴즈로 간주해 알림 재발송 방지
            if not quiz_state.is_active or (now - quiz_state.start_time > 180):
                if quiz_state.timer:
                    quiz_state.timer.cancel()
                
                quiz_state.is_active = True
                quiz_state.start_time = now
                quiz_state.answer_counts.clear()
                
                # 텔레그램 신규 메시지 발송
                initial_text = f"🚨 <b>[토스 퀴즈 감지!]</b>\n<b>제보:</b> {nick}\n<b>내용:</b> {content}\n\n⏳ <i>5분간 정답 수집을 시작합니다...</i>"
                quiz_state.telegram_msg_id = send_telegram_msg(initial_text)
                
                # 5분(300초) 타이머 설정
                quiz_state.timer = threading.Timer(300.0, finish_quiz_collection)
                quiz_state.timer.start()
                
                print(f"🚨 [퀴즈 트리거 감지] 5분 정답 수집 모드 진입 (Msg ID: {quiz_state.telegram_msg_id})")

    # --------------------------------------------------
    # 로직 2: 5분 수집 기간 동안 정답 추출 및 텔레그램 수정
    # --------------------------------------------------
    with quiz_state.lock:
        is_active = quiz_state.is_active
        msg_id = quiz_state.telegram_msg_id

    if is_active:
        candidate = extract_answer(content)
        if candidate:
            with quiz_state.lock:
                quiz_state.answer_counts[candidate] = quiz_state.answer_counts.get(candidate, 0) + 1
                updated_text = render_quiz_message()
            
            # 실시간 텔레그램 메시지 수정
            edit_telegram_msg(msg_id, updated_text)
            print(f"🎯 [정답 후보 감지] '{candidate}' (총 {quiz_state.answer_counts[candidate]}회)")

# 기타 소켓 이벤트 생략 처리
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
    # 환경변수 설정 여부 체크 안내
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("💡 [안내] TELEGRAM_TOKEN 또는 TELEGRAM_CHAT_ID 환경변수가 설정되지 않았습니다. 터미널 출력 전용 모드로 동작합니다.")

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
