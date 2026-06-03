import os
import requests
from bs4 import BeautifulSoup
import time
import re
import random

# 🎯 환경변수 이름 일치 (대소문자 및 철자 수정)
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

DB_FILE = "sent_posts.txt"

def load_sent_posts():
    """파일에서 이미 발송한 게시글 제목 목록을 읽어옵니다."""
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_sent_post(title):
    """새로 발송한 게시글 제목을 파일에 기록합니다."""
    with open(DB_FILE, "a", encoding="utf-8") as f:
        f.write(title + "\n")

def send_telegram_message(text):
    if not TOKEN or not CHAT_ID:
        print("❌ 텔레그램 토큰 또는 CHAT_ID가 설정되지 않았습니다.")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {'chat_id': CHAT_ID, 'text': text}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"❌ 텔레그램 발송 에러: {e}")

def get_detail_content(post_url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        response = requests.get(post_url, headers=headers)
        if response.status_code != 200:
            return "본문 페이지 접속 실패", []
            
        soup = BeautifulSoup(response.text, 'html.parser')
        content_div = soup.select_one('.board-contents, .pic_bg, .bbs_view_content, .cont')
        
        if not content_div:
            content_div = soup.select_one('#mainContent')
            
        content_text = content_div.get_text().strip() if content_div else "본문 내용을 파싱할 수 없는 구조입니다."
        content_text = re.sub(r'\n+', '\n', content_text)
        if len(content_text) > 250:
            content_text = content_text[:250] + "...(지면상 생략)"
            
        comment_elements = soup.select('.comment_memo, .comment_text, .comment-content, div[class*="comment_"]')
        comments = []
        for i, reply in enumerate(comment_elements):
            if i >= 5: 
                break
            reply_text = reply.get_text().strip()
            reply_text = re.sub(r'\s+', ' ', reply_text)
            if reply_text and len(reply_text) > 1:
                comments.append(f"- {reply_text}")
                
        return content_text, comments
    except Exception as e:
        print(f"❌ 상세 페이지 분석 에러: {e}")
        return "본문 로딩 실패", []

def check_ppomppu_coupon():
    # 실행할 때마다 저장된 파일에서 기존 발송 목록을 불러옵니다.
    sent_posts = load_sent_posts()
    
    url = "https://m.ppomppu.co.kr/new/bbs_list.php?id=coupon&extref=1"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            print(f"❌ 뽐뿌 서버 접속 실패")
            return
            
        soup = BeautifulSoup(response.text, 'html.parser')
        titles = soup.select('a.title_a, a.list_title, span.title, td.title a')
        
        if len(titles) == 0:
            titles = [a for a in soup.find_all('a') if 'bbs_view.php' in a.get('href', '')]
            
        print(f"🔄 현재 시간 {time.strftime('%Y-%m-%d %H:%M:%S')} - 게시글 {len(titles)}개 스캔 중...")
        
        for item in titles:
            raw_text = item.get_text().strip()
            if not raw_text:
                continue
                
            lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
            if not lines:
                continue
            title_text = lines[0]
            title_text = re.sub(r'\s+', ' ', title_text)
            
            if not title_text or len(title_text) < 3:
                continue
                
            # 복합 조건 필터링 (토스+퀴즈 OR 네이버+180)
            if ("토스" in title_text and "퀴즈" in title_text) or ("네이버" in title_text and "180" in title_text):
                
                if title_text in sent_posts:
                    continue
                    
                raw_href = item.get('href', '')
                if not raw_href:
                    continue
                
                no_match = re.search(r'no=(\d+)', raw_href)
                if no_match:
                    post_no = no_match.group(1)
                    post_url = f"https://m.ppomppu.co.kr/new/bbs_view.php?id=coupon&no={post_no}"
                else:
                    post_url = "https://m.ppomppu.co.kr/new/" + raw_href if not raw_href.startswith('http') else raw_href
                
                content, comments_list = get_detail_content(post_url)
                comments_str = "\n".join(comments_list) if comments_list else "등록된 댓글이 없습니다."
                
                alert_msg = (
                    f"🚨 [Shadow_crawler_bot] 조건 발견!\n\n"
                    f"📌 제목: {title_text}\n"
                    f"🔗 링크: {post_url}\n\n"
                    f"📝 [글 내용]\n{content}\n\n"
                    f"💬 [최신 댓글 요약]\n{comments_str}"
                )
                
                send_telegram_message(alert_msg)
                save_sent_post(title_text)  # 🎯 중복 차단을 위해 영구 파일에 저장
                time.sleep(1.5)
                
    except Exception as e:
        print(f"❌ 크롤링 중 에러 발생: {e}")

# 🎯 무한 루프 없이 깃허브가 깨워주면 25~45초 간격으로 '딱 6번' 스캔하고 종료
if __name__ == "__main__":
    print("🚀 [GitHub Actions] 정각/30분 트리거 발동. 6회 집중 정찰을 시작합니다.")
    
    # 1부터 6까지 정확히 6번 반복 실행하도록 변경
    for attempt in range(1, 7):
        print(f"🕵️‍♂️ [{attempt}/6 번째 정찰 수행 중...]")
        check_ppomppu_coupon()
        
        # 6번째 마지막 크롤링을 마쳤다면 더 이상 대기할 필요가 없으므로 루프 탈출
        if attempt == 6:
            break
            
        # 🎯 [변경 포인트] 다음 스캔까지 25초에서 45초 사이의 무작위 초 선택
        next_sleep = random.randint(25, 45)
        print(f"⏳ 보안 우회 및 밀착 감시를 위해 {next_sleep}초 대기 후 다음 스캔...")
        time.sleep(next_sleep)
        
    print("✅ 6회 집중 밀착 정찰 완료. 가상 서버를 안전하게 종료합니다.")

