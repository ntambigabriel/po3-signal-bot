import time
import json
import urllib.request
import urllib.parse
import re
from datetime import datetime

# ─── CONFIG ───────────────────────────────────────────────────────────────────
SYMBOL        = "BTCUSD"
INTERVAL      = "1m"
POLL_SECONDS  = 60
CANDLES_FETCH = 150
FEED_LABEL    = "Bitstamp"  # matches TradingView exactly

TELEGRAM_TOKEN   = "8592174927:AAEEKWBbqn251iXhBs4-RGm33HIUjfLUaX0"
TELEGRAM_CHAT_ID = "6726986738"

# ─── INDICATOR SETTINGS (v3.4) ────────────────────────────────────────────────
SWING_LEN         = 5
MAX_BARS          = 100
MIN_BREAK_PTS     = 10
MIN_CORR_PTS      = 10
SL_BUFFER         = 10
RR_RATIO          = 1.7
MIN_CLOSES_BELOW  = 2
APPROACH_BUF      = 0.001
SELL_TP_R         = 1.5
PIP               = 0.1

# ─── STATE ────────────────────────────────────────────────────────────────────
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

sent_approach_bar  = -1
sent_corr_bar      = -1
sent_buy_bar       = -1
sent_mid_touch_bar = -1
sent_mid_red_bar   = -1
sent_sell_bar      = -1

last_processed_bar = -1
current_price      = None

# ─── HELPERS ──────────────────────────────────────────────────────────────────
def log(msg):
    print(f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}", flush=True)

def format_message(alert, price):
    a = alert.strip()
    price_line = f"Current Price: <b>{price:.2f}</b>\n" if price else ""
    p1_line    = f"P1 Level: <b>{p1:.2f}</b>\n" if p1 else ""
    source     = f"Source: <b>{FEED_LABEL}</b>\n"

    if a.startswith("CORRECTION CONFIRMED"):
        pair = a.replace("CORRECTION CONFIRMED ", "")
        return (
            f"📉 <b>STAGE 3 — Correction Confirmed</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Pair: <b>{pair}</b>\n"
            f"{source}{price_line}{p1_line}\n"
            f"✅ Price has closed below P1 the required number of times.\n"
            f"✅ Correction size is valid.\n\n"
            f"⏳ Now watching for price to approach and reclaim P1 from below..."
        )

    if a.startswith("APPROACHING P1"):
        pair = a.replace("APPROACHING P1 ", "")
        return (
            f"🟠 <b>STAGE 4 — Approaching P1</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Pair: <b>{pair}</b>\n"
            f"{source}{price_line}{p1_line}\n"
            f"⚠️ Price is getting close to P1 from below.\n"
            f"👀 Get ready — a BUY signal may fire soon if price closes above P1."
        )

    if a.startswith("BUY "):
        parts = re.match(r"BUY (\S+) entry=([\d.]+) SL=([\d.]+) TP=([\d.]+)", a)
        if parts:
            entry = float(parts.group(2))
            sl    = float(parts.group(3))
            tp    = float(parts.group(4))
            rr    = round((tp - entry) / (entry - sl), 2)
            return (
                f"🟢 <b>STAGE 5 — BUY SIGNAL FIRED</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Pair: <b>{parts.group(1)}</b>\n"
                f"{source}{price_line}"
                f"Entry: <b>{parts.group(2)}</b>\n"
                f"Stop Loss: <b>{parts.group(3)}</b>\n"
                f"Take Profit: <b>{parts.group(4)}</b>\n"
                f"R:R Ratio: <b>1:{rr}</b>\n\n"
                f"✅ Price has closed back above P1.\n"
                f"⏳ Now managing trade — watching midpoint and SL..."
            )

    if a.startswith("MIDPOINT TOUCHED"):
        parts = re.match(r"MIDPOINT TOUCHED (\S+) mid=([\d.]+)", a)
        if parts:
            return (
                f"⚪ <b>STAGE 6 — Midpoint Touched</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Pair: <b>{parts.group(1)}</b>\n"
                f"{source}{price_line}"
                f"Midpoint Level: <b>{parts.group(2)}</b>\n\n"
                f"⚠️ Price has touched the midpoint between entry and SL.\n"
                f"🔴 Model A Sell is now ARMED.\n"
                f"👀 If price drops back to SL from here, a SELL LIMIT will trigger."
            )

    if a.startswith("MIDPOINT CROSSED DOWN"):
        parts = re.match(r"MIDPOINT CROSSED DOWN (\S+) mid=([\d.]+)", a)
        if parts:
            return (
                f"🔻 <b>STAGE 6b — Midpoint Crossed Down</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Pair: <b>{parts.group(1)}</b>\n"
                f"{source}{price_line}"
                f"Midpoint Level: <b>{parts.group(2)}</b>\n\n"
                f"⚠️ Price has dropped below the midpoint.\n"
                f"⚠️ Caution — buy trade is under pressure."
            )

    if a.startswith("MODEL A SELL"):
        parts = re.match(r"MODEL A SELL (\S+) LIMIT=([\d.]+) SL=([\d.]+) TP=([\d.]+)", a)
        if parts:
            limit = float(parts.group(2))
            sl    = float(parts.group(3))
            tp    = float(parts.group(4))
            rr    = round((limit - tp) / (sl - limit), 2)
            return (
                f"🔴 <b>STAGE 7 — MODEL A SELL TRIGGERED</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Pair: <b>{parts.group(1)}</b>\n"
                f"{source}{price_line}"
                f"Sell Limit: <b>{parts.group(2)}</b>\n"
                f"Stop Loss: <b>{parts.group(3)}</b>\n"
                f"Take Profit: <b>{parts.group(4)}</b>\n"
                f"R:R Ratio: <b>1:{rr}</b>\n\n"
                f"✅ Buy failed — midpoint was touched then price hit SL.\n"
                f"📌 Place sell limit at <b>{parts.group(2)}</b> with SL at <b>{parts.group(3)}</b> and TP at <b>{parts.group(4)}</b>."
            )

    return f"📡 <b>Alert</b>\n{source}{price_line}{a}"

def send_telegram(message):
    time.sleep(1.5)
    try:
        text = format_message(message, current_price)
        body = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            log(f"TELEGRAM SENT → {message[:60]} | status={resp.status}")
    except Exception as e:
        log(f"TELEGRAM FAILED: {e}")

# ─── BITSTAMP DATA ─────────────────────────────────────────────────────────────
def get_candles():
    url = f"https://www.bitstamp.net/api/v2/ohlc/btcusd/?step=60&limit={CANDLES_FETCH}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    raw = sorted(data["data"]["ohlc"], key=lambda x: int(x["timestamp"]))
    candles = []
    for k in raw[:-1]:  # drop last unconfirmed candle
        candles.append({
            "open_time": int(k["timestamp"]) * 1000,
            "high":  float(k["high"]),
            "low":   float(k["low"]),
            "close": float(k["close"]),
        })
    return candles

def pivot_high(candles, idx, swing):
    if idx < swing or idx + swing >= len(candles):
        return None
    center = candles[idx]["high"]
    for j in range(idx - swing, idx):
        if candles[j]["high"] > center: return None
    for j in range(idx + 1, idx + swing + 1):
        if candles[j]["high"] >= center: return None
    return center

# ─── PROCESS ──────────────────────────────────────────────────────────────────
def process_candles(candles):
    global state, s_bar, closes_below, p1, p2, c_low, corr_low
    global in_buy_trade, mid_touched, mid_crossed_down
    global buy_entry, buy_sl, buy_tp, buy_mid, p2_snapshot
    global sent_approach_bar, sent_corr_bar, sent_buy_bar
    global sent_mid_touch_bar, sent_mid_red_bar, sent_sell_bar
    global last_processed_bar, current_price

    start_idx = 0
    for i, c in enumerate(candles):
        if c["open_time"] > last_processed_bar:
            start_idx = i
            break

    process_from = max(SWING_LEN, start_idx - 1)

    for i in range(process_from, len(candles)):
        bar      = candles[i]
        bar_time = bar["open_time"]
        is_new   = bar_time > last_processed_bar

        h = bar["high"]
        l = bar["low"]
        c = bar["close"]

        current_price = c

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
                log(f"[S1->2] Breakout! P2={p2:.2f}")
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
                    send_telegram(f"CORRECTION CONFIRMED {SYMBOL}")
            if s_bar > MAX_BARS: state = 0; s_bar = 0

        elif state == 3:
            if l < corr_low: corr_low = l
            threshold = p1 - ((p2 - p1) * APPROACH_BUF)
            if h >= threshold and c < p1:
                if is_new and bar_time != sent_approach_bar:
                    sent_approach_bar = bar_time
                    send_telegram(f"APPROACHING P1 {SYMBOL}")
            if c > p1:
                buy_entry = c
                buy_sl  = corr_low - SL_BUFFER * PIP
                buy_tp  = buy_entry + (buy_entry - buy_sl) * RR_RATIO
                buy_mid = (buy_entry + buy_sl) / 2
                p2_snapshot = p2
                in_buy_trade = True; mid_touched = False; mid_crossed_down = False
                state = 0; s_bar = 0
                log(f"[S3->BUY] entry={buy_entry:.2f} SL={buy_sl:.2f} TP={buy_tp:.2f}")
                if is_new and bar_time != sent_buy_bar:
                    sent_buy_bar = bar_time
                    send_telegram(f"BUY {SYMBOL} entry={buy_entry:.2f} SL={buy_sl:.2f} TP={buy_tp:.2f}")
            if s_bar > MAX_BARS: state = 0; s_bar = 0

        if in_buy_trade and buy_sl is not None:
            if not mid_crossed_down and l < buy_mid:
                mid_crossed_down = True
                if is_new and bar_time != sent_mid_red_bar:
                    sent_mid_red_bar = bar_time
                    send_telegram(f"MIDPOINT CROSSED DOWN {SYMBOL} mid={buy_mid:.2f}")

            if not mid_touched and l <= buy_mid and h >= buy_mid:
                mid_touched = True
                if is_new and bar_time != sent_mid_touch_bar:
                    sent_mid_touch_bar = bar_time
                    send_telegram(f"MIDPOINT TOUCHED {SYMBOL} mid={buy_mid:.2f}")

            if h >= buy_tp:
                log(f"[TRADE] TP hit at {buy_tp:.2f}")
                in_buy_trade = False
                buy_entry = buy_sl = buy_tp = buy_mid = None
                mid_touched = False

            elif mid_touched and l <= buy_sl:
                s_entry = buy_mid
                s_sl    = p2_snapshot + SL_BUFFER * PIP
                s_tp    = s_entry - (s_sl - s_entry) * SELL_TP_R
                if is_new and bar_time != sent_sell_bar:
                    sent_sell_bar = bar_time
                    send_telegram(f"MODEL A SELL {SYMBOL} LIMIT={s_entry:.2f} SL={s_sl:.2f} TP={s_tp:.2f}")
                in_buy_trade = False
                buy_entry = buy_sl = buy_tp = buy_mid = None
                mid_touched = False

            elif l <= buy_sl and not mid_touched:
                log(f"[TRADE] SL hit — stopped out")
                in_buy_trade = False
                buy_entry = buy_sl = buy_tp = buy_mid = None
                mid_touched = False

        if is_new:
            last_processed_bar = bar_time

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    log(f"Power of 3 Model A Bot started — {SYMBOL} 1m — {FEED_LABEL}")
    log(f"Sending directly to Telegram chat {TELEGRAM_CHAT_ID}")
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
