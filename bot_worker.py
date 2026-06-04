#!/usr/bin/env python3
"""
Bot worker — τρέχει 24/7 ανεξάρτητα από τον browser.
Γράφει log/trades σε αρχεία που διαβάζει το Streamlit dashboard.
"""
import json
import os
import time
from urllib.request import urlopen, Request as UReq

LOG_FILE    = "/tmp/bot_log.txt"
TRADES_FILE = "/tmp/bot_trades.json"
CONFIG_FILE = "/tmp/bot_config.json"
STOP_FILE   = "/tmp/bot_stop"

BASE = "https://contract.mexc.com"

DEFAULT_CONFIG = {
    "pairs":    ["NEAR_USDT","BTC_USDT","ETH_USDT","SOL_USDT",
                 "BNB_USDT","XRP_USDT","DOGE_USDT","ADA_USDT"],
    "interval": 60,
    "tp_pct":   1.5,
    "sl_pct":   1.0,
}

# ── State per pair ─────────────────────────────────────────
last_signals  = {}
paper_trades  = {}

# ── Helpers ────────────────────────────────────────────────
def add_log(text):
    line = f"[{time.strftime('%H:%M:%S')}]  {text}\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)
    # keep last 600 lines
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > 600:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines[-600:])
    except Exception:
        pass

def save_trades(trades):
    with open(TRADES_FILE, "w", encoding="utf-8") as f:
        json.dump(trades, f, ensure_ascii=False)

def load_trades():
    if os.path.exists(TRADES_FILE):
        try:
            with open(TRADES_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def read_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg

def fetch(url):
    try:
        r = urlopen(UReq(url, headers={"User-Agent":"Mozilla/5.0"}), timeout=10)
        return json.loads(r.read())
    except Exception as e:
        add_log(f"ERR fetch: {e}")
        return None

def get_klines(pair, iv, lim=150):
    d = fetch(f"{BASE}/api/v1/contract/kline/{pair}?interval={iv}&limit={lim}")
    if not d or not d.get("data"): return []
    r = d["data"]
    C=r.get("close",[]); H=r.get("high",[]); L=r.get("low",[])
    O=r.get("open",[]); V=r.get("vol",[])
    return [{"h":float(H[i]),"l":float(L[i]),"c":float(C[i]),
             "o":float(O[i]),"v":float(V[i]) if i<len(V) else 0}
            for i in range(len(C))]

def ema(d, p):
    k=2/(p+1); r=[d[0]]
    for v in d[1:]: r.append(v*k+r[-1]*(1-k))
    return r

def macd(cl, f, s, sg):
    ml=[a-b for a,b in zip(ema(cl,f),ema(cl,s))]
    return [m-s for m,s in zip(ml,ema(ml,sg))]

def kdj(cc, kp):
    K,D,J=[],[],[]
    for i in range(len(cc)):
        w=cc[max(0,i-kp+1):i+1]
        hi=max(c["h"] for c in w); lo=min(c["l"] for c in w)
        rsv=(cc[i]["c"]-lo)/(hi-lo)*100 if hi!=lo else 50
        kv=(2/3)*K[-1]+(1/3)*rsv if K else 50.0
        dv=(2/3)*D[-1]+(1/3)*kv  if D else 50.0
        K.append(kv); D.append(dv); J.append(3*kv-2*dv)
    return K,D,J

def bias(cl, p):
    r=[]
    for i in range(len(cl)):
        if i<p-1: r.append(0.0)
        else:
            ma=sum(cl[i-p+1:i+1])/p; r.append((cl[i]-ma)/ma*100)
    return r

def tg(text):
    tok = os.environ.get("TELEGRAM_TOKEN", "")
    cht = os.environ.get("TELEGRAM_CHAT",  "")
    if not tok or not cht: return
    try:
        payload = json.dumps({"chat_id":cht,"text":text,
                               "disable_web_page_preview":True}).encode()
        urlopen(UReq(f"https://api.telegram.org/bot{tok}/sendMessage",
                     data=payload, headers={"Content-Type":"application/json"},
                     method="POST"), timeout=8)
        add_log("✅ Telegram εστάλη!")
    except Exception as e:
        add_log(f"ERR telegram: {e}")

def analyze(pair):
    c15=get_klines(pair,"Min15",150)
    if not c15: return None
    cl15=[c["c"] for c in c15]; price=cl15[-1]
    e5=ema(cl15,5); h15=macd(cl15,2,41,18)[-1]
    K15,D15,J15=kdj(c15,7); k15=K15[-1]; d15=D15[-1]
    kdj15_bull=k15>d15; kdj15_bear=k15<d15
    b6=bias(cl15,6)[-1]; b14=bias(cl15,14)[-1]
    bull15=sum([price>e5[-1],h15>0,kdj15_bull,b6>0 and b14>0])
    bear15=4-bull15
    c5=get_klines(pair,"Min5",150)
    if not c5: return None
    cl5=[c["c"] for c in c5]
    e5_5=ema(cl5,5); e15_5=ema(cl5,15); h5=macd(cl5,2,19,40)[-1]
    K5,D5,J5=kdj(c5,9); k5=K5[-1]; d5=D5[-1]; kdj5_bull=k5>d5
    bull5=sum([cl5[-1]>e5_5[-1],e5_5[-1]>e15_5[-1],h5>0,kdj5_bull])
    bear5=4-bull5
    hi20=max(c["h"] for c in c15[-20:]); lo20=min(c["l"] for c in c15[-20:])
    rng=(price-lo20)/(hi20-lo20)*100 if hi20!=lo20 else 50
    vols=[c["v"] for c in c15[-22:-2]]
    avg=sum(vols)/len(vols) if vols else 1
    vol=c15[-2]["v"]/avg if avg>0 else 0
    return {"price":price,"bull15":bull15,"bear15":bear15,
            "bull5":bull5,"bear5":bear5,"j15":J15[-1],
            "rng":rng,"vol":vol,"kdj15_bull":kdj15_bull,
            "kdj15_bear":kdj15_bear,"kdj5_bull":kdj5_bull}

def decide(d):
    if d["bull15"]>=3 and d["bull5"]>=3:
        if d["kdj15_bear"]: return "WAIT","LONG αλλά KDJ DX ❌"
        return "LONG",f"15m {d['bull15']}/4 ✅  5m {d['bull5']}/4 ✅"
    if d["bear15"]>=3 and d["bear5"]>=3:
        if d["kdj15_bull"]: return "WAIT","SHORT αλλά KDJ GX ❌"
        return "SHORT",f"15m {d['bear15']}/4 ❌  5m {d['bear5']}/4 ❌"
    return "WAIT",f"15m {d['bull15']}↑{d['bear15']}↓  5m {d['bull5']}↑{d['bear5']}↓"

# ── Main loop ──────────────────────────────────────────────
add_log("="*44)
add_log("Bot worker ξεκίνησε")
add_log("="*44)

trades = load_trades()

while True:
    # Παύση αν υπάρχει STOP_FILE
    if os.path.exists(STOP_FILE):
        time.sleep(5)
        continue

    cfg = read_config()
    pairs    = cfg["pairs"]
    interval = int(cfg["interval"])
    tp_pct   = float(cfg["tp_pct"])
    sl_pct   = float(cfg["sl_pct"])

    add_log(f"── Κύκλος {time.strftime('%H:%M:%S')} | {len(pairs)} ζεύγη ──")

    for pair in pairs:
        try:
            d = analyze(pair)
            if not d:
                add_log(f"{pair}: ERR no data"); continue

            sig, _ = decide(d)
            tag = "GX" if d["kdj15_bull"] else "DX"
            add_log(f"{pair} {d['price']:.4f}  "
                    f"15m:{d['bull15']}↑{d['bear15']}↓  "
                    f"5m:{d['bull5']}↑{d['bear5']}↓  "
                    f"{tag}  J15={d['j15']:.0f}  "
                    f"range={d['rng']:.0f}%  vol={d['vol']:.1f}x  → {sig}")

            # paper trade
            pt = paper_trades.get(pair)
            if pt:
                en=pt["entry"]; dr=pt["direction"]
                el=(time.time()-pt["time"])/60
                pct=(d["price"]-en)/en*100 if dr=="LONG" else (en-d["price"])/en*100
                if pct >= tp_pct:
                    add_log(f"  📊 {pair} {dr} {pct:+.2f}% KERDOS ✅")
                    trades.append({"Ώρα":time.strftime("%H:%M:%S"),"Ζεύγος":pair,
                        "Κατ/νση":dr,"Είσοδος":f"{en:.4f}","Έξοδος":f"{d['price']:.4f}",
                        "% P&L":f"{pct:+.2f}%","Αποτ/μα":"KERDOS ✅","Διάρκεια":f"{el:.0f}λ"})
                    save_trades(trades)
                    tg(f"📊 {pair} PAPER — KERDOS ✅\n{dr} {en:.4f}→{d['price']:.4f}\n{pct:+.2f}%  {el:.0f}λ")
                    paper_trades.pop(pair, None)
                elif pct <= -sl_pct:
                    add_log(f"  📊 {pair} {dr} {pct:+.2f}% ZIMIA ❌")
                    trades.append({"Ώρα":time.strftime("%H:%M:%S"),"Ζεύγος":pair,
                        "Κατ/νση":dr,"Είσοδος":f"{en:.4f}","Έξοδος":f"{d['price']:.4f}",
                        "% P&L":f"{pct:+.2f}%","Αποτ/μα":"ZIMIA ❌","Διάρκεια":f"{el:.0f}λ"})
                    save_trades(trades)
                    tg(f"📊 {pair} PAPER — ZIMIA ❌\n{dr} {en:.4f}→{d['price']:.4f}\n{pct:+.2f}%  {el:.0f}λ")
                    paper_trades.pop(pair, None)
                else:
                    add_log(f"  📊 {pair} {dr} @ {en:.4f} | {pct:+.2f}% | {el:.0f}λ")

            # signal
            last  = last_signals.get(pair, "WAIT")
            fresh = last == "WAIT"
            vok   = d["vol"] >= 1.5
            long_ok  = sig=="LONG"  and fresh and d["j15"]<70 and d["rng"]<80 and d["bull15"]>=3 and vok
            short_ok = sig=="SHORT" and fresh and d["j15"]>30 and d["rng"]>20 and d["bear15"]>=3 and vok

            if long_ok or short_ok:
                last_signals[pair] = sig
                if pair not in paper_trades:
                    paper_trades[pair] = {"direction":sig,"entry":d["price"],"time":time.time()}
                    add_log(f"  📊 {pair} PAPER ΑΝΟΙΞΕ: {sig} @ {d['price']:.4f}")
                tg((f"🟢 LONG — {pair}\nΤιμή: {d['price']:.4f}\n"
                    f"15m:{d['bull15']}↑  5m:{d['bull5']}↑  J15={d['j15']:.0f}\n"
                    f"ΠΑΝΩ ⏰{time.strftime('%H:%M:%S')}")
                   if sig=="LONG" else
                   (f"🔴 SHORT — {pair}\nΤιμή: {d['price']:.4f}\n"
                    f"15m:{d['bear15']}↓  5m:{d['bear5']}↓  J15={d['j15']:.0f}\n"
                    f"ΚΑΤΩ ⏰{time.strftime('%H:%M:%S')}"))
            elif sig=="WAIT" and last in ("LONG","SHORT"):
                if last=="SHORT" and d["j15"]<50:
                    tg(f"🔄 REVERSAL LONG — {pair}\n{d['price']:.4f}  J15={d['j15']:.0f}")
                elif last=="LONG" and d["j15"]>50:
                    tg(f"🔄 REVERSAL SHORT — {pair}\n{d['price']:.4f}  J15={d['j15']:.0f}")
                last_signals[pair] = "WAIT"

        except Exception as e:
            add_log(f"{pair} ERR: {e}")

    time.sleep(interval)
