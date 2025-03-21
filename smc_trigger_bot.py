import requests
import time
import os
from datetime import datetime, timezone, timedelta

# 환경 변수에서 TELEGRAM_TOKEN과 TELEGRAM_CHAT_ID 가져오기
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
BASE_URL = "https://api.telegram.org/bot{}/sendMessage"

def send_message(text):
    url = BASE_URL.format(TOKEN)
    params = {
        "chat_id": CHAT_ID,
        "text": text
    }
    response = requests.get(url, params=params)
    return response.json()

def get_kst_time():
    # 한국 시간(KST) = UTC + 9시간
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst)

def wait_until_next_4hour_candle():
    now = get_kst_time()
    # 4시간봉 기준: 0시, 4시, 8시, 12시, 16시, 20시
    current_hour = now.hour
    next_candle_hour = ((current_hour // 4) + 1) * 4
    if next_candle_hour == 24:
        next_candle_hour = 0
        next_day = now + timedelta(days=1)
        next_candle = datetime(next_day.year, next_day.month, next_day.day, next_candle_hour, 0, 0, tzinfo=now.tzinfo)
    else:
        next_candle = datetime(now.year, now.month, now.day, next_candle_hour, 0, 0, tzinfo=now.tzinfo)
    
    wait_seconds = (next_candle - now).total_seconds()
    return wait_seconds

def main():
    while True:
        # SMC 트리거 봇 시작 메시지 전송
        message = "SMC 트리거 봇 시작! (4시간봉 기준, 한국 시장 시간)"
        send_message(message)
        print(message)

        # 다음 4시간봉 캔들까지 대기
        wait_seconds = wait_until_next_4hour_candle()
        print(f"다음 4시간봉 캔들 완성까지 대기: {wait_seconds}초")
        time.sleep(wait_seconds)

if __name__ == "__main__":
    main()
