#!/usr/bin/env python3
"""
NEAR/USDT Signal Bot v4 + Paper Trading
- Καταγράφει εικονικές αγορές από τα σήματα
- TP: +1.5% | SL: -1.0%
- Αποτελέσματα σε: ~/Downloads/paper_trades.csv
"""
import json, time, csv, os
from urllib.request import urlopen, Request

TELEGRAM_TOKEN = "YOUR_TELEGRAM_TOKEN"
TELEGRAM_CHAT  = "YOUR_TELEGRAM_CHAT_ID"
BASE           = "https://contract.mexc.com"
PAIR           = "NEAR_USDT"
INTERVAL       = 60

TP_PCT         = 1.5   # +1.5% κέρδος
SL_PCT         = 1.0   # -1.0% ζημία
LOG_FILE       = os.path.expanduser("~/Downloads/paper_trades.csv")

last_signal      = "WAIT"
last_signal_time = 0

# Paper trade state
paper_trade = None  # {"direction", "entry", "time"}

j15_history    = []
j5_history     = []
price_history  = []
bull5_history  = []
bull15_history = []

# ── CSV init ───────────────────────────────────────────────
def init_log():
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow([
                "Ωρα", "Ζεύγος", "Κατεύθυνση", "Τιμή Εισόδου",
                "Τιμή Εξόδου", "% Αποτέλεσμα", "Αποτέλεσμα", "Διάρκεια (λεπτά)"
            ])

def log_trade(direction, entry, exit_price, pct, result, duration_min):
    with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
        csv.writer(f).writerow([
            time.strftime("%H:%M:%S"), PAIR, direction,
            f"{entry:.4f}", f"{exit_price:.4f}",
            f"{pct:+.2f}%", result, f"{duration_min:.0f}"
        ])
    print(f"  📊 PAPER: {direction} {entry:.4f}→{exit_price:.4f} {pct:+.2f}% {result} ({duration_min:.0f}λ)")

# ── Fetch ──────────────────────────────────────────────────
def fetch(url):
    try:
        r = urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=10)
        return json.loads(r.read())
    except Exception as e:
        print(f"  ERR: {e}")
        return None

def get_klines(interval, limit=150):
    d = fetch(f"{BASE}/api/v1/contract/kline/{PAIR}?interval={interval}&limit={limit}")
    if not d or not d.get("data"): return []
    rows = d["data"]
    candles = []
    H=rows.get("high",[]); L=rows.get("low",[])
    C=rows.get("close",[]); O=rows.get("open",[]); V=rows.get("vol",[])
    for i in range(len(C)):
        candles.append({
            "h": float(H[i]), "l": float(L[i]),
            "c": float(C[i]), "o": float(O[i]),
            "v": float(V[i]) if i < len(V) else 0
        })
    return candles

def get_price():
    t = fetch(f"{BASE}/api/v1/contract/ticker?symbol={PAIR}")
    if t and t.get("data"):
        return float(t["data"].get("lastPrice", 0))
    return 0

# ── Indicators ────────────────────────────────────────────
def ema(data, period):
    k = 2 / (period + 1)
    result = [data[0]]
    for v in data[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result

def macd_calc(closes, fast, slow, signal):
    ml = [f - s for f, s in zip(ema(closes, fast), ema(closes, slow))]
    sl = ema(ml, signal)
    return ml, sl, [m - s for m, s in zip(ml, sl)]

def kdj_calc(candles, k_period):
    K, D, J = [], [], []
    for i in range(len(candles)):
        w = candles[max(0, i-k_period+1):i+1]
        hi = max(c["h"] for c in w)
        lo = min(c["l"] for c in w)
        rsv = (candles[i]["c"] - lo) / (hi - lo) * 100 if hi != lo else 50
        kv = (2/3) * K[-1] + (1/3) * rsv if K else 50.0
        dv = (2/3) * D[-1] + (1/3) * kv if D else 50.0
        K.append(kv); D.append(dv); J.append(3*kv - 2*dv)
    return K, D, J

def bias_ma(closes, period):
    result = []
    for i in range(len(closes)):
        if i < period-1:
            result.append(0.0)
        else:
            ma = sum(closes[i-period+1:i+1]) / period
            result.append((closes[i] - ma) / ma * 100)
    return result

# ── Telegram ──────────────────────────────────────────────
def telegram_send(text):
    try:
        payload = json.dumps({
            "chat_id": TELEGRAM_CHAT,
            "text": text,
            "disable_web_page_preview": True
        }).encode("utf-8")
        req = Request(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        urlopen(req, timeout=8)
        print("  ✅ Telegram εστάλη!")
    except Exception as e:
        print(f"  ERR telegram: {e}")

# ── Analyze ───────────────────────────────────────────────
def analyze():
    c15 = get_klines("Min15", 150)
    if not c15: return None
    cl15  = [c["c"] for c in c15]
    price = cl15[-1]

    e5_15        = ema(cl15, 5)
    _, _, hist15 = macd_calc(cl15, 2, 41, 18)
    K15, D15, J15 = kdj_calc(c15, 7)
    b6_15        = bias_ma(cl15, 6)
    b14_15       = bias_ma(cl15, 14)

    j15 = J15[-1]; k15 = K15[-1]; d15 = D15[-1]
    h15 = hist15[-1]
    b6v = b6_15[-1]; b14v = b14_15[-1]

    kdj15_bull = k15 > d15
    kdj15_bear = k15 < d15

    bull15 = sum([
        price > e5_15[-1],
        h15 > 0,
        kdj15_bull,
        b6v > 0 and b14v > 0
    ])
    bear15 = 4 - bull15

    c5 = get_klines("Min5", 150)
    if not c5: return None
    cl5 = [c["c"] for c in c5]

    e5_5         = ema(cl5, 5)
    e15_5        = ema(cl5, 15)
    _, _, hist5  = macd_calc(cl5, 2, 19, 40)
    K5, D5, J5   = kdj_calc(c5, 9)

    j5 = J5[-1]; k5 = K5[-1]; d5 = D5[-1]
    h5 = hist5[-1]

    kdj5_bull = k5 > d5
    kdj5_bear = k5 < d5

    bull5 = sum([
        cl5[-1] > e5_5[-1],
        e5_5[-1] > e15_5[-1],
        h5 > 0,
        kdj5_bull
    ])
    bear5 = 4 - bull5

    hi20 = max(c["h"] for c in c15[-20:])
    lo20 = min(c["l"] for c in c15[-20:])
    range_pct = (price - lo20) / (hi20 - lo20) * 100 if hi20 != lo20 else 50

    vols15    = [c["v"] for c in c15[-22:-2]]
    avg_vol20 = sum(vols15) / len(vols15) if vols15 else 1
    cur_vol   = c15[-2]["v"]
    vol_ratio = cur_vol / avg_vol20 if avg_vol20 > 0 else 0

    return {
        "price": price, "bull15": bull15, "bear15": bear15,
        "bull5": bull5, "bear5": bear5, "j15": j15, "j5": j5,
        "k15": k15, "d15": d15, "k5": k5, "d5": d5,
        "h15": h15, "h5": h5, "b6": b6v, "b14": b14v,
        "range_pct": range_pct, "vol_ratio": vol_ratio,
        "kdj15_bull": kdj15_bull, "kdj15_bear": kdj15_bear,
        "kdj5_bull": kdj5_bull, "kdj5_bear": kdj5_bear,
    }

def decide(d):
    bull15 = d["bull15"]; bear15 = d["bear15"]
    bull5  = d["bull5"];  bear5  = d["bear5"]

    if bull15 >= 3 and bull5 >= 3:
        if d["kdj15_bear"]:
            return "WAIT", "LONG αλλά KDJ DX ❌"
        return "LONG", f"15m {bull15}/4 ✅  5m {bull5}/4 ✅"

    if bear15 >= 3 and bear5 >= 3:
        if d["kdj15_bull"]:
            return "WAIT", "SHORT αλλά KDJ GX ❌"
        return "SHORT", f"15m {bear15}/4 ❌  5m {bear5}/4 ❌"

    return "WAIT", f"15m {bull15}↑{bear15}↓  5m {bull5}↑{bear5}↓"

# ── Paper Trade Check ──────────────────────────────────────
def check_paper_trade(current_price):
    global paper_trade
    if not paper_trade:
        return

    entry     = paper_trade["entry"]
    direction = paper_trade["direction"]
    elapsed   = (time.time() - paper_trade["time"]) / 60

    if direction == "LONG":
        pct = (current_price - entry) / entry * 100
    else:
        pct = (entry - current_price) / entry * 100

    if pct >= TP_PCT:
        log_trade(direction, entry, current_price, pct, "KERDOS ✅", elapsed)
        telegram_send(
            f"📊 PAPER TRADE — KERDOS ✅\n"
            f"────────────────────\n"
            f"{direction} {entry:.4f} → {current_price:.4f}\n"
            f"Αποτέλεσμα: {pct:+.2f}%\n"
            f"Διάρκεια: {elapsed:.0f} λεπτά\n"
            f"⏰ {time.strftime('%H:%M:%S')}"
        )
        paper_trade = None

    elif pct <= -SL_PCT:
        log_trade(direction, entry, current_price, pct, "ZIMIA ❌", elapsed)
        telegram_send(
            f"📊 PAPER TRADE — ZIMIA ❌\n"
            f"────────────────────\n"
            f"{direction} {entry:.4f} → {current_price:.4f}\n"
            f"Αποτέλεσμα: {pct:+.2f}%\n"
            f"Διάρκεια: {elapsed:.0f} λεπτά\n"
            f"⏰ {time.strftime('%H:%M:%S')}"
        )
        paper_trade = None

    else:
        print(f"  📊 PAPER {direction} @ {entry:.4f} | τώρα {current_price:.4f} | {pct:+.2f}% | {elapsed:.0f}λ")

# ── MAIN ──────────────────────────────────────────────────
init_log()
print("=" * 50)
print("  NEAR/USDT Signal Bot v4 + Paper Trading")
print(f"  TP: +{TP_PCT}%  |  SL: -{SL_PCT}%")
print(f"  Log: {LOG_FILE}")
print(f"  Έλεγχος κάθε {INTERVAL} δευτερόλεπτα")
print("=" * 50)

while True:
    try:
        now = time.time()
        t   = time.strftime("%H:%M:%S")

        d = analyze()
        if not d:
            print(f"[{t}] ERR: δεν ήρθαν data")
            time.sleep(INTERVAL)
            continue

        j15_history.append(d["j15"]); j5_history.append(d["j5"])
        price_history.append(d["price"]); bull5_history.append(d["bull5"])
        bull15_history.append(d["bull15"])
        for lst in [j15_history, j5_history, price_history, bull5_history, bull15_history]:
            if len(lst) > 10: lst.pop(0)

        signal, reason = decide(d)

        kdj_tag = "🟢GX" if d["kdj15_bull"] else "🔴DX"
        print(f"[{t}] {d['price']:.4f}  15m:{d['bull15']}↑{d['bear15']}↓  5m:{d['bull5']}↑{d['bear5']}↓  {kdj_tag}  J15={d['j15']:.0f} J5={d['j5']:.0f}  range={d['range_pct']:.0f}%  vol={d['vol_ratio']:.1f}x  → {signal}")

        # ── Paper trade check ──────────────────────────
        check_paper_trade(d["price"])

        # ── Σήμα φίλτρο ───────────────────────────────
        fresh    = (last_signal == "WAIT")
        vol_ok   = d['vol_ratio'] >= 1.5
        long_ok  = (signal == "LONG"  and fresh and d['j15'] < 70 and d['range_pct'] < 80 and d['bull15'] >= 3 and vol_ok)
        short_ok = (signal == "SHORT" and fresh and d['j15'] > 30 and d['range_pct'] > 20 and d['bear15'] >= 3 and vol_ok)

        print(f"  DBG last={last_signal} fresh={fresh} sig={signal} vol={d['vol_ratio']:.1f}x long_ok={long_ok} short_ok={short_ok}")

        if long_ok or short_ok:
            last_signal      = signal
            last_signal_time = now

            # Paper trade — άνοιγμα εικονικής θέσης
            if paper_trade is None:
                paper_trade = {
                    "direction": signal,
                    "entry":     d["price"],
                    "time":      now
                }
                print(f"  📊 PAPER TRADE ΑΝΟΙΞΕ: {signal} @ {d['price']:.4f}")

            if signal == "LONG":
                telegram_send(
                    f"🟢 LONG — NEAR\n"
                    f"────────────────────\n"
                    f"Τιμή: {d['price']:.4f}\n"
                    f"15m: {d['bull15']}↑  5m: {d['bull5']}↑  J15={d['j15']:.0f}\n"
                    f"────────────────────\n"
                    f"Σήμα επιβεβαιώθηκε — κίνηση προς τα ΠΑΝΩ\n"
                    f"⏰ {time.strftime('%H:%M:%S')}"
                )
            else:
                telegram_send(
                    f"🔴 SHORT — NEAR\n"
                    f"────────────────────\n"
                    f"Τιμή: {d['price']:.4f}\n"
                    f"15m: {d['bear15']}↓  5m: {d['bear5']}↓  J15={d['j15']:.0f}\n"
                    f"────────────────────\n"
                    f"Σήμα επιβεβαιώθηκε — κίνηση προς τα ΚΑΤΩ\n"
                    f"⏰ {time.strftime('%H:%M:%S')}"
                )

        elif signal == "WAIT" and last_signal in ("LONG", "SHORT"):
            if last_signal == "SHORT" and d['j15'] < 50:
                telegram_send(
                    f"🔄 REVERSAL LONG — NEAR\n"
                    f"────────────────────\n"
                    f"Τιμή: {d['price']:.4f}\n"
                    f"J15={d['j15']:.0f}  range={d['range_pct']:.0f}%\n"
                    f"────────────────────\n"
                    f"Το SHORT τελείωσε — πιθανή ανατροπή ΠΑΝΩ\n"
                    f"⏰ {time.strftime('%H:%M:%S')}"
                )
            elif last_signal == "LONG" and d['j15'] > 50:
                telegram_send(
                    f"🔄 REVERSAL SHORT — NEAR\n"
                    f"────────────────────\n"
                    f"Τιμή: {d['price']:.4f}\n"
                    f"J15={d['j15']:.0f}  range={d['range_pct']:.0f}%\n"
                    f"────────────────────\n"
                    f"Το LONG τελείωσε — πιθανή ανατροπή ΚΑΤΩ\n"
                    f"⏰ {time.strftime('%H:%M:%S')}"
                )
            last_signal = "WAIT"

    except Exception as e:
        print(f"  ERR: {e}")

    time.sleep(INTERVAL)
