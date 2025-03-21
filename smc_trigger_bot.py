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

def is_market_open():
    # 현재 한국 시간 확인
    now = get_kst_time()
    current_hour = now.hour
    current_minute = now.minute

    # 한국 주식 시장(KRX) 장 열림 시간: 오전 9시 ~ 오후 3시 30분
    market_open_hour = 9
    market_close_hour = 15  # 오후 3시
    market_close_minute = 30

    # 장이 열리는 시간인지 확인
    if current_hour < market_open_hour or (current_hour > market_close_hour or (current_hour == market_close_hour and current_minute > market_close_minute)):
        return False  # 장이 닫혀 있음
    return True  # 장이 열려 있음

def wait_until_next_4hour_candle():
    now = get_kst_time()
    # 4시간봉 기준: 0시, 4시, 8시, 12시, 16시, 20시
    # 하지만 장 시간(오전 9시 ~ 오후 3시 30분)에 맞춰 조정
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
    # 인덱스를 datetime으로 변환하고 KST 타임존으로 설정
    df.index = pd.to_datetime(df.index).tz_convert('Asia/Seoul')
    # 기준 시간도 KST 타임존으로 설정
    kst = timezone(timedelta(hours=9))
    origin_time = pd.Timestamp('2025-03-19 09:00:00', tz='Asia/Seoul')
    # 4시간봉으로 리샘플링 (오전 9시 기준)
    df_4h = df.resample('4h', origin=origin_time, closed='right').agg({
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

    # 종목 수 확인
    print(f"총 종목 수: {len(stock_codes)}")

    # 종목을 50개씩 나눠서 처리
    batch_size = 50
    total_batches = (len(stock_codes) + batch_size - 1) // batch_size  # 총 배치 수 계산
    batch_interval = 4 * 60 * 60 / total_batches  # 4시간(14400초)을 배치 수로 나눔
    print(f"총 배치 수: {total_batches}, 배치 간 대기 시간: {batch_interval}초")

    while True:
        # SMC 트리거 봇 시작 메시지 전송
        message = "SMC 트리거 봇 시작! (4시간봉 기준, 한국 시장 시간)"
        send_message(message)
        print(message)

        # 트리거 결과를 저장할 리스트
        triggers = []

        # 4시간 동안 50개씩 종목 처리 (장이 닫혀 있어도 분석은 진행)
        for batch_idx in range(total_batches):
            start_idx = batch_idx * batch_size
            end_idx = min((batch_idx + 1) * batch_size, len(stock_codes))
            batch_codes = stock_codes[start_idx:end_idx]

            print(f"배치 {batch_idx + 1}/{total_batches} 처리 중... 종목 수: {len(batch_codes)}")
            for code in batch_codes:
                try:
                    candles = get_4h_data(code)
                    if candles is None or len(candles) < 5:
                        continue

                    order_blocks = find_order_blocks(candles)
                    if order_blocks:
                        trigger = check_triggers(candles, order_blocks[-1])  # 가장 최근 OB 체크
                        if trigger:
                            action = "매수" if trigger['type'] == "Buy" else "매도"
                            trigger_message = f"트리거 감지! 종목: {code}, {action}, Price: {trigger['price']}, Stop: {trigger['stop']}"
                            triggers.append(trigger_message)
                            print(trigger_message)

                except Exception as e:
                    print(f"오류 발생: {code}, {e}")

            # 다음 배치까지 대기 (4시간을 배치 수로 나눈 시간만큼 대기)
            if batch_idx < total_batches - 1:  # 마지막 배치가 아니면 대기
                print(f"다음 배치까지 대기: {batch_interval}초")
                time.sleep(batch_interval)

        # 4시간봉 캔들 완성 시점에 트리거 결과 전송 (장이 열려 있을 때만)
        if is_market_open():
            if triggers:
                # 모든 트리거 메시지를 한 번에 전송
                combined_message = "\n".join(triggers)
                send_message(combined_message)
                print("트리거 결과 전송 완료")
            else:
                send_message("이번 캔들에서 트리거가 감지되지 않았습니다.")
                print("트리거 없음")
        else:
            print("장이 닫혀 있어 트리거 결과를 전송하지 않습니다.")

        # 다음 4시간봉 캔들까지 대기
        wait_seconds = wait_until_next_4hour_candle()
        print(f"다음 4시간봉 캔들 완성까지 대기: {wait_seconds}초")
        time.sleep(wait_seconds)

if __name__ == "__main__":
    main()
