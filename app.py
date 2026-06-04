#!/usr/bin/env python3
"""
NEAR/USDT Signal Bot v4 — Web Interface (Streamlit)
"""
import json
import os
import threading
import time
from collections import deque
from urllib.request import urlopen, Request as UReq
import streamlit as st

# ── Shared state — mutable containers, NEVER reassigned ────
# Streamlit re-runs the script on every interaction, so plain
# variables like `_thread = None` would reset each time.
# Dicts/objects are safe because their *contents* persist.
_shared = {
    "log":        deque(maxlen=400),
    "trades":     [],
    "log_lock":   threading.Lock(),
    "trade_lock": threading.Lock(),
    "thread":     None,
    "stop":       None,
}

def _log(text):
    with _shared["log_lock"]:
        _shared["log"].append(f"[{time.strftime('%H:%M:%S')}]  {text}")

def _bot_running():
    t = _shared["thread"]
    return t is not None and t.is_alive()

# ── Page config ────────────────────────────────────────────
st.set_page_config(page_title="NEAR/USDT Signal Bot", page_icon="📈", layout="wide")

# ── Sidebar ────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Ρυθμίσεις")
    telegram_token = st.text_input("Telegram Bot Token",
        value=os.environ.get("TELEGRAM_TOKEN", ""), type="password",
        placeholder="123456789:AAF...")
    telegram_chat  = st.text_input("Telegram Chat ID",
        value=os.environ.get("TELEGRAM_CHAT", ""), placeholder="-100123456789")
    st.divider()
    PAIRS = [
        "NEAR_USDT", "BTC_USDT", "ETH_USDT", "SOL_USDT",
        "BNB_USDT", "XRP_USDT", "DOGE_USDT", "ADA_USDT",
        "AVAX_USDT", "DOT_USDT", "LINK_USDT", "MATIC_USDT",
        "TRX_USDT", "SHIB_USDT", "LTC_USDT", "ATOM_USDT",
    ]
    pair     = st.selectbox("Ζεύγος", options=PAIRS, index=PAIRS.index("NEAR_USDT"))
    interval = st.number_input("Interval (δευτ.)", value=60, min_value=10, step=10)
    tp_pct   = st.number_input("Take Profit %",    value=1.5, min_value=0.1, step=0.1, format="%.1f")
    sl_pct   = st.number_input("Stop Loss %",      value=1.0, min_value=0.1, step=0.1, format="%.1f")

# ── Header ─────────────────────────────────────────────────
st.title("📈 NEAR/USDT Signal Bot v4")
running = _bot_running()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Κατάσταση", "🟢 Τρέχει" if running else "🔴 Σταματημένο")
c2.metric("Take Profit", f"+{tp_pct}%")
c3.metric("Stop Loss",   f"-{sl_pct}%")
c4.metric("Ζεύγος",      pair)

b1, b2, _ = st.columns([1, 1, 6])
start_clicked = b1.button("▶ Εκκίνηση", type="primary", disabled=running, use_container_width=True)
stop_clicked  = b2.button("⏹ Παύση",                   disabled=not running, use_container_width=True)

# ── Bot ────────────────────────────────────────────────────
def run_bot(cfg, stop_ev):
    TOKEN = cfg["token"]; CHAT = cfg["chat"]
    BASE  = "https://contract.mexc.com"
    PAIR  = cfg["pair"];  INTERVAL = int(cfg["interval"])
    TP    = float(cfg["tp_pct"]); SL = float(cfg["sl_pct"])

    last_signal = "WAIT"
    paper_trade = None

    def fetch(url):
        try:
            r = urlopen(UReq(url, headers={"User-Agent":"Mozilla/5.0"}), timeout=10)
            return json.loads(r.read())
        except Exception as e:
            _log(f"ERR fetch: {e}"); return None

    def klines(iv, lim=150):
        d = fetch(f"{BASE}/api/v1/contract/kline/{PAIR}?interval={iv}&limit={lim}")
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
        sl=ema(ml,sg); return [m-s for m,s in zip(ml,sl)]

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
        if not TOKEN or not CHAT: return
        try:
            payload=json.dumps({"chat_id":CHAT,"text":text,
                                 "disable_web_page_preview":True}).encode()
            urlopen(UReq(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                         data=payload,headers={"Content-Type":"application/json"},
                         method="POST"),timeout=8)
            _log("✅ Telegram εστάλη!")
        except Exception as e:
            _log(f"ERR telegram: {e}")

    def analyze():
        c15=klines("Min15",150)
        if not c15: return None
        cl15=[c["c"] for c in c15]; price=cl15[-1]
        e5=ema(cl15,5); h15=macd(cl15,2,41,18)[-1]
        K15,D15,J15=kdj(c15,7)
        k15=K15[-1]; d15=D15[-1]
        kdj15_bull=k15>d15; kdj15_bear=k15<d15
        b6=bias(cl15,6)[-1]; b14=bias(cl15,14)[-1]
        bull15=sum([price>e5[-1],h15>0,kdj15_bull,b6>0 and b14>0])
        bear15=4-bull15
        c5=klines("Min5",150)
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
                "bull5":bull5,"bear5":bear5,"j15":J15[-1],"j5":J5[-1],
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

    _log("="*44)
    _log(f"Bot ξεκίνησε — {PAIR}  TP:{TP}%  SL:{SL}%")
    _log("="*44)

    while not stop_ev.is_set():
        try:
            d=analyze()
            if not d:
                _log("ERR: δεν ήρθαν data"); stop_ev.wait(INTERVAL); continue

            sig,reason=decide(d)
            tag="GX" if d["kdj15_bull"] else "DX"
            _log(f"{d['price']:.4f}  15m:{d['bull15']}↑{d['bear15']}↓  "
                 f"5m:{d['bull5']}↑{d['bear5']}↓  {tag}  J15={d['j15']:.0f}  "
                 f"range={d['rng']:.0f}%  vol={d['vol']:.1f}x  → {sig}")

            if paper_trade:
                en=paper_trade["entry"]; dr=paper_trade["direction"]
                el=(time.time()-paper_trade["time"])/60
                pct=(d["price"]-en)/en*100 if dr=="LONG" else (en-d["price"])/en*100
                if pct>=TP:
                    _log(f"📊 PAPER {dr} {en:.4f}→{d['price']:.4f} {pct:+.2f}% KERDOS ✅")
                    with _shared["trade_lock"]:
                        _shared["trades"].append({"Ώρα":time.strftime("%H:%M:%S"),
                            "Κατ/νση":dr,"Είσοδος":f"{en:.4f}","Έξοδος":f"{d['price']:.4f}",
                            "% P&L":f"{pct:+.2f}%","Αποτ/μα":"KERDOS ✅","Διάρκεια":f"{el:.0f}λ"})
                    tg(f"📊 PAPER — KERDOS ✅\n{dr} {en:.4f}→{d['price']:.4f}\n{pct:+.2f}%  {el:.0f}λ")
                    paper_trade=None
                elif pct<=-SL:
                    _log(f"📊 PAPER {dr} {en:.4f}→{d['price']:.4f} {pct:+.2f}% ZIMIA ❌")
                    with _shared["trade_lock"]:
                        _shared["trades"].append({"Ώρα":time.strftime("%H:%M:%S"),
                            "Κατ/νση":dr,"Είσοδος":f"{en:.4f}","Έξοδος":f"{d['price']:.4f}",
                            "% P&L":f"{pct:+.2f}%","Αποτ/μα":"ZIMIA ❌","Διάρκεια":f"{el:.0f}λ"})
                    tg(f"📊 PAPER — ZIMIA ❌\n{dr} {en:.4f}→{d['price']:.4f}\n{pct:+.2f}%  {el:.0f}λ")
                    paper_trade=None
                else:
                    _log(f"📊 PAPER {dr} @ {en:.4f} | {d['price']:.4f} | {pct:+.2f}% | {el:.0f}λ")

            fresh=last_signal=="WAIT"; vok=d["vol"]>=1.5
            long_ok =(sig=="LONG"  and fresh and d["j15"]<70 and d["rng"]<80 and d["bull15"]>=3 and vok)
            short_ok=(sig=="SHORT" and fresh and d["j15"]>30 and d["rng"]>20 and d["bear15"]>=3 and vok)

            if long_ok or short_ok:
                last_signal=sig
                if paper_trade is None:
                    paper_trade={"direction":sig,"entry":d["price"],"time":time.time()}
                    _log(f"📊 PAPER TRADE ΑΝΟΙΞΕ: {sig} @ {d['price']:.4f}")
                tg((f"🟢 LONG — NEAR\nΤιμή: {d['price']:.4f}\n"
                    f"15m:{d['bull15']}↑  5m:{d['bull5']}↑  J15={d['j15']:.0f}\nΠΑΝΩ ⏰{time.strftime('%H:%M:%S')}")
                   if sig=="LONG" else
                   (f"🔴 SHORT — NEAR\nΤιμή: {d['price']:.4f}\n"
                    f"15m:{d['bear15']}↓  5m:{d['bear5']}↓  J15={d['j15']:.0f}\nΚΑΤΩ ⏰{time.strftime('%H:%M:%S')}"))
            elif sig=="WAIT" and last_signal in ("LONG","SHORT"):
                if last_signal=="SHORT" and d["j15"]<50:
                    tg(f"🔄 REVERSAL LONG\n{d['price']:.4f}  J15={d['j15']:.0f}")
                elif last_signal=="LONG" and d["j15"]>50:
                    tg(f"🔄 REVERSAL SHORT\n{d['price']:.4f}  J15={d['j15']:.0f}")
                last_signal="WAIT"

        except Exception as e:
            _log(f"ERR: {e}")

        stop_ev.wait(INTERVAL)

    _log("⏹ Bot σταμάτησε.")


# ── Start / Stop handlers ──────────────────────────────────
if start_clicked:
    tok = telegram_token or os.environ.get("TELEGRAM_TOKEN","")
    cht = telegram_chat  or os.environ.get("TELEGRAM_CHAT", "")
    if not tok or not cht:
        st.error("⚠️  Άνοιξε το sidebar (>>) και συμπλήρωσε Token + Chat ID.")
    else:
        ev = threading.Event()
        t  = threading.Thread(
            target=run_bot,
            args=({"token":tok,"chat":cht,"pair":pair,
                   "interval":interval,"tp_pct":tp_pct,"sl_pct":sl_pct}, ev),
            daemon=True)
        _shared["stop"]   = ev
        _shared["thread"] = t
        t.start()
        st.rerun()

if stop_clicked:
    ev = _shared.get("stop")
    if ev: ev.set()
    st.rerun()

# ── Tabs ───────────────────────────────────────────────────
tab_log, tab_trades = st.tabs(["📋 Live Log", "📊 Paper Trades"])

with tab_log:
    with _shared["log_lock"]:
        lines = list(_shared["log"])
    st.text_area("Log output", value="\n".join(reversed(lines[-100:])),
                 height=440, label_visibility="hidden")

with tab_trades:
    with _shared["trade_lock"]:
        trades = list(_shared["trades"])
    if trades:
        st.dataframe(trades, use_container_width=True, hide_index=True)
        wins = sum(1 for t in trades if "KERDOS" in t["Αποτ/μα"])
        c1,c2,c3 = st.columns(3)
        c1.metric("Σύνολο", len(trades))
        c2.metric("Κέρδη ✅",  wins)
        c3.metric("Ζημίες ❌", len(trades)-wins)
    else:
        st.info("Δεν υπάρχουν trades ακόμα.")

# ── Auto-refresh κάθε 5 δευτ. όταν τρέχει ─────────────────
if _bot_running():
    time.sleep(5)
    st.rerun()
