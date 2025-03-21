import requests
import time
import os
import yfinance as yf
import pandas as pd
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

# 4시간봉 데이터 가져오기 (1시간봉 데이터를 리샘플링)
def get_4h_data(stock_code):
    stock = yf.Ticker(f"{stock_code}.KS")
    # 최근 14일간의 1시간봉 데이터 가져오기
    df = stock.history(period="14d", interval="1h")
    if df.empty:
        return None
    # 인덱스를 datetime으로 변환
    df.index = pd.to_datetime(df.index)
    # 4시간봉으로 리샘플링 (오전 9시 기준)
    df_4h = df.resample('4H', origin=pd.Timestamp('2025-03-19 09:00:00'), closed='right').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum'
    }).dropna()
    return df_4h

# HH, HL, LL, LH 패턴 감지
def detect_patterns(candles):
    highs = candles['High'].values
    lows = candles['Low'].values
    patterns = []

    for i in range(1, len(candles)):
        pattern = []
        if highs[i] > highs[i-1]:
            pattern.append("HH")
        elif highs[i] < highs[i-1]:
            pattern.append("LH")
        if lows[i] > lows[i-1]:
            pattern.append("HL")
        elif lows[i] < lows[i-1]:
            pattern.append("LL")
        patterns.append(pattern)
    return patterns

# OB 감지 (MSB/CHOCH 기반)
def find_order_blocks(candles):
    patterns = detect_patterns(candles)
    order_blocks = []

    for i in range(2, len(patterns)):
        # Demand OB: HH 직전 HL
        if "HH" in patterns[i] and "HL" in patterns[i-1]:
            order_blocks.append({
                "type": "Demand",
                "index": i-1,
                "open": candles['Open'].iloc[i-1],
                "low": candles['Low'].iloc[i-1]
            })
        # Supply OB: LL 직전 LH
        if "LL" in patterns[i] and "LH" in patterns[i-1]:
            order_blocks.append({
                "type": "Supply",
                "index": i-1,
                "open": candles['Open'].iloc[i-1],
                "high": candles['High'].iloc[i-1]
            })
    return order_blocks

# FVG 계산 (3캔들 공백)
def detect_fvg(candles, index):
    if index < 2 or index >= len(candles) - 1:
        return None
    c1 = candles.iloc[index-2]
    c2 = candles.iloc[index-1]
    c3 = candles.iloc[index]
    gap = c3['High'] - c1['Low']
    if gap > 0.01 * c1['Low']:  # 최소 1% 공백 가정
        return {
            "start": c1['Low'],
            "end": c3['High'],
            "type": "Bullish" if c2['Close'] > c1['Close'] else "Bearish"
        }
    return None

# 매수/매도 신호 확인
def check_triggers(candles, ob):
    patterns = detect_patterns(candles)
    fvg = detect_fvg(candles, ob['index'] + 1)  # OB 다음 캔들에서 FVG 체크
    current = candles.iloc[-1]  # 가장 최근 캔들

    if not fvg:
        return None

    # 매수 조건 (Demand OB 후 FVG 상단 터치, 저점 갱신 없음)
    if ob['type'] == "Demand" and fvg['type'] == "Bullish":
        recent_lows = candles['Low'].iloc[ob['index']+1:-1]
        if current['Low'] >= ob['low'] and current['High'] >= fvg['end']:  # FVG 상단 터치
            return {"type": "Buy", "price": fvg['end'], "stop": ob['open']}

    # 매도 조건 (Supply OB 후 FVG 하단 터치, 고점 갱신 없음)
    if ob['type'] == "Supply" and fvg['type'] == "Bearish":
        recent_highs = candles['High'].iloc[ob['index']+1:-1]
        if current['High'] <= ob['high'] and current['Low'] <= fvg['start']:  # FVG 하단 터치
            return {"type": "Sell", "price": fvg['start'], "stop": ob['open']}

    return None

def main():
    # stocks.txt에서 종목 코드 읽기
    with open("stocks.txt", "r", encoding="utf-8") as file:
        stock_codes = [line.strip() for line in file if line.strip()]

    while True:
        # SMC 트리거 봇 시작 메시지 전송
        message = "SMC 트리거 봇 시작! (4시간봉 기준, 한국 시장 시간)"
        send_message(message)
        print(message)

        # 다음 4시간봉 캔들까지 대기
        wait_seconds = wait_until_next_4hour_candle()
        print(f"다음 4시간봉 캔들 완성까지 대기: {wait_seconds}초")
        time.sleep(wait_seconds)

        # 4시간봉 캔들 완성 후 트리거 확인
        print(f"4시간봉 캔들 완성! 트리거 확인 시작...")
        for code in stock_codes:
            try:
                candles = get_4h_data(code)
                if candles is None or len(candles) < 5:
                    continue

                order_blocks = find_order_blocks(candles)
                if order_blocks:
                    trigger = check_triggers(candles, order_blocks[-1])  # 가장 최근 OB 체크
                    if trigger:
                        action = "매수" if trigger['type'] == "Buy" else "매도"
                        message = f"트리거 감지! 종목: {code}, {action}, Price: {trigger['price']}, Stop: {trigger['stop']}"
                        send_message(message)
                        print(message)

            except Exception as e:
                print(f"오류 발생: {code}, {e}")

if __name__ == "__main__":
    main()
