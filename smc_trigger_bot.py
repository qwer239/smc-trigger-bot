import yfinance as yf
import requests
import time
import pandas as pd
from datetime import datetime, timedelta
import pytz

# 텔레그램 설정
TOKEN = "7817223183:AAE32NZpN_RtwWN0foVG5HEmrzR1JS-PhQY"
CHAT_ID = "1075148573"
BASE_URL = "https://api.telegram.org/bot{}/sendMessage"

def send_telegram_message(message):
    url = BASE_URL.format(TOKEN)
    payload = {"chat_id": CHAT_ID, "text": message}
    response = requests.post(url, data=payload)
    return response.json()

# 4시간봉 데이터 가져오기 (1시간봉 데이터를 리샘플링)
def get_4h_data(stock_code):
    stock = yf.Ticker(f"{stock_code}.KS")
    df = stock.history(period="14d", interval="1h")
    if df.empty:
        print(f"{stock_code}: 데이터 없음")
        return None
    df.index = pd.to_datetime(df.index).tz_convert('Asia/Seoul')
    df_4h = df.resample('4H', origin=pd.Timestamp('2025-03-19 09:00:00', tz='Asia/Seoul'), closed='right').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum'
    }).dropna()
    print(f"{stock_code}: 4시간봉 데이터 가져옴, 최근 캔들: {df_4h.index[-1]}")
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
        if "HH" in patterns[i] and "HL" in patterns[i-1]:
            order_blocks.append({
                "type": "Demand",
                "index": i-1,
                "open": candles['Open'].iloc[i-1],
                "low": candles['Low'].iloc[i-1]
            })
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
    if gap > 0.01 * c1['Low']:
        return {
            "start": c1['Low'],
            "end": c3['High'],
            "type": "Bullish" if c2['Close'] > c1['Close'] else "Bearish"
        }
    return None

# 매수/매도 신호 확인
def check_triggers(candles, ob):
    patterns = detect_patterns(candles)
    fvg = detect_fvg(candles, ob['index'] + 1)
    current = candles.iloc[-1]

    if not fvg:
        return None

    if ob['type'] == "Demand" and fvg['type'] == "Bullish":
        recent_lows = candles['Low'].iloc[ob['index']+1:-1]
        if current['Low'] >= ob['low'] and current['High'] >= fvg['end']:
            return {"type": "Buy", "price": fvg['end'], "stop": ob['open']}

    if ob['type'] == "Supply" and fvg['type'] == "Bearish":
        recent_highs = candles['High'].iloc[ob['index']+1:-1]
        if current['High'] <= ob['high'] and current['Low'] <= fvg['start']:
            return {"type": "Sell", "price": fvg['start'], "stop": ob['open']}

    return None

# 다음 4시간봉 캔들 완성 시점 계산 (한국 시장 시간 기준)
def get_next_4h_candle_time():
    now = datetime.now(pytz.timezone('Asia/Seoul'))
    market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)

    if now < market_open:
        return market_open
    elif now >= market_close:
        next_day = now + timedelta(days=1)
        return next_day.replace(hour=9, minute=0, second=0, microsecond=0)

    start_of_day = now.replace(hour=9, minute=0, second=0, microsecond=0)
    hours_since_start = (now - start_of_day).total_seconds() / 3600
    next_candle_hour = start_of_day + timedelta(hours=4 * (int(hours_since_start // 4) + 1))

    if next_candle_hour > market_close:
        next_day = now + timedelta(days=1)
        return next_day.replace(hour=9, minute=0, second=0, microsecond=0)

    return next_candle_hour

def main():
    with open("stocks.txt", "r", encoding="utf-8") as file:
        stock_codes = [line.strip() for line in file if line.strip()]

    send_telegram_message("SMC 트리거 봇 시작! (4시간봉 기준, 한국 시장 시간)")

    while True:
        next_candle_time = get_next_4h_candle_time()
        wait_seconds = (next_candle_time - datetime.now(pytz.timezone('Asia/Seoul'))).total_seconds()

        if wait_seconds > 0:
            print(f"다음 4시간봉 캔들 완성까지 대기: {int(wait_seconds)}초")
            time.sleep(wait_seconds)

        print(f"4시간봉 캔들 완성! ({next_candle_time}) 트리거 확인 시작...")
        for code in stock_codes:
            try:
                candles = get_4h_data(code)
                if candles is None or len(candles) < 5:
                    continue

                order_blocks = find_order_blocks(candles)
                if order_blocks:
                    trigger = check_triggers(candles, order_blocks[-1])
                    if trigger:
                        action = "매수" if trigger['type'] == "Buy" else "매도"
                        message = f"트리거 감지! 종목: {code}, {action}, Price: {trigger['price']}, Stop: {trigger['stop']}"
                        send_telegram_message(message)
                        print(message)

            except Exception as e:
                print(f"오류 발생: {code}, {e}")

if __name__ == "__main__":
    main()
