#!/usr/bin/env python3
"""
NEAR/USDT Signal Bot v4 — Web Interface (Streamlit)
Auto-start αν υπάρχουν credentials, τρέχει όλα τα επιλεγμένα ζεύγη.
"""
import json
import os
import time
from urllib.request import urlopen, Request as UReq
import streamlit as st

st.set_page_config(page_title="Signal Bot v4", page_icon="📈", layout="wide")

ALL_PAIRS = ["NEAR_USDT","BTC_USDT","ETH_USDT","SOL_USDT",
             "BNB_USDT","XRP_USDT","DOGE_USDT","ADA_USDT"]

# ── Session state init ─────────────────────────────────────
if "running"       not in st.session_state: st.session_state.running       = False
if "log"           not in st.session_state: st.session_state.log           = []
if "trades"        not in st.session_state: st.session_state.trades        = []
if "last_signals"  not in st.session_state: st.session_state.last_signals  = {}  # per pair
if "paper_trades"  not in st.session_state: st.session_state.paper_trades  = {}  # per pair

# ── Sidebar ────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Ρυθμίσεις")
    token = st.text_input("Telegram Bot Token",
        value=os.environ.get("TELEGRAM_TOKEN", ""), type="password",
        placeholder="123456789:AAF...")
    chat = st.text_input("Telegram Chat ID",
        value=os.environ.get("TELEGRAM_CHAT", ""), placeholder="-100123456789")
    st.divider()
    selected_pairs = st.multiselect("Ζεύγη", options=ALL_PAIRS, default=ALL_PAIRS)
    interval = st.number_input("Interval (δευτ.)", value=60, min_value=10, step=10)
    tp_pct   = st.number_input("Take Profit %",    value=1.5, min_value=0.1, step=0.1, format="%.1f")
    sl_pct   = st.number_input("Stop Loss %",      value=1.0, min_value=0.1, step=0.1, format="%.1f")

# ── Auto-start αν credentials υπάρχουν στο Railway ────────
tok = token or os.environ.get("TELEGRAM_TOKEN", "")
cht = chat  or os.environ.get("TELEGRAM_CHAT",  "")
if tok and cht and not st.session_state.running:
    st.session_state.running = True
    st.session_state.log = []

# ── Header ─────────────────────────────────────────────────
st.title("📈 Signal Bot v4")
c1, c2, c3 = st.columns(3)
c1.metric("Κατάσταση",  "🟢 Τρέχει" if st.session_state.running else "🔴 Σταματημένο")
c2.metric("Take Profit", f"+{tp_pct}%")
c3.metric("Stop Loss",   f"-{sl_pct}%")

b1, b2, _ = st.columns([1, 1, 6])
start_clicked = b1.button("▶ Εκκίνηση", type="primary",
    disabled=st.session_state.running, use_container_width=True)
stop_clicked  = b2.button("⏹ Παύση",
    disabled=not st.session_state.running, use_container_width=True)

if start_clicked:
    if not tok or not cht:
        st.error("⚠️  Συμπλήρωσε Token + Chat ID στο sidebar (>>).")
    else:
        st.session_state.running      = True
        st.session_state.log          = []
        st.session_state.last_signals = {}
        st.session_state.paper_trades = {}
        st.rerun()

if stop_clicked:
    st.session_state.running = False
    st.rerun()

if not selected_pairs:
    st.warning("Επέλεξε τουλάχιστον ένα ζεύγος.")
    st.stop()

# ── Tabs ───────────────────────────────────────────────────
tab_log, tab_trades = st.tabs(["📋 Live Log", "📊 Paper Trades"])
with tab_log:
    log_ph = st.empty()
with tab_trades:
    trades_ph = st.empty()

def render():
    lines = list(st.session_state.log)[-120:]
    log_ph.text_area("Log output", value="\n".join(reversed(lines)),
                     height=420, label_visibility="hidden")
    if st.session_state.trades:
        trades_ph.dataframe(st.session_state.trades, use_container_width=True, hide_index=True)
        wins = sum(1 for t in st.session_state.trades if "KERDOS" in t["Αποτ/μα"])
        ca, cb, cc = st.columns(3)
        ca.metric("Σύνολο",   len(st.session_state.trades))
        cb.metric("Κέρδη ✅",  wins)
        cc.metric("Ζημίες ❌", len(st.session_state.trades)-wins)
    else:
        trades_ph.info("Δεν υπάρχουν trades ακόμα.")

render()

if not st.session_state.running:
    st.stop()

# ── Bot helpers ────────────────────────────────────────────
BASE = "https://contract.mexc.com"

def add_log(text):
    st.session_state.log.append(f"[{time.strftime('%H:%M:%S')}]  {text}")

def fetch(url):
    try:
        r = urlopen(UReq(url, headers={"User-Agent":"Mozilla/5.0"}), timeout=10)
        return json.loads(r.read())
    except Exception as e:
        add_log(f"ERR fetch: {e}"); return None

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
    if not tok or not cht: return
    try:
        payload=json.dumps({"chat_id":cht,"text":text,
                             "disable_web_page_preview":True}).encode()
        urlopen(UReq(f"https://api.telegram.org/bot{tok}/sendMessage",
                     data=payload,headers={"Content-Type":"application/json"},
                     method="POST"),timeout=8)
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

# ── Ανάλυση για κάθε ζεύγος ────────────────────────────────
add_log(f"── Κύκλος {time.strftime('%H:%M:%S')} | {len(selected_pairs)} ζεύγη ──")

for pair in selected_pairs:
    d = analyze(pair)
    if not d:
        add_log(f"{pair}: ERR no data"); continue

    sig, reason = decide(d)
    tag = "GX" if d["kdj15_bull"] else "DX"
    add_log(f"{pair} {d['price']:.4f}  15m:{d['bull15']}↑{d['bear15']}↓  "
            f"5m:{d['bull5']}↑{d['bear5']}↓  {tag}  J15={d['j15']:.0f}  "
            f"range={d['rng']:.0f}%  vol={d['vol']:.1f}x  → {sig}")

    # paper trade check per pair
    pt = st.session_state.paper_trades.get(pair)
    if pt:
        en=pt["entry"]; dr=pt["direction"]
        el=(time.time()-pt["time"])/60
        pct=(d["price"]-en)/en*100 if dr=="LONG" else (en-d["price"])/en*100
        if pct >= tp_pct:
            add_log(f"  📊 {pair} PAPER {dr} {pct:+.2f}% KERDOS ✅")
            st.session_state.trades.append({"Ώρα":time.strftime("%H:%M:%S"),
                "Ζεύγος":pair,"Κατ/νση":dr,"Είσοδος":f"{en:.4f}",
                "Έξοδος":f"{d['price']:.4f}","% P&L":f"{pct:+.2f}%",
                "Αποτ/μα":"KERDOS ✅","Διάρκεια":f"{el:.0f}λ"})
            tg(f"📊 {pair} PAPER — KERDOS ✅\n{dr} {en:.4f}→{d['price']:.4f}\n{pct:+.2f}%  {el:.0f}λ")
            st.session_state.paper_trades.pop(pair, None)
        elif pct <= -sl_pct:
            add_log(f"  📊 {pair} PAPER {dr} {pct:+.2f}% ZIMIA ❌")
            st.session_state.trades.append({"Ώρα":time.strftime("%H:%M:%S"),
                "Ζεύγος":pair,"Κατ/νση":dr,"Είσοδος":f"{en:.4f}",
                "Έξοδος":f"{d['price']:.4f}","% P&L":f"{pct:+.2f}%",
                "Αποτ/μα":"ZIMIA ❌","Διάρκεια":f"{el:.0f}λ"})
            tg(f"📊 {pair} PAPER — ZIMIA ❌\n{dr} {en:.4f}→{d['price']:.4f}\n{pct:+.2f}%  {el:.0f}λ")
            st.session_state.paper_trades.pop(pair, None)
        else:
            add_log(f"  📊 {pair} PAPER {dr} @ {en:.4f} | {pct:+.2f}% | {el:.0f}λ")

    # signal filter
    last = st.session_state.last_signals.get(pair, "WAIT")
    fresh = last == "WAIT"
    vok   = d["vol"] >= 1.5
    long_ok  = sig=="LONG"  and fresh and d["j15"]<70 and d["rng"]<80 and d["bull15"]>=3 and vok
    short_ok = sig=="SHORT" and fresh and d["j15"]>30 and d["rng"]>20 and d["bear15"]>=3 and vok

    if long_ok or short_ok:
        st.session_state.last_signals[pair] = sig
        if pair not in st.session_state.paper_trades:
            st.session_state.paper_trades[pair] = {"direction":sig,"entry":d["price"],"time":time.time()}
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
        st.session_state.last_signals[pair] = "WAIT"

render()

# ── Επόμενος κύκλος ────────────────────────────────────────
time.sleep(int(interval))
st.rerun()
