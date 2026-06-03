import os
import requests
from bs4 import BeautifulSoup
import time
import re
import random

# ⚠️ [필수 수정] 본인의 정보로 채워 넣으세요!
TOKEN = os.environ.get('8461694962:AAG8A1pjQ5CGvmVPdP51LuVGQFI_JSLK_AI')
CHAT_ID = os.environ.get('839306219')
sent_posts = set()

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {'chat_id': CHAT_ID, 'text': text}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"❌ 텔레그램 발송 에러: {e}")

def get_detail_content(post_url):
    """실제 뽐뿌 상세 페이지의 태그를 추적하여 본문과 댓글을 파싱"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    try:
        response = requests.get(post_url, headers=headers)
        if response.status_code != 200:
            return "본문 페이지 접속 실패", []
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 1. [보정] 모바일 뽐뿌 실제 본문 컨텐츠 영역 감지 (.pic_bg 또는 .board-contents 등)
        content_div = soup.select_one('.board-contents, .pic_bg, .bbs_view_content, .cont')
        
        # 만약 특정 클래스가 안 잡히면 본문 글을 담는 상위 div 구조를 직접 추적
        if not content_div:
            content_div = soup.select_one('#mainContent')
            
        content_text = content_div.get_text().strip() if content_div else "본문 내용을 파싱할 수 없는 구조입니다."
        
        # 광고 및 불필요한 공백 정제
        content_text = re.sub(r'\n+', '\n', content_text)
        if len(content_text) > 250:
            content_text = content_text[:250] + "...(지면상 생략)"
            
        # 2. [보정] 모바일 뽐뿌 실제 댓글 내역 추적 (.comment_text 또는 .comment_memo)
        comment_elements = soup.select('.comment_memo, .comment_text, .comment-content, div[class*="comment_"]')
        comments = []
        for i, reply in enumerate(comment_elements):
            if i >= 5: 
                break
            reply_text = reply.get_text().strip()
            # 작성자 아이디나 날짜 등이 섞여서 지저분하게 나오는 것 방지
            reply_text = re.sub(r'\s+', ' ', reply_text)
            if reply_text and len(reply_text) > 1:
                comments.append(f"- {reply_text}")
                
        return content_text, comments
        
    except Exception as e:
        print(f"❌ 상세 페이지 분석 에러: {e}")
        return "본문 로딩 실패", []

def check_ppomppu_coupon():
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
            # 1. 태그 안의 raw 텍스트를 가져옵니다.
            raw_text = item.get_text().strip()
            if not raw_text:
                continue
                
            # 2. [핵심 보정] 엔터(\n)나 탭 문자를 기준으로 쪼개서 맨 첫 줄(진짜 제목)만 가져옵니다.
            # 뽐뿌 모바일은 제목 뒤의 시간, 조회수 등을 엔터나 공백으로 구분해 둡니다.
            lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
            if not lines:
                continue
            title_text = lines[0] # 첫 번째 줄이 무조건 진짜 제목입니다.
            
            # 3. 간혹 한 줄로 붙어 나오는 지저분한 여백이나 마무리를 깔끔하게 정리
            title_text = re.sub(r'\s+', ' ', title_text)
            
            if not title_text or len(title_text) < 3:
                continue
                
            # 설정한 핵심 키워드 감시 조건 (예: '쿠폰' 또는 '네이버페이')
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
                sent_posts.add(title_text)
                time.sleep(1.5)
                
    except Exception as e:
        print(f"❌ 크롤링 중 에러 발생: {e}")

if __name__ == "__main__":
    print("🕵️‍♂️ [Shadow_crawler_bot] 상시 감시 모드를 가동합니다.")
    
    while True:
        check_ppomppu_coupon()
        
        # 🎯 [변경 포인트] 30초 고정이 아니라, 지정한 범위 내에서 랜덤하게 초를 선택합니다.
        # 예: 20초에서 45초 사이의 정수를 무작위로 추출
        sleep_time = random.randint(20, 45)
        
        print(f"💤 보안을 위해 {sleep_time}초 동안 무작위 대기 후 다시 정찰합니다...\n")
        time.sleep(sleep_time)

