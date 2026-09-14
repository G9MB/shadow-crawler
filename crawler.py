import time
import socketio
from bs4 import BeautifulSoup
from datetime import datetime

# 닉네임 및 고유 디바이스 ID 설정
BOT_NICKNAME = "셰도우링크"
DEVICE_ID = f"dev_shadowlink_{int(time.time() * 1000)}"

sio = socketio.Client(logger=False, engineio_logger=False)

def clean_html(text):
    if not text:
        return ""
    return BeautifulSoup(text, "html.parser").get_text()

def format_time(ts):
    if not ts:
        return datetime.now().strftime("%H:%M:%S")
    try:
        return datetime.fromtimestamp(ts / 1000.0).strftime("%H:%M:%S")
    except Exception:
        return str(ts)

def format_reactions(reactions_dict):
    if not isinstance(reactions_dict, dict) or not reactions_dict:
        return ""
    items = [f"{emoji}:{len(users)}" for emoji, users in reactions_dict.items() if users]
    return f" [{ ' '.join(items) }]" if items else ""

@sio.event
def connect():
    print("✅ [소켓 연결 성공] '셰도우링크'로 참가 등록(join)을 시작합니다...")
    
    # 서버로 핸드셰이크 전송
    join_data = {
        "nick": BOT_NICKNAME,
        "deviceId": DEVICE_ID
    }
    sio.emit("join", join_data)
    
    print(f"🚀 [핸드셰이크 완료] 닉네임: '{BOT_NICKNAME}' 로 실시간 수신 대기 중...\n" + "="*50)

@sio.event
def disconnect():
    print("❌ [소켓 연결 끊김]")

# 1. 실시간 새 메시지 수신 (new_msg)
@sio.on("new_msg")
def on_new_message(data):
    if not isinstance(data, dict):
        return
        
    nick = data.get("u") or data.get("nick") or "익명"
    content = clean_html(data.get("c") or data.get("rawContent") or "")
    time_str = format_time(data.get("t"))
    react_str = format_reactions(data.get("reactions"))
    
    print(f"💬 [{time_str}] {nick}: {content}{react_str}")

# 2. 메시지 삭제 수신 (message_deleted_status)
@sio.on("message_deleted_status")
def on_message_deleted(data):
    if isinstance(data, dict):
        content = clean_html(data.get("rawContent", ""))
        print(f"🗑️ [실시간 삭제] 원문: {content}")

# 3. 실시간 리액션 변경 수신 (update_reactions)
@sio.on("update_reactions")
def on_update_reactions(data):
    if isinstance(data, dict):
        msg_id = data.get("msgId")
        reactions = data.get("reactions", {})
        summary = format_reactions(reactions)
        print(f"👍 [리액션 변경] msg({msg_id}) ->{summary if summary else ' (해제됨)'}")

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
        sio.disconnect()
    except Exception as e:
        print(f"❌ 접속 오류 발생: {e}")