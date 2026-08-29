import os
import random
import time
from bs4 import BeautifulSoup
from curl_cffi import requests
from dotenv import load_dotenv

# 환경변수 로드 (.env 파일에서 TELEGRAM_TOKEN, TELEGRAM_CHAT_ID 읽기)
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TARGET_URL = "https://m.ppomppu.co.kr/new/bbs_list.php?id=coupon"
BASE_URL = "https://m.ppomppu.co.kr/new/"

# 이전에 발송한 글의 ID를 저장할 집합
seen_post_ids = set()

# 최근에 성공했던 프록시를 기억하기 위한 변수
working_proxy = None


def send_telegram_message(message):
    """텔레그램 알림 발송 함수"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[경고] 텔레그램 토큰 또는 Chat ID가 설정되지 않았습니다.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"[텔레그램 발송 실패] {e}")


def fetch_free_proxies():
    """ProxyScrape API에서 무료 HTTP 프록시 목록 수집"""
    api_url = "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=3000&country=all&ssl=all&anonymity=all"
    try:
        r = requests.get(api_url, timeout=5)
        if r.status_code == 200:
            proxies = [
                p.strip() for p in r.text.strip().split("\r\n") if p.strip()
            ]
            return proxies
    except Exception as e:
        print(f"[프록시 리스트 수집 실패] {e}")
    return []


def get_html_with_proxy(url):
    """무료 프록시를 순환하며 200 OK 응답을 얻을 때까지 요청 시도"""
    global working_proxy

    # 1. 이전 실행에서 성공했던 프록시가 있다면 우선 시도
    if working_proxy:
        try:
            session = requests.Session(impersonate="chrome120")
            session.proxies = {
                "http": working_proxy,
                "https": working_proxy,
            }
            res = session.get(url, timeout=4)
            if res.status_code == 200:
                return res.text
        except Exception:
            working_proxy = None  # 이전 프록시가 죽었으면 초기화

    # 2. 새로운 무료 프록시 리스트 가져오기
    proxies = fetch_free_proxies()
    if not proxies:
        print("[오류] 사용 가능한 프록시 목록을 가져올 수 없습니다.")
        return None

    random.shuffle(proxies)

    # 3. 최대 15개 프록시까지 순차 테스트
    for proxy in proxies[:15]:
        proxy_url = f"http://{proxy}"
        try:
            session = requests.Session(impersonate="chrome120")
            session.proxies = {"http": proxy_url, "https": proxy_url}

            res = session.get(url, timeout=4)

            # 200 OK로 성공했을 때
            if res.status_code == 200:
                print(f"[성공] 프록시 우회 성공: {proxy_url}")
                working_proxy = proxy_url  # 성공한 프록시 저장
                return res.text
            elif res.status_code == 403:
                print(f"[차단] 403 Forbidden ({proxy_url}) -> 다음 프록시 시도")

        except Exception:
            # 타임아웃이나 연결 끊김 시 조용히 넘어감
            pass

    print("[실패] 시도한 모든 프록시가 차단되었거나 응답이 없습니다.")
    return None


def parse_and_notify(html):
    """뽐뿌 게재글 파싱 및 신규 글 알림 텔레그램 전송"""
    soup = BeautifulSoup(html, "html.parser")

    # 뽐뿌 모바일 게시판 리스트 파싱
    posts = soup.select("ul.bbsList > li")

    new_posts_count = 0

    for post in reversed(posts):  # 과거 글부터 처리
        try:
            # 공지사항이나 카테고리 태그 예외 처리
            title_tag = post.select_one("span.title")
            link_tag = post.select_one("a")

            if not title_tag or not link_tag:
                continue

            title = title_tag.get_text(strip=True)
            link = BASE_URL + link_tag["href"]

            # URL에서 게시글 고유 ID 추출 (예: no=123456)
            post_id = link.split("no=")[-1].split("&")[0]

            # 이미 확인한 글이면 건너뜀
            if post_id in seen_post_ids:
                continue

            # 최초 실행 시에는 기존 글들을 '이미 본 글'로 등록만 하고 알림은 쏘지 않음
            if len(seen_post_ids) == 0:
                seen_post_ids.add(post_id)
                continue

            # 신규 글 등록 및 알림 전송
            seen_post_ids.add(post_id)
            new_posts_count += 1

            msg = f"<b>[뽐뿌 쿠폰 알림]</b>\n\n{title}\n\n<a href='{link}'>게시글 바로가기</a>"
            send_telegram_message(msg)
            print(f"[알림 발송] {title}")

        except Exception as e:
            print(f"[파싱 에러] {e}")

    if new_posts_count > 0:
        print(f"총 {new_posts_count}개의 새로운 알림을 보냈습니다.")


def main():
    print("=== 뽐뿌 크롤러 시작 (GCP 프록시 우회 모드) ===")

    # 1. 첫 실행 시 게시판을 읽어와서 기존 글 ID들을 초기 세팅
    print("초기 게시글 목록을 수집합니다...")
    html = get_html_with_proxy(TARGET_URL)
    if html:
        parse_and_notify(html)
        print(f"초기 세팅 완료 (기존 글 {len(seen_post_ids)}개 등록됨)")

    # 2. 루프를 돌며 주기적으로 크롤링 (예: 30초 간격)
    while True:
        try:
            time.sleep(30)
            print("\n[주기적 수집 시도]")
            html = get_html_with_proxy(TARGET_URL)
            if html:
                parse_and_notify(html)
        except KeyboardInterrupt:
            print("\n크롤러를 종료합니다.")
            break
        except Exception as e:
            print(f"[메인 루프 에러] {e}")


if __name__ == "__main__":
    main()
