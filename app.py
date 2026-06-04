#!/usr/bin/env python3
"""
NEAR/USDT Signal Bot v4 — Web Interface (Streamlit)
Χρησιμοποιεί module-level globals για thread-safe log.
"""
import json
import os
import threading
import time
from collections import deque
from urllib.request import urlopen, Request as UReq
import streamlit as st

# ── Module-level shared state (persist across reruns) ──────
_log        = deque(maxlen=400)
_log_lock   = threading.Lock()
_trades     = []
_trades_lock = threading.Lock()
_stop_event  = None
_bot_thread  = None
_is_running  = False

def _log_append(text):
    with _log_lock:
        _log.append(f"[{time.strftime('%H:%M:%S')}]  {text}")

def _is_bot_alive():
    return _bot_thread is not None and _bot_thread.is_alive()

# ── Page config ────────────────────────────────────────────
st.set_page_config(
    page_title="NEAR/USDT Signal Bot",
    page_icon="📈",
    layout="wide",
)

# ── Sidebar — Ρυθμίσεις ───────────────────────────────────
with st.sidebar:
    st.title("⚙️ Ρυθμίσεις")

    telegram_token = st.text_input(
        "Telegram Bot Token",
        value=os.environ.get("TELEGRAM_TOKEN", ""),
        type="password",
        placeholder="123456789:AAF...",
    )
    telegram_chat = st.text_input(
        "Telegram Chat ID",
        value=os.environ.get("TELEGRAM_CHAT", ""),
        placeholder="-100123456789",
    )

    st.divider()

    pair     = st.text_input("Ζεύγος",            value="NEAR_USDT")
    interval = st.number_input("Interval (δευτ.)", value=60, min_value=10, step=10)
    tp_pct   = st.number_input("Take Profit %",    value=1.5, min_value=0.1, step=0.1, format="%.1f")
    sl_pct   = st.number_input("Stop Loss %",      value=1.0, min_value=0.1, step=0.1, format="%.1f")

    st.divider()
    st.caption("📄 Trades: paper_trades.csv")

# ── Header ─────────────────────────────────────────────────
st.title("📈 NEAR/USDT Signal Bot v4")

running = _is_bot_alive()
col_status, col_tp, col_sl, col_pair = st.columns(4)
col_status.metric("Κατάσταση", "🟢 Τρέχει" if running else "🔴 Σταματημένο")
col_tp.metric("Take Profit",  f"+{tp_pct}%")
col_sl.metric("Stop Loss",    f"-{sl_pct}%")
col_pair.metric("Ζεύγος",     pair)

# ── Buttons ────────────────────────────────────────────────
btn1, btn2, _ = st.columns([1, 1, 6])
start_clicked = btn1.button("▶ Εκκίνηση", type="primary",
                             disabled=running, use_container_width=True)
stop_clicked  = btn2.button("⏹ Παύση",
                             disabled=not running, use_container_width=True)

# ── Bot logic ──────────────────────────────────────────────
def run_bot(cfg, stop_ev):
    global _is_running
    TOKEN    = cfg["token"]
    CHAT     = cfg["chat"]
    BASE     = "https://contract.mexc.com"
    PAIR     = cfg["pair"]
    INTERVAL = int(cfg["interval"])
    TP_PCT   = float(cfg["tp_pct"])
    SL_PCT   = float(cfg["sl_pct"])

    last_signal = "WAIT"
    paper_trade = None

    def fetch(url):
        try:
            r = urlopen(UReq(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=10)
            return json.loads(r.read())
        except Exception as e:
            _log_append(f"ERR fetch: {e}")
            return None

    def get_klines(iv, limit=150):
        d = fetch(f"{BASE}/api/v1/contract/kline/{PAIR}?interval={iv}&limit={limit}")
        if not d or not d.get("data"): return []
        rows = d["data"]
        C=rows.get("close",[]); H=rows.get("high",[])
        L=rows.get("low",[]);   V=rows.get("vol",[])
        O=rows.get("open",[])
        return [{"h":float(H[i]),"l":float(L[i]),"c":float(C[i]),
                 "o":float(O[i]),"v":float(V[i]) if i<len(V) else 0}
                for i in range(len(C))]

    def ema(data, p):
        k=2/(p+1); r=[data[0]]
        for v in data[1:]: r.append(v*k+r[-1]*(1-k))
        return r

    def macd_calc(cl, fast, slow, sig):
        ml=[f-s for f,s in zip(ema(cl,fast),ema(cl,slow))]
        sl=ema(ml,sig)
        return ml,sl,[m-s for m,s in zip(ml,sl)]

    def kdj_calc(candles, kp):
        K,D,J=[],[],[]
        for i in range(len(candles)):
            w=candles[max(0,i-kp+1):i+1]
            hi=max(c["h"] for c in w); lo=min(c["l"] for c in w)
            rsv=(candles[i]["c"]-lo)/(hi-lo)*100 if hi!=lo else 50
            kv=(2/3)*K[-1]+(1/3)*rsv if K else 50.0
            dv=(2/3)*D[-1]+(1/3)*kv  if D else 50.0
            K.append(kv); D.append(dv); J.append(3*kv-2*dv)
        return K,D,J

    def bias_ma(cl, p):
        r=[]
        for i in range(len(cl)):
            if i<p-1: r.append(0.0)
            else:
                ma=sum(cl[i-p+1:i+1])/p
                r.append((cl[i]-ma)/ma*100)
        return r

    def tg(text):
        if not TOKEN or not CHAT:
            _log_append("⚠️ Telegram credentials λείπουν"); return
        try:
            payload=json.dumps({"chat_id":CHAT,"text":text,
                                 "disable_web_page_preview":True}).encode()
            urlopen(UReq(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                         data=payload,headers={"Content-Type":"application/json"},
                         method="POST"),timeout=8)
            _log_append("✅ Telegram εστάλη!")
        except Exception as e:
            _log_append(f"ERR telegram: {e}")

    def analyze():
        c15=get_klines("Min15",150)
        if not c15: return None
        cl15=[c["c"] for c in c15]; price=cl15[-1]
        e5_15=ema(cl15,5)
        _,_,hist15=macd_calc(cl15,2,41,18)
        K15,D15,J15=kdj_calc(c15,7)
        b6v=bias_ma(cl15,6)[-1]; b14v=bias_ma(cl15,14)[-1]
        k15=K15[-1]; d15=D15[-1]
        kdj15_bull=k15>d15; kdj15_bear=k15<d15
        bull15=sum([price>e5_15[-1],hist15[-1]>0,kdj15_bull,b6v>0 and b14v>0])
        bear15=4-bull15
        c5=get_klines("Min5",150)
        if not c5: return None
        cl5=[c["c"] for c in c5]
        e5_5=ema(cl5,5); e15_5=ema(cl5,15)
        _,_,hist5=macd_calc(cl5,2,19,40)
        K5,D5,J5=kdj_calc(c5,9)
        k5=K5[-1]; d5=D5[-1]; kdj5_bull=k5>d5
        bull5=sum([cl5[-1]>e5_5[-1],e5_5[-1]>e15_5[-1],hist5[-1]>0,kdj5_bull])
        bear5=4-bull5
        hi20=max(c["h"] for c in c15[-20:]); lo20=min(c["l"] for c in c15[-20:])
        range_pct=(price-lo20)/(hi20-lo20)*100 if hi20!=lo20 else 50
        vols=[c["v"] for c in c15[-22:-2]]
        avg_v=sum(vols)/len(vols) if vols else 1
        vol_ratio=c15[-2]["v"]/avg_v if avg_v>0 else 0
        return {"price":price,"bull15":bull15,"bear15":bear15,
                "bull5":bull5,"bear5":bear5,"j15":J15[-1],"j5":J5[-1],
                "range_pct":range_pct,"vol_ratio":vol_ratio,
                "kdj15_bull":kdj15_bull,"kdj15_bear":kdj15_bear,"kdj5_bull":kdj5_bull}

    def decide(d):
        if d["bull15"]>=3 and d["bull5"]>=3:
            if d["kdj15_bear"]: return "WAIT","LONG αλλά KDJ DX ❌"
            return "LONG",f"15m {d['bull15']}/4 ✅  5m {d['bull5']}/4 ✅"
        if d["bear15"]>=3 and d["bear5"]>=3:
            if d["kdj15_bull"]: return "WAIT","SHORT αλλά KDJ GX ❌"
            return "SHORT",f"15m {d['bear15']}/4 ❌  5m {d['bear5']}/4 ❌"
        return "WAIT",f"15m {d['bull15']}↑{d['bear15']}↓  5m {d['bull5']}↑{d['bear5']}↓"

    _log_append("="*44)
    _log_append(f"Bot ξεκίνησε — {PAIR}  TP:{TP_PCT}%  SL:{SL_PCT}%")
    _log_append("="*44)

    while not stop_ev.is_set():
        try:
            d=analyze()
            if not d:
                _log_append("ERR: δεν ήρθαν data από MEXC")
                stop_ev.wait(INTERVAL); continue

            signal,reason=decide(d)
            kdj_tag="GX" if d["kdj15_bull"] else "DX"
            _log_append(
                f"{d['price']:.4f}  "
                f"15m:{d['bull15']}↑{d['bear15']}↓  5m:{d['bull5']}↑{d['bear5']}↓  "
                f"{kdj_tag}  J15={d['j15']:.0f}  "
                f"range={d['range_pct']:.0f}%  vol={d['vol_ratio']:.1f}x  → {signal}"
            )

            if paper_trade:
                entry=paper_trade["entry"]; direction=paper_trade["direction"]
                elapsed=(time.time()-paper_trade["time"])/60
                pct=(d["price"]-entry)/entry*100 if direction=="LONG" \
                    else (entry-d["price"])/entry*100
                if pct>=TP_PCT:
                    res="KERDOS ✅"
                    _log_append(f"📊 PAPER {direction} {entry:.4f}→{d['price']:.4f} {pct:+.2f}% {res}")
                    with _trades_lock:
                        _trades.append({"Ώρα":time.strftime("%H:%M:%S"),
                            "Κατ/νση":direction,"Είσοδος":f"{entry:.4f}",
                            "Έξοδος":f"{d['price']:.4f}","% P&L":f"{pct:+.2f}%",
                            "Αποτ/μα":res,"Διάρκεια":f"{elapsed:.0f}λ"})
                    tg(f"📊 PAPER — KERDOS ✅\n{direction} {entry:.4f}→{d['price']:.4f}\n"
                       f"Αποτέλεσμα: {pct:+.2f}%\nΔιάρκεια: {elapsed:.0f}λ")
                    paper_trade=None
                elif pct<=-SL_PCT:
                    res="ZIMIA ❌"
                    _log_append(f"📊 PAPER {direction} {entry:.4f}→{d['price']:.4f} {pct:+.2f}% {res}")
                    with _trades_lock:
                        _trades.append({"Ώρα":time.strftime("%H:%M:%S"),
                            "Κατ/νση":direction,"Είσοδος":f"{entry:.4f}",
                            "Έξοδος":f"{d['price']:.4f}","% P&L":f"{pct:+.2f}%",
                            "Αποτ/μα":res,"Διάρκεια":f"{elapsed:.0f}λ"})
                    tg(f"📊 PAPER — ZIMIA ❌\n{direction} {entry:.4f}→{d['price']:.4f}\n"
                       f"Αποτέλεσμα: {pct:+.2f}%\nΔιάρκεια: {elapsed:.0f}λ")
                    paper_trade=None
                else:
                    _log_append(f"📊 PAPER {direction} @ {entry:.4f} | {d['price']:.4f} | {pct:+.2f}% | {elapsed:.0f}λ")

            fresh=last_signal=="WAIT"; vol_ok=d["vol_ratio"]>=1.5
            long_ok =(signal=="LONG"  and fresh and d["j15"]<70 and d["range_pct"]<80 and d["bull15"]>=3 and vol_ok)
            short_ok=(signal=="SHORT" and fresh and d["j15"]>30 and d["range_pct"]>20 and d["bear15"]>=3 and vol_ok)

            if long_ok or short_ok:
                last_signal=signal
                if paper_trade is None:
                    paper_trade={"direction":signal,"entry":d["price"],"time":time.time()}
                    _log_append(f"📊 PAPER TRADE ΑΝΟΙΞΕ: {signal} @ {d['price']:.4f}")
                if signal=="LONG":
                    tg(f"🟢 LONG — NEAR\nΤιμή: {d['price']:.4f}\n"
                       f"15m:{d['bull15']}↑  5m:{d['bull5']}↑  J15={d['j15']:.0f}\n"
                       f"Σήμα: κίνηση ΠΑΝΩ ⏰{time.strftime('%H:%M:%S')}")
                else:
                    tg(f"🔴 SHORT — NEAR\nΤιμή: {d['price']:.4f}\n"
                       f"15m:{d['bear15']}↓  5m:{d['bear5']}↓  J15={d['j15']:.0f}\n"
                       f"Σήμα: κίνηση ΚΑΤΩ ⏰{time.strftime('%H:%M:%S')}")
            elif signal=="WAIT" and last_signal in ("LONG","SHORT"):
                if last_signal=="SHORT" and d["j15"]<50:
                    tg(f"🔄 REVERSAL LONG\nΤιμή:{d['price']:.4f} J15={d['j15']:.0f}")
                elif last_signal=="LONG" and d["j15"]>50:
                    tg(f"🔄 REVERSAL SHORT\nΤιμή:{d['price']:.4f} J15={d['j15']:.0f}")
                last_signal="WAIT"

        except Exception as e:
            _log_append(f"ERR: {e}")

        stop_ev.wait(INTERVAL)

    _log_append("⏹ Bot σταμάτησε.")


# ── Button handlers ────────────────────────────────────────
global _stop_event, _bot_thread

if start_clicked:
    tok = telegram_token or os.environ.get("TELEGRAM_TOKEN", "")
    cht = telegram_chat  or os.environ.get("TELEGRAM_CHAT",  "")
    if not tok or not cht:
        st.error("⚠️  Άνοιξε το sidebar (>>) και συμπλήρωσε Token + Chat ID.")
    else:
        _stop_event = threading.Event()
        _bot_thread = threading.Thread(
            target=run_bot,
            args=({"token":tok,"chat":cht,"pair":pair,
                   "interval":interval,"tp_pct":tp_pct,"sl_pct":sl_pct},
                  _stop_event),
            daemon=True
        )
        _bot_thread.start()
        st.rerun()

if stop_clicked:
    if _stop_event:
        _stop_event.set()
    st.rerun()

# ── Tabs ───────────────────────────────────────────────────
tab_log, tab_trades = st.tabs(["📋 Live Log", "📊 Paper Trades"])

with tab_log:
    with _log_lock:
        lines = list(_log)
    log_text = "\n".join(reversed(lines[-100:]))
    st.text_area("", value=log_text, height=440, label_visibility="collapsed")

with tab_trades:
    with _trades_lock:
        trades_copy = list(_trades)
    if trades_copy:
        st.dataframe(trades_copy, use_container_width=True, hide_index=True)
        wins   = sum(1 for t in trades_copy if "KERDOS" in t["Αποτ/μα"])
        losses = len(trades_copy) - wins
        c1, c2, c3 = st.columns(3)
        c1.metric("Σύνολο", len(trades_copy))
        c2.metric("Κέρδη ✅",  wins)
        c3.metric("Ζημίες ❌", losses)
    else:
        st.info("Δεν υπάρχουν trades ακόμα.")

# ── Auto-refresh κάθε 6 δευτερόλεπτα όταν τρέχει ──────────
if _is_bot_alive():
    time.sleep(6)
    st.rerun()
