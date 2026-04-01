import time
import json
import urllib.request
import urllib.parse
import re
from datetime import datetime

# --- CONFIG -------------------------------------------------------------------
SYMBOL        = "BTCUSD"
INTERVAL      = "1m"
POLL_SECONDS  = 10
CANDLES_FETCH = 150
FEED_LABEL    = "Bitstamp"

TELEGRAM_TOKEN   = "8592174927:AAEEKWBbqn251iXhBs4-RGm33HIUjfLUaX0"
TELEGRAM_CHAT_ID = "6726986738"

# --- INDICATOR SETTINGS -------------------------------------------------------
SWING_LEN         = 5
MAX_BARS          = 100
MIN_BREAK_PTS     = 10
MIN_CORR_PTS      = 10
SL_BUFFER         = 10
RR_RATIO          = 1.5
MIN_CLOSES_BELOW  = 3
APPROACH_BUF      = 0.001
SELL_TP_R         = 1.0
PIP               = 0.1

# --- STATE --------------------------------------------------------------------
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
sell_triggered   = False

sent_p1_break_bar  = -1
sent_approach_bar  = -1
sent_corr_bar      = -1
sent_buy_bar       = -1
sent_mid_touch_bar = -1
sent_mid_red_bar   = -1
sent_sell_bar      = -1

last_processed_bar = -1

# --- HELPERS ------------------------------------------------------------------
def log(msg):
    print(f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}", flush=True)

def format_message(alert, signal_price):
    """
    FIX: signal_price is now passed in at the moment the signal fires,
    not read from a global that may have already moved.
    """
    a = alert.strip()
    price_line = f"Current Price: <b>{signal_price:.2f}</b>\n" if signal_price else ""
    p1_line    = f"P1 Level: <b>{p1:.2f}</b>\n" if p1 else ""
    source     = f"Source: <b>{FEED_LABEL}</b>\n"

    if a.startswith("STARTUP"):
        pair = a.replace("STARTUP ", "")
        return (
            f"✅ <b>Bot is LIVE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Pair: <b>{pair}</b>\n"
            f"{source}"
            f"Timeframe: <b>1 minute</b>\n"
            f"Poll interval: <b>10 seconds</b>\n\n"
            f"🟢 Watching for PO3 signals...\n"
            f"📡 All alerts will appear here."
        )

    if a.startswith("P1 BREAKOUT"):
        parts = re.match(r"P1 BREAKOUT (\S+) p1=([\d.]+) price=([\d.]+)", a)
        if parts:
            return (
                f"🚀 <b>STAGE 1 — PO3 BREAKOUT [1m]</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Pair: <b>{parts.group(1)}</b>  |  TF: <b>1m</b>\n"
                f"P1 Level: <b>{parts.group(2)}</b>\n"
                f"Current Price: <b>{parts.group(3)}</b>\n\n"
                f"✅ Price broke above P1 on 1m.\n"
                f"⏳ Watching for correction..."
            )

    if a.startswith("CORRECTION CONFIRMED"):
        pair = a.replace("CORRECTION CONFIRMED ", "")
        return (
            f"🗒 <b>STAGE 3 — Correction Confirmed [1m]</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Pair: <b>{pair}</b>  |  TF: <b>1m</b>\n\n"
            f"✅ Price closed below P1 required times.\n"
            f"⏳ Watching for re-entry above P1..."
        )

    if a.startswith("APPROACHING P1"):
        pair = a.replace("APPROACHING P1 ", "")
        return (
            f"🟠 <b>STAGE 4 — Approaching P1 [1m]</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Pair: <b>{pair}</b>  |  TF: <b>1m</b>\n"
            f"{price_line}{p1_line}\n"
            f"⚠️ Price nearing P1 from below.\n"
            f"👀 BUY signal may fire soon."
        )

    if a.startswith("BUY "):
        parts = re.match(r"BUY (\S+) entry=([\d.]+) SL=([\d.]+) TP=([\d.]+)", a)
        if parts:
            entry = float(parts.group(2))
            sl    = float(parts.group(3))
            tp    = float(parts.group(4))
            rr    = round((tp - entry) / (entry - sl), 2) if (entry - sl) != 0 else 0
            return (
                f"🟢 <b>STAGE 5 — BUY SIGNAL [1m]</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Pair: <b>{parts.group(1)}</b>  |  TF: <b>1m</b>\n"
                f"Entry: <b>{parts.group(2)}</b>\n"
                f"Stop Loss: <b>{parts.group(3)}</b>\n"
                f"Take Profit: <b>{parts.group(4)}</b>\n"
                f"R:R: <b>1:{rr}</b>\n\n"
                f"✅ Price reclaimed P1.\n"
                f"⏳ Watching midpoint & SL..."
            )

    if a.startswith("MIDPOINT TOUCHED"):
        parts = re.match(r"MIDPOINT TOUCHED (\S+) mid=([\d.]+)", a)
        if parts:
            return (
                f"⚪ <b>STAGE 6 — Midpoint Touched [1m]</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Pair: <b>{parts.group(1)}</b>  |  TF: <b>1m</b>\n"
                f"Midpoint: <b>{parts.group(2)}</b>\n\n"
                f"🔴 Model A Sell now ARMED.\n"
                f"👀 Watching for SL sweep..."
            )

    if a.startswith("MIDPOINT CROSSED DOWN"):
        parts = re.match(r"MIDPOINT CROSSED DOWN (\S+) mid=([\d.]+)", a)
        if parts:
            return (
                f"🔻 <b>STAGE 6b — Midpoint Crossed Down [1m]</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Pair: <b>{parts.group(1)}</b>  |  TF: <b>1m</b>\n"
                f"Midpoint: <b>{parts.group(2)}</b>\n\n"
                f"⚠️ Price dropped below midpoint.\n"
                f"⚠️ Buy trade under pressure."
            )

    if a.startswith("MODEL A SELL"):
        parts = re.match(r"MODEL A SELL (\S+) LIMIT=([\d.]+) SL=([\d.]+) TP=([\d.]+)", a)
        if parts:
            limit = float(parts.group(2))
            sl    = float(parts.group(3))
            tp    = float(parts.group(4))
            rr    = round((limit - tp) / (sl - limit), 2) if (sl - limit) != 0 else 0
            return (
                f"🔴 <b>STAGE 7 — MODEL A SELL [1m]</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Pair: <b>{parts.group(1)}</b>  |  TF: <b>1m</b>\n"
                f"Sell Limit: <b>{parts.group(2)}</b>\n"
                f"Stop Loss: <b>{parts.group(3)}</b>\n"
                f"Take Profit: <b>{parts.group(4)}</b>\n"
                f"R:R: <b>1:{rr}</b>\n\n"
                f"✅ Buy failed — mid touched then SL hit.\n"
                f"📌 Place SELL LIMIT @ <b>{parts.group(2)}</b>\n"
                f"   SL: <b>{parts.group(3)}</b>  |  TP: <b>{parts.group(4)}</b>"
            )

    return f"📡 <b>Alert</b>\n{source}{price_line}{a}"


def send_telegram(message, signal_price=None):
    """
    FIX: signal_price is now an explicit parameter so each signal
    carries the price captured at the exact moment the signal fired.
    """
    try:
        text = format_message(message, signal_price)
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
            log(f"TELEGRAM SENT -> {message[:60]} | status={resp.status}")
    except Exception as e:
        log(f"TELEGRAM FAILED: {e}")


# --- BINANCE DATA -------------------------------------------------------------
def get_candles():
    url = f"https://www.bitstamp.net/api/v2/ohlc/btcusd/?step=60&limit={CANDLES_FETCH}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    raw = sorted(data["data"]["ohlc"], key=lambda x: int(x["timestamp"]))
    candles = []
    # Exclude the last candle — it is still open (incomplete)
    for k in raw[:-1]:
        candles.append({
            "open_time": int(k["timestamp"]) * 1000,  # convert to ms to match state tracking
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
        if candles[j]["high"] > center:
            return None
    for j in range(idx + 1, idx + swing + 1):
        if candles[j]["high"] >= center:
            return None
    return center


# --- PROCESS ------------------------------------------------------------------
def process_candles(candles):
    global state, s_bar, closes_below, p1, p2, c_low, corr_low
    global in_buy_trade, mid_touched, mid_crossed_down, sell_triggered
    global buy_entry, buy_sl, buy_tp, buy_mid, p2_snapshot
    global sent_p1_break_bar, sent_approach_bar, sent_corr_bar, sent_buy_bar
    global sent_mid_touch_bar, sent_mid_red_bar, sent_sell_bar
    global last_processed_bar

    # FIX: find the first bar we haven't processed yet — no off-by-one
    start_idx = 0
    for i, c in enumerate(candles):
        if c["open_time"] > last_processed_bar:
            start_idx = i
            break
    else:
        # All candles already processed
        return

    # Go back SWING_LEN bars so pivot detection has enough lookback,
    # but only PROCESS (send signals) for new bars using is_new guard.
    process_from = max(SWING_LEN * 2, start_idx - SWING_LEN)

    for i in range(process_from, len(candles)):
        bar      = candles[i]
        bar_time = bar["open_time"]
        # FIX: is_new is True only for bars we haven't seen before
        is_new   = bar_time > last_processed_bar

        h = bar["high"]
        l = bar["low"]
        c = bar["close"]

        # FIX: capture price AT THIS BAR for use in signals — not a global
        bar_price = c

        pivot_h = pivot_high(candles, i - SWING_LEN, SWING_LEN) if i >= SWING_LEN * 2 else None
        if is_new:
            s_bar += 1

        # ── STATE 0: Looking for first pivot high ──────────────────────────
        if state == 0 and pivot_h is not None:
            p1 = pivot_h
            state = 1
            s_bar = 0
            closes_below = 0
            log(f"[S0->1] P1 set = {p1:.2f}")

        # ── STATE 1: Waiting for breakout above P1 ─────────────────────────
        elif state == 1:
            if pivot_h is not None and pivot_h > p1:
                p1 = pivot_h
                s_bar = 0
                closes_below = 0

            if c > p1 and is_new and bar_time != sent_p1_break_bar:
                sent_p1_break_bar = bar_time
                # FIX: pass bar_price — the price on this exact candle
                send_telegram(
                    f"P1 BREAKOUT {SYMBOL} p1={p1:.2f} price={bar_price:.2f}",
                    signal_price=bar_price
                )

            if c > p1 + MIN_BREAK_PTS * PIP:
                p2 = h
                c_low = l
                corr_low = None
                state = 2
                s_bar = 0
                log(f"[S1->2] Breakout confirmed. P2={p2:.2f}")

            if s_bar > MAX_BARS:
                state = 0
                s_bar = 0

        # ── STATE 2: Tracking the correction ──────────────────────────────
        elif state == 2:
            if h > p2:
                p2 = h
            if l < c_low:
                c_low = l

            if c < p1:
                closes_below += 1
            else:
                closes_below = 0

            if closes_below >= MIN_CLOSES_BELOW and (p2 - c_low) >= MIN_CORR_PTS * PIP:
                corr_low = c_low
                state = 3
                s_bar = 0
                closes_below = 0
                log(f"[S2->3] Correction confirmed. corr_low={corr_low:.2f}")
                if is_new and bar_time != sent_corr_bar:
                    sent_corr_bar = bar_time
                    send_telegram(f"CORRECTION CONFIRMED {SYMBOL}", signal_price=bar_price)

            if s_bar > MAX_BARS:
                state = 0
                s_bar = 0

        # ── STATE 3: Watching for re-entry above P1 ────────────────────────
        elif state == 3:
            if l < corr_low:
                corr_low = l

            threshold = p1 - ((p2 - p1) * APPROACH_BUF)
            if h >= threshold and c < p1:
                if is_new and bar_time != sent_approach_bar:
                    sent_approach_bar = bar_time
                    send_telegram(f"APPROACHING P1 {SYMBOL}", signal_price=bar_price)

            if c > p1:
                buy_entry = c
                buy_sl    = corr_low - SL_BUFFER * PIP
                buy_tp    = buy_entry + (buy_entry - buy_sl) * RR_RATIO
                buy_mid   = (buy_entry + buy_sl) / 2
                p2_snapshot   = p2
                in_buy_trade  = True
                mid_touched   = False
                mid_crossed_down = False
                sell_triggered   = False
                # FIX: do NOT reset state to 0 here — keep state separate
                # from trade management so a new P1 hunt doesn't clobber
                # the live trade's corr_low / p2_snapshot
                state = 4   # new "in trade" state — no pivot hunting
                s_bar = 0
                log(f"[S3->BUY] entry={buy_entry:.2f} SL={buy_sl:.2f} TP={buy_tp:.2f}")
                if is_new and bar_time != sent_buy_bar:
                    sent_buy_bar = bar_time
                    send_telegram(
                        f"BUY {SYMBOL} entry={buy_entry:.2f} SL={buy_sl:.2f} TP={buy_tp:.2f}",
                        signal_price=bar_price
                    )

            if s_bar > MAX_BARS:
                state = 0
                s_bar = 0

        # ── STATE 4: In trade (idle state — no new pivot hunting) ──────────
        elif state == 4:
            pass   # trade managed below; state returns to 0 on exit

        # ── TRADE MANAGEMENT (runs regardless of state when in_buy_trade) ──
        if in_buy_trade and buy_sl is not None:

            # Midpoint touch check
            if not mid_touched and l <= buy_mid and h >= buy_mid:
                mid_touched = True
                if is_new and bar_time != sent_mid_touch_bar:
                    sent_mid_touch_bar = bar_time
                    send_telegram(
                        f"MIDPOINT TOUCHED {SYMBOL} mid={buy_mid:.2f}",
                        signal_price=bar_price
                    )

            # Midpoint crossed down check
            if not mid_crossed_down and l < buy_mid:
                mid_crossed_down = True
                if is_new and bar_time != sent_mid_red_bar:
                    sent_mid_red_bar = bar_time
                    send_telegram(
                        f"MIDPOINT CROSSED DOWN {SYMBOL} mid={buy_mid:.2f}",
                        signal_price=bar_price
                    )

            # TP hit
            if h >= buy_tp:
                log(f"[TRADE] TP hit at {buy_tp:.2f}")
                in_buy_trade = False
                sell_triggered = False
                buy_entry = buy_sl = buy_tp = buy_mid = None
                mid_touched = False
                state = 0   # safe to hunt again
                s_bar = 0

            # FIX: MODEL A SELL — SL is now correctly above the midpoint
            # (buy_mid + buffer), NOT above p2_snapshot which gave wrong levels
            elif mid_touched and l <= buy_sl and not sell_triggered:
                sell_triggered = True
                s_entry = buy_mid                                # sell limit = midpoint
                s_sl    = buy_mid + SL_BUFFER * PIP             # FIX: SL just above midpoint
                s_tp    = s_entry - (s_entry - buy_sl) * SELL_TP_R  # FIX: TP mirrors the risk
                if is_new and bar_time != sent_sell_bar:
                    sent_sell_bar = bar_time
                    send_telegram(
                        f"MODEL A SELL {SYMBOL} LIMIT={s_entry:.2f} SL={s_sl:.2f} TP={s_tp:.2f}",
                        signal_price=bar_price
                    )
                in_buy_trade = False
                buy_entry = buy_sl = buy_tp = buy_mid = None
                mid_touched = False
                state = 0
                s_bar = 0

            # Stopped out without midpoint touch — just reset
            elif l <= buy_sl and not mid_touched:
                log(f"[TRADE] SL hit without mid touch — stopped out")
                in_buy_trade = False
                sell_triggered = False
                buy_entry = buy_sl = buy_tp = buy_mid = None
                mid_touched = False
                state = 0
                s_bar = 0

        if is_new:
            last_processed_bar = bar_time


# --- MAIN ---------------------------------------------------------------------
def main():
    log(f"Power of 3 Model A Bot started - {SYMBOL} 1m - {FEED_LABEL}")
    log(f"Sending to Telegram chat {TELEGRAM_CHAT_ID}")
    send_telegram(f"STARTUP {SYMBOL}", signal_price=None)

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
