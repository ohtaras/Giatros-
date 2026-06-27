#!/usr/bin/env python3
"""
Signal Bot Dashboard — διαβάζει από αρχεία που γράφει το bot_worker.py
"""
import json
import os
import subprocess
import sys
import time
import streamlit as st

st.set_page_config(page_title="Signal Bot v4", page_icon="📈", layout="wide")

# ── Auto-launch worker με το ίδιο Python (venv-safe) ───────
_PID_FILE = "/tmp/worker_pid"

def _worker_alive():
    if not os.path.exists(_PID_FILE):
        return False
    try:
        pid = int(open(_PID_FILE).read().strip())
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError):
        return False

if not _worker_alive():
    worker_path = os.path.join(os.path.dirname(__file__), "bot_worker.py")
    proc = subprocess.Popen([sys.executable, worker_path])
    with open(_PID_FILE, "w") as f:
        f.write(str(proc.pid))

LOG_FILE       = "/tmp/bot_log.txt"
TRADES_FILE    = "/tmp/bot_trades.json"
OPEN_FILE      = "/tmp/bot_open_trades.json"
CONFIG_FILE    = "/tmp/bot_config.json"
STOP_FILE      = "/tmp/bot_stop"

ALL_PAIRS = ["NEAR_USDT","BTC_USDT","ETH_USDT","SOL_USDT",
             "BNB_USDT","XRP_USDT","DOGE_USDT","ADA_USDT"]

def bot_running():
    return not os.path.exists(STOP_FILE)

def read_log():
    if not os.path.exists(LOG_FILE): return []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        return f.readlines()

def read_trades():
    if not os.path.exists(TRADES_FILE): return []
    try:
        with open(TRADES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def read_open_trades():
    if not os.path.exists(OPEN_FILE): return []
    try:
        with open(OPEN_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"pairs": ALL_PAIRS, "interval": 60, "tp_pct": 1.5, "sl_pct": 1.0,
            "vol_spike_max": 1.8}

cfg = load_config()

# ── Sidebar ────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Ρυθμίσεις")
    st.caption(f"Token: {'✅ Set' if os.environ.get('TELEGRAM_TOKEN') else '❌ Missing'}")
    st.caption(f"Chat:  {'✅ Set' if os.environ.get('TELEGRAM_CHAT')  else '❌ Missing'}")
    st.divider()
    sel_pairs = st.multiselect("Ζεύγη", options=ALL_PAIRS,
                                default=cfg.get("pairs", ALL_PAIRS))
    interval  = st.number_input("Interval (δευτ.)", value=cfg.get("interval",60),
                                 min_value=10, step=10)
    tp_pct    = st.number_input("Take Profit %",    value=cfg.get("tp_pct",1.5),
                                 min_value=0.1, step=0.1, format="%.1f")
    sl_pct    = st.number_input("Stop Loss %",      value=cfg.get("sl_pct",1.0),
                                 min_value=0.1, step=0.1, format="%.1f")
    vspike_max = st.number_input("Όριο μεταβλητότητας (vol-spike x)",
                                  value=cfg.get("vol_spike_max",1.8),
                                  min_value=1.0, step=0.1, format="%.1f",
                                  help="Αν η τρέχουσα διακύμανση (ATR) ξεπερνά αυτό το πολλαπλάσιο της κανονικής, το σήμα αγνοείται.")
    if st.button("💾 Αποθήκευση ρυθμίσεων", use_container_width=True):
        save_config({"pairs":sel_pairs,"interval":interval,
                     "tp_pct":tp_pct,"sl_pct":sl_pct,
                     "vol_spike_max":vspike_max})
        st.success("Αποθηκεύτηκε!")

# ── Header ─────────────────────────────────────────────────
st.title("📈 Signal Bot v4")
running = bot_running()
c1, c2, c3 = st.columns(3)
c1.metric("Κατάσταση",  "🟢 Τρέχει" if running else "🔴 Παύση")
c2.metric("Take Profit", f"+{tp_pct}%")
c3.metric("Stop Loss",   f"-{sl_pct}%")

b1, b2, _ = st.columns([1, 1, 6])
if b1.button("▶ Εκκίνηση", type="primary", disabled=running, use_container_width=True):
    if os.path.exists(STOP_FILE):
        os.remove(STOP_FILE)
    st.rerun()

if b2.button("⏹ Παύση", disabled=not running, use_container_width=True):
    open(STOP_FILE, "w").close()
    st.rerun()

# ── Tabs ───────────────────────────────────────────────────
tab_log, tab_trades, tab_about, tab_journal = st.tabs(
    ["📋 Live Log", "📊 Paper Trades", "ℹ️ About", "📓 Journal"])

with tab_log:
    lines = read_log()
    st.text_area("Log output",
                 value="".join(reversed(lines[-100:])),
                 height=440, label_visibility="hidden")

with tab_trades:
    open_trades = read_open_trades()
    if open_trades:
        st.subheader("🔓 Ανοικτές Θέσεις")
        now_ts = time.time()
        open_rows = []
        for ot in open_trades:
            el = (now_ts - ot["time"]) / 60
            open_rows.append({
                "Ζεύγος": ot["pair"],
                "Κατ/νση": ot["direction"],
                "Είσοδος": f"{ot['entry']:.4f}",
                "Διάρκεια": f"{el:.0f}λ",
                "Μεταβλητότητα (είσοδος)": f"{ot.get('garch_emoji','🟡')} {ot.get('garch_label','Άγνωστο')}",
                "Κατάσταση": "🔓 Ανοικτό",
            })
        st.dataframe(open_rows, use_container_width=True, hide_index=True)
        st.divider()

    trades = read_trades()
    st.subheader("📋 Κλειστές Θέσεις")
    if trades:
        st.dataframe(trades, use_container_width=True, hide_index=True)
        wins = sum(1 for t in trades if "KERDOS" in t.get("Αποτ/μα",""))
        ca, cb, cc = st.columns(3)
        ca.metric("Σύνολο",   len(trades))
        cb.metric("Κέρδη ✅",  wins)
        cc.metric("Ζημίες ❌", len(trades)-wins)
    else:
        st.info("Δεν υπάρχουν κλειστά trades ακόμα.")

with tab_about:
    st.markdown("""
## 🏝️ Η στρατηγική σαν μεταφορά: Η "Ομάδα Παρατηρητών" σε δύο πύργους

Φαντάσου ότι έχεις **4 παρατηρητές** πάνω σε δύο πύργους παρατήρησης — έναν
ψηλό πύργο (το γράφημα 15 λεπτών, βλέπει τη "μεγάλη εικόνα") και έναν χαμηλό
πύργο (το γράφημα 5 λεπτών, βλέπει "τι γίνεται αυτή τη στιγμή").

### Οι 4 παρατηρητές και τι κοιτάζει ο καθένας

1. **"Ο Μέσος"** (EMA) — κοιτάζει αν το πλοίο (η τιμή) είναι πάνω ή κάτω από
   τη συνηθισμένη του πορεία.
2. **"Ο Ταχύμετρος"** (MACD) — μετράει αν το πλοίο επιταχύνει ή φρενάρει.
3. **"Ο Ρεύμα-μετρητής"** (KDJ) — βλέπει αν το ρεύμα της θάλασσας γύρισε φορά.
4. **"Ο Απόσταση-μετρητής"** (BIAS) — μετράει πόσο έχει απομακρυνθεί το
   πλοίο από τη συνηθισμένη διαδρομή του.

### Η ψηφοφορία

Κάθε παρατηρητής, σε κάθε πύργο, σηκώνει σημαία **πράσινη (πάνω) ή κόκκινη
(κάτω)**. Δεν αρκεί όμως ένας-δύο να σηκώσουν την ίδια σημαία — το bot
περιμένει **τουλάχιστον 3 από τους 4 να συμφωνήσουν, και στους ΔΥΟ πύργους
ταυτόχρονα**. Είναι σαν να λες: *"Δεν παίρνω απόφαση μέχρι να δω και ο
πύργος της μεγάλης εικόνας ΚΑΙ ο πύργος της στιγμής να φωνάζουν το ίδιο
πράγμα."*

### Ο φρουρός στην πύλη

Πριν δοθεί το "πράσινο φως" για να μπει κανείς στη μάχη (να ανοίξει trade),
υπάρχει κι ένας **φρουρός** στην πύλη που ρωτάει:

- *"Υπάρχει αρκετός κόσμος/κίνηση εδώ;"* — όγκος συναλλαγών ≥1.5× το
  συνηθισμένο. Αν είναι έρημος δρόμος, δεν μπαίνει κανείς, μπορεί να είναι
  ψεύτικος συναγερμός.
- *"Μήπως είμαστε ήδη στην άκρη του γκρεμού;"* — ακραίο J15 ή ακραίο εύρος
  τιμής. Αν η τιμή είναι ήδη στα άκρα, ο φρουρός λέει "ρίσκο πολύ μεγάλο,
  περίμενε".
- *"Είναι η θάλασσα ασυνήθιστα ταραγμένη αυτή τη στιγμή;"* — το νεότερο
  μέλος της ομάδας φύλαξης, ο **"Μετρητής Φουρτούνας"** (βασισμένος στο
  ATR). Συγκρίνει το πόσο "πλατιά" κινείται η τιμή τώρα με το πόσο κινείται
  κανονικά. Αν είναι πάνω από ένα όριο (π.χ. 1.8× το φυσιολογικό), ο φρουρός
  αρνείται να αφήσει το πλοίο να μπει — έστω και αν όλοι οι άλλοι
  παρατηρητές συμφωνούν. Αυτό προστατεύει από το να μπαίνει το πλοίο σε
  ξαφνικά, χαοτικά κύματα που το χτυπάνε αμέσως πριν προλάβει να πιάσει
  σταθερή πορεία.
- *"Έχουμε ήδη κάποιον εκεί μέσα;"* — αν υπάρχει ήδη ανοιχτή θέση στο ίδιο
  ζεύγος, δεν στέλνει δεύτερο πλοίο.

### Η μάχη μόλις ξεκινήσει

Μόλις μπει το πλοίο στη μάχη (ανοίξει trade), έχει βάλει εκ των προτέρων
δύο σημάδια στο νερό:

- Μια **σημαία νίκης** στο +1.5% — αν φτάσει εκεί, αποσύρεται με κέρδος.
- Μια **γραμμή υποχώρησης** στο -1.0% — αν η μάχη πάει στραβά μέχρι εκεί,
  αποσύρεται αμέσως για να μην χάσει περισσότερα.

### Γιατί κάποιες φορές χάνει μόνο προς μία πλευρά

Αν το ρεύμα της θάλασσας (η γενική τάση του asset) τραβάει σταθερά προς μία
κατεύθυνση για μεγάλο διάστημα, οι 4 παρατηρητές βλέπουν συνέχεια το ίδιο
χρώμα σημαίας προς αυτή την πλευρά — οπότε το bot στέλνει σχεδόν πάντα
πλοία προς εκείνη την κατεύθυνση. Το πρόβλημα είναι όταν η θάλασσα έχει
**απότομα κύματα** μέσα σε αυτό το ρεύμα: το πλοίο μπαίνει σωστά στην
κατεύθυνση του ρεύματος, αλλά ένα απρόοπτο κύμα το χτυπάει στη γραμμή
υποχώρησης (-1%) πριν προλάβει να πιάσει τη σημαία νίκης. Ο "Μετρητής
Φουρτούνας" υπάρχει ακριβώς για να μειώσει αυτό το ρίσκο, αρνούμενος την
είσοδο όταν η θάλασσα είναι ασυνήθιστα ταραγμένη.

### Ο μετεωρολόγος του πλοίου (καθαρά πληροφοριακό)

Πρόσφατα μπήκε στο πλοίο κι ένας **μετεωρολόγος** (μοντέλο GARCH) που δεν
παίρνει αποφάσεις — απλά γράφει στο ημερολόγιο του πλοίου την πρόγνωσή του
για τη μεταβλητότητα της επόμενης περιόδου: 🟢 Χαμηλή, 🟡 Μέτρια, ή 🔴
Αυξημένη. Αυτή η ένδειξη καταγράφεται στη στιγμή που ανοίγει κάθε θέση και
παραμένει ορατή και μετά το κλείσιμό της, στις Ανοικτές και Κλειστές
Θέσεις — χρήσιμο για να βλέπεις εκ των υστέρων αν τα trades που χάθηκαν
άνοιξαν συχνότερα σε περιόδους αυξημένης αναμενόμενης μεταβλητότητας.
Δεν επηρεάζει το αν θα ανοίξει trade, ούτε το TP/SL — είναι αμιγώς για
ανάλυση.

### Σε μία πρόταση

Το bot ψάχνει στιγμές όπου *πολλοί ανεξάρτητοι παρατηρητές συμφωνούν
ταυτόχρονα, σε δύο χρονικά πλαίσια, με αρκετή κίνηση στην αγορά και χωρίς
ασυνήθιστη φουρτούνα* — και μπαίνει με σταθερό μικρό ρίσκο (1%) και
ελαφρώς μεγαλύτερο στόχο κέρδους (1.5%).
""")

with tab_journal:
    st.markdown("""
## 📓 Journal — Ανάλυση δείγματος 385 trades

**Στιγμιότυπο (baseline) ΠΡΙΝ** προστεθούν το φίλτρο μεταβλητότητας ATR
(vol-spike guard) και ο δείκτης GARCH. Καταγράφεται εδώ σαν σημείο αναφοράς,
για να μπορούμε αργότερα να συγκρίνουμε «πριν vs μετά» τις αλλαγές.

---

### Με NEAR_USDT (385 trades — όλο το δείγμα)

- **Win rate:** 52.5%
- **Συνολικό P&L:** +7.80%
- **Μέσο κέρδος:** +1.07% &nbsp;|&nbsp; **Μέση ζημία:** -1.14%
- Σε ευρώ (υποθετικά 100€/trade): **+7.80€** (κέρδη +216.87€, ζημίες -209.07€)

**Κατάταξη ζευγαριών (με NEAR), κατά P&L:**

| Ζεύγος | Trades | Win rate | P&L |
|---|---|---|---|
| BNB_USDT  | 42 | 59.5% | **+6.60%** |
| ADA_USDT  | 60 | 56.7% | +5.57% |
| SOL_USDT  | 51 | 54.9% | +3.37% |
| DOGE_USDT | 53 | 52.8% | +2.19% |
| XRP_USDT  | 44 | 52.3% | +2.05% |
| ETH_USDT  | 44 | 52.3% | +1.38% |
| BTC_USDT  | 23 | 52.2% | -0.92% |
| **NEAR_USDT** | 68 | **42.6%** | **-12.44%** |

**Ανά 4ωρο (με NEAR):**

| Ζώνη ώρας | Trades | Win rate | P&L |
|---|---|---|---|
| 00-04 | 40 | 52.5% | -1.26% |
| 04-08 | 47 | 61.7% | **+6.59%** |
| 08-12 | 50 | 46.0% | -4.21% |
| 12-16 | 70 | 57.1% | **+12.27%** |
| 16-20 | 138 | 48.6% | **-6.84%** (περισσότερος όγκος, χειρότερο P&L) |
| 20-24 | 40 | 55.0% | +1.25% |

Στο παράθυρο 16:00-20:00, το NEAR_USDT από μόνο του έκανε 19 trades με
21.1% win rate και **-11.64%** P&L — δηλαδή κάλυψε όλη την απώλεια του
παραθύρου. Τα υπόλοιπα ζεύγη ήταν στην πλειοψηφία τους θετικά εκεί
(π.χ. XRP +6.93%, BTC +3.24%, ADA +2.15%, DOGE +1.92%).

---

### Χωρίς NEAR_USDT (317 trades)

- **Win rate:** 54.6%
- **Συνολικό P&L:** +20.24%
- **Profit factor:** 1.12 &nbsp;|&nbsp; **Expectancy:** +0.064%/trade
- **Μέση διάρκεια:** 199 λεπτά
- LONG: 115 trades, 51.3% wr, +7.14%
- SHORT: 202 trades, 56.4% wr, +13.10%

**Ανά 4ωρο (χωρίς NEAR):**

| Ζώνη ώρας | Trades | Win rate | P&L |
|---|---|---|---|
| 00-04 | 33 | 57.6% | +3.49% |
| 04-08 | 41 | 61.0% | +5.63% |
| 08-12 | 36 | 44.4% | **-4.38%** (χειρότερο παράθυρο, ανεξάρτητο από NEAR) |
| 12-16 | 56 | 57.1% | **+10.34%** (καλύτερο παράθυρο) |
| 16-20 | 119 | 52.9% | +4.80% (γυρίζει θετικό όταν αφαιρεθεί το NEAR) |
| 20-24 | 32 | 56.2% | +0.36% |

**Καλύτερες μεμονωμένες ώρες:** 13:00 (13 trades, 69.2% wr, +7.75%) και
14:00 (20 trades, 70.0% wr, +6.73%) — το παράθυρο 12:00-16:00 παράγει
περίπου τη μισή συνολική απόδοση της στρατηγικής όταν αφαιρεθεί το NEAR.

Το παράθυρο 08:00-12:00 παραμένει αδύναμο ανεξάρτητα από το NEAR
(πιθανή μετάβαση ρευστότητας Ασία→Ευρώπη).

---

### Γιατί υποαποδίδει το NEAR_USDT

- Βαριά μεροληψία προς SHORT (72% των trades) — στατιστικά «σωστή»
  λόγω της γενικότερης πτωτικής τάσης, αλλά...
- Υψηλή μεταβλητότητα → πολλά whipsaw stop-outs: 35% των trades
  έκλεισαν σε ≤20 λεπτά, και το 62% από αυτά ήταν ζημίες.
- Συμπέρασμα: όχι σφάλμα λογικής δεικτών, αλλά ότι το σταθερό SL 1%
  είναι πολύ σφιχτό για τη φυσική διακύμανση του ζεύγους.
- Γι' αυτό προστέθηκε το φίλτρο μεταβλητότητας ATR (vol-spike guard)
  αντί να αφαιρεθεί το ζεύγος — στόχος είναι να μπλοκάρει ακριβώς αυτές
  τις ασταθείς στιγμές εισόδου.

---

*Σημείωση: τα παραπάνω αντλήθηκαν από CSV εξαγωγή 385 ιστορικών paper
trades, πριν την προσθήκη του φίλτρου ATR vol-spike και του δείκτη
GARCH. Χρησιμεύει ως σημείο αναφοράς "πριν" για μελλοντική σύγκριση.*
""")

# ── Auto-refresh κάθε 10 δευτ. ────────────────────────────
time.sleep(10)
st.rerun()
