import time
import json
import urllib.request
import urllib.parse
from datetime import datetime

SYMBOL        = "BTCUSDT"
INTERVAL      = "1m"
WEBHOOK_URL   = "https://tv-telegram-relay-l4ri.onrender.com/webhook"
POLL_SECONDS  = 60
CANDLES_FETCH = 150

SWING_LEN         = 5
MAX_BARS          = 100
MIN_BREAK_PTS     = 10
MIN_CORR_PTS      = 10
SL_BUFFER         = 10
RR_RATIO          = 1.5
MIN_CLOSES_BELOW  = 2
APPROACH_BUF      = 0.001
SELL_TP_R         = 1.5
PIP               = 0.1

state            = 0
s_bar            = 0
closes_below     = 0
p1               = None
p2               = None
c_low            = None
corr_low         = None
in_buy_trade     = False
mid_touched      = False
mid_crossed_down = False
buy_entry        = None
buy_sl           = None
buy_tp           = None
buy_mid          = None
p2_snapshot      = None

sent_p1_break_bar  = -1
sent_approach_bar  = -1
sent_corr_bar      = -1
sent_buy_bar       = -1
sent_mid_touch_bar = -1
sent_mid_red_bar   = -1
sent_sell_bar      = -1

last_processed_bar = -1

def log(msg):
    print(f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}", flush=True)

def send_alert(message):
    try:
        payload = json.dumps({"alert": message}).encode("utf-8")
        req = urllib.request.Request(WEBHOOK_URL, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            log(f"ALERT SENT -> {message} | status={resp.status}")
    except Exception as e:
        log(f"ALERT FAILED: {e}")

def get_candles():
    params = urllib.parse.urlencode({"symbol": SYMBOL, "interval": INTERVAL, "limit": CANDLES_FETCH})
    url = f"https://api.binance.com/api/v3/klines?{params}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    candles = []
    for k in data[:-1]:
        candles.append({"open_time": int(k[0]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4])})
    return candles

def pivot_high(candles, idx, swing):
    if idx < swing or idx + swing >= len(candles): return None
    center = candles[idx]["high"]
    for j in range(idx - swing, idx):
        if candles[j]["high"] > center: return None
    for j in range(idx + 1, idx + swing + 1):
        if candles[j]["high"] >= center: return None
    return center

def process_candles(candles):
    global state, s_bar, closes_below, p1, p2, c_low, corr_low
    global in_buy_trade, mid_touched, mid_crossed_down
    global buy_entry, buy_sl, buy_tp, buy_mid, p2_snapshot
    global sent_p1_break_bar, sent_approach_bar, sent_corr_bar, sent_buy_bar
    global sent_mid_touch_bar, sent_mid_red_bar, sent_sell_bar
    global last_processed_bar

    start_idx = 0
    for i, c in enumerate(candles):
        if c["open_time"] > last_processed_bar:
            start_idx = i
            break

    process_from = max(SWING_LEN, start_idx - 1)

    for i in range(process_from, len(candles)):
        bar = candles[i]
        bar_time = bar["open_time"]
        is_new = bar_time > last_processed_bar
        h = bar["high"]
        l = bar["low"]
        c = bar["close"]
        pivot_h = pivot_high(candles, i - SWING_LEN, SWING_LEN) if i >= SWING_LEN else None
        s_bar += 1

        if state == 0 and pivot_h is not None:
            p1 = pivot_h; state = 1; s_bar = 0; closes_below = 0
            log(f"[S0->1] P1 set = {p1:.2f}")
        elif state == 1:
            if pivot_h is not None and pivot_h > p1:
                p1 = pivot_h; s_bar = 0; closes_below = 0
            if c > p1 + MIN_BREAK_PTS * PIP:
                p2 = h; c_low = l; corr_low = None; state = 2; s_bar = 0
                log(f"[S1->2] P1 Breakout! P2={p2:.2f}")
                if is_new and bar_time != sent_p1_break_bar:
                    sent_p1_break_bar = bar_time
                    send_alert(f"P1 BREAKOUT {SYMBOL} p1={p1:.2f} price={c:.2f}")
            if s_bar > MAX_BARS: state = 0; s_bar = 0
        elif state == 2:
            if h > p2: p2 = h
            if l < c_low: c_low = l
            if c < p1: closes_below += 1
            else: closes_below = 0
            if closes_below >= MIN_CLOSES_BELOW and (p2 - c_low) >= MIN_CORR_PTS * PIP:
                corr_low = c_low; state = 3; s_bar = 0; closes_below = 0
                log(f"[S2->3] Correction confirmed. corr_low={corr_low:.2f}")
                if is_new and bar_time != sent_corr_bar:
                    sent_corr_bar = bar_time
                    send_alert(f"CORRECTION CONFIRMED {SYMBOL}")
            if s_bar > MAX_BARS: state = 0; s_bar = 0
        elif state == 3:
            if l < corr_low: corr_low = l
            threshold = p1 - ((p2 - p1) * APPROACH_BUF)
            if h >= threshold and c < p1:
                if is_new and bar_time != sent_approach_bar:
                    sent_approach_bar = bar_time
                    send_alert(f"APPROACHING P1 {SYMBOL}")
            if c > p1:
                buy_entry = c; buy_sl = corr_low - SL_BUFFER * PIP
                buy_tp = buy_entry + (buy_entry - buy_sl) * RR_RATIO
                buy_mid = (buy_entry + buy_sl) / 2
                p2_snapshot = p2; in_buy_trade = True; mid_touched = False; mid_crossed_down = False
                state = 0; s_bar = 0
                log(f"[S3->BUY] entry={buy_entry:.2f} SL={buy_sl:.2f} TP={buy_tp:.2f}")
                if is_new and bar_time != sent_buy_bar:
                    sent_buy_bar = bar_time
                    send_alert(f"BUY {SYMBOL} entry={buy_entry:.2f} SL={buy_sl:.2f} TP={buy_tp:.2f}")
            if s_bar > MAX_BARS: state = 0; s_bar = 0

        if in_buy_trade and buy_sl is not None:
            if not mid_crossed_down and l < buy_mid:
                mid_crossed_down = True
                if is_new and bar_time != sent_mid_red_bar:
                    sent_mid_red_bar = bar_time
                    send_alert(f"MIDPOINT CROSSED DOWN {SYMBOL} mid={buy_mid:.2f}")
            if not mid_touched and l <= buy_mid and h >= buy_mid:
                mid_touched = True
                if is_new and bar_time != sent_mid_touch_bar:
                    sent_mid_touch_bar = bar_time
                    send_alert(f"MIDPOINT TOUCHED {SYMBOL} mid={buy_mid:.2f}")
            if h >= buy_tp:
                log(f"[TRADE] TP hit at {buy_tp:.2f}")
                in_buy_trade = False; buy_entry = buy_sl = buy_tp = buy_mid = None; mid_touched = False
            elif mid_touched and l <= buy_sl:
                s_entry = buy_mid; s_sl = p2_snapshot + SL_BUFFER * PIP
                s_tp = s_entry - (s_sl - s_entry) * SELL_TP_R
                if is_new and bar_time != sent_sell_bar:
                    sent_sell_bar = bar_time
                    send_alert(f"MODEL A SELL {SYMBOL} LIMIT={s_entry:.2f} SL={s_sl:.2f} TP={s_tp:.2f}")
                in_buy_trade = False; buy_entry = buy_sl = buy_tp = buy_mid = None; mid_touched = False
            elif l <= buy_sl and not mid_touched:
                in_buy_trade = False; buy_entry = buy_sl = buy_tp = buy_mid = None; mid_touched = False

        if is_new:
            last_processed_bar = bar_time

def main():
    log(f"Power of 3 Model A Bot started — {SYMBOL} {INTERVAL}")
    while True:
        try:
            candles = get_candles()
            process_candles(candles)
        except Exception as e:
            log(f"ERROR: {e}")
        log(f"Sleeping {POLL_SECONDS}s...")
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
