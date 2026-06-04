#!/usr/bin/env python3
"""
NEAR/USDT Signal Bot v4 — GUI Launcher
Εισαγωγή ρυθμίσεων, εκκίνηση/παύση bot, live output.
"""
import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

CONFIG_FILE = os.path.expanduser("~/.near_usdt_bot_config.json")

DEFAULT_CONFIG = {
    "telegram_token": "",
    "telegram_chat":  "",
    "pair":           "NEAR_USDT",
    "interval":       "60",
    "tp_pct":         "1.5",
    "sl_pct":         "1.0",
}

# ── Config helpers ─────────────────────────────────────────
def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

# ── Bot runner (runs in background thread) ─────────────────
def run_bot(cfg, log_fn, stop_event):
    """
    Inline bot loop — uses the same logic as near_usdt_signal_bot.py
    but reads settings from cfg dict and writes output via log_fn().
    """
    import json as _json
    import csv
    from urllib.request import urlopen, Request as UReq

    TELEGRAM_TOKEN = cfg["telegram_token"]
    TELEGRAM_CHAT  = cfg["telegram_chat"]
    BASE           = "https://contract.mexc.com"
    PAIR           = cfg["pair"]
    INTERVAL       = int(cfg["interval"])
    TP_PCT         = float(cfg["tp_pct"])
    SL_PCT         = float(cfg["sl_pct"])
    LOG_FILE       = os.path.expanduser("~/Downloads/paper_trades.csv")

    last_signal      = "WAIT"
    last_signal_time = 0
    paper_trade      = None

    j15_history    = []
    j5_history     = []
    price_history  = []
    bull5_history  = []
    bull15_history = []

    # ── CSV init ──────────────────────────────────────────
    os.makedirs(os.path.expanduser("~/Downloads"), exist_ok=True)
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([
                "Ωρα", "Ζεύγος", "Κατεύθυνση", "Τιμή Εισόδου",
                "Τιμή Εξόδου", "% Αποτέλεσμα", "Αποτέλεσμα", "Διάρκεια (λεπτά)"
            ])

    def log_trade(direction, entry, exit_price, pct, result, dur):
        with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([
                time.strftime("%H:%M:%S"), PAIR, direction,
                f"{entry:.4f}", f"{exit_price:.4f}",
                f"{pct:+.2f}%", result, f"{dur:.0f}"
            ])
        log_fn(f"  📊 PAPER: {direction} {entry:.4f}→{exit_price:.4f} {pct:+.2f}% {result} ({dur:.0f}λ)")

    def fetch(url):
        try:
            r = urlopen(UReq(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=10)
            return _json.loads(r.read())
        except Exception as e:
            log_fn(f"  ERR fetch: {e}")
            return None

    def get_klines(interval, limit=150):
        d = fetch(f"{BASE}/api/v1/contract/kline/{PAIR}?interval={interval}&limit={limit}")
        if not d or not d.get("data"): return []
        rows = d["data"]
        C = rows.get("close", []); H = rows.get("high", [])
        L = rows.get("low", []);   O = rows.get("open", [])
        V = rows.get("vol", [])
        return [{"h": float(H[i]), "l": float(L[i]), "c": float(C[i]),
                 "o": float(O[i]), "v": float(V[i]) if i < len(V) else 0}
                for i in range(len(C))]

    def ema(data, period):
        k = 2 / (period + 1)
        r = [data[0]]
        for v in data[1:]:
            r.append(v * k + r[-1] * (1 - k))
        return r

    def macd_calc(closes, fast, slow, sig):
        ml = [f - s for f, s in zip(ema(closes, fast), ema(closes, slow))]
        sl = ema(ml, sig)
        return ml, sl, [m - s for m, s in zip(ml, sl)]

    def kdj_calc(candles, k_period):
        K, D, J = [], [], []
        for i in range(len(candles)):
            w  = candles[max(0, i - k_period + 1):i + 1]
            hi = max(c["h"] for c in w)
            lo = min(c["l"] for c in w)
            rsv = (candles[i]["c"] - lo) / (hi - lo) * 100 if hi != lo else 50
            kv  = (2/3) * K[-1] + (1/3) * rsv if K else 50.0
            dv  = (2/3) * D[-1] + (1/3) * kv  if D else 50.0
            K.append(kv); D.append(dv); J.append(3*kv - 2*dv)
        return K, D, J

    def bias_ma(closes, period):
        result = []
        for i in range(len(closes)):
            if i < period - 1:
                result.append(0.0)
            else:
                ma = sum(closes[i - period + 1:i + 1]) / period
                result.append((closes[i] - ma) / ma * 100)
        return result

    def telegram_send(text):
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
            log_fn("  ⚠️  Telegram token/chat δεν έχουν οριστεί")
            return
        try:
            payload = _json.dumps({
                "chat_id": TELEGRAM_CHAT,
                "text": text,
                "disable_web_page_preview": True
            }).encode("utf-8")
            req = UReq(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            urlopen(req, timeout=8)
            log_fn("  ✅ Telegram εστάλη!")
        except Exception as e:
            log_fn(f"  ERR telegram: {e}")

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
            price > e5_15[-1], h15 > 0, kdj15_bull,
            b6v > 0 and b14v > 0
        ])
        bear15 = 4 - bull15

        c5 = get_klines("Min5", 150)
        if not c5: return None
        cl5  = [c["c"] for c in c5]
        e5_5        = ema(cl5, 5)
        e15_5       = ema(cl5, 15)
        _, _, hist5 = macd_calc(cl5, 2, 19, 40)
        K5, D5, J5  = kdj_calc(c5, 9)

        j5 = J5[-1]; k5 = K5[-1]; d5 = D5[-1]
        h5 = hist5[-1]
        kdj5_bull = k5 > d5

        bull5 = sum([
            cl5[-1] > e5_5[-1], e5_5[-1] > e15_5[-1],
            h5 > 0, kdj5_bull
        ])
        bear5 = 4 - bull5

        hi20 = max(c["h"] for c in c15[-20:])
        lo20 = min(c["l"] for c in c15[-20:])
        range_pct = (price - lo20) / (hi20 - lo20) * 100 if hi20 != lo20 else 50

        vols15    = [c["v"] for c in c15[-22:-2]]
        avg_vol20 = sum(vols15) / len(vols15) if vols15 else 1
        vol_ratio = c15[-2]["v"] / avg_vol20 if avg_vol20 > 0 else 0

        return {
            "price": price, "bull15": bull15, "bear15": bear15,
            "bull5": bull5, "bear5": bear5, "j15": j15, "j5": j5,
            "k15": k15, "d15": d15, "k5": k5, "d5": d5,
            "h15": h15, "h5": h5, "b6": b6v, "b14": b14v,
            "range_pct": range_pct, "vol_ratio": vol_ratio,
            "kdj15_bull": kdj15_bull, "kdj15_bear": kdj15_bear,
            "kdj5_bull": kdj5_bull, "kdj5_bear": k5 < d5,
        }

    def decide(d):
        if d["bull15"] >= 3 and d["bull5"] >= 3:
            if d["kdj15_bear"]:
                return "WAIT", "LONG αλλά KDJ DX ❌"
            return "LONG", f"15m {d['bull15']}/4 ✅  5m {d['bull5']}/4 ✅"
        if d["bear15"] >= 3 and d["bear5"] >= 3:
            if d["kdj15_bull"]:
                return "WAIT", "SHORT αλλά KDJ GX ❌"
            return "SHORT", f"15m {d['bear15']}/4 ❌  5m {d['bear5']}/4 ❌"
        return "WAIT", f"15m {d['bull15']}↑{d['bear15']}↓  5m {d['bull5']}↑{d['bear5']}↓"

    def check_paper_trade(current_price):
        nonlocal paper_trade
        if not paper_trade:
            return
        entry     = paper_trade["entry"]
        direction = paper_trade["direction"]
        elapsed   = (time.time() - paper_trade["time"]) / 60
        pct = (current_price - entry) / entry * 100 if direction == "LONG" \
              else (entry - current_price) / entry * 100

        if pct >= TP_PCT:
            log_trade(direction, entry, current_price, pct, "KERDOS ✅", elapsed)
            telegram_send(
                f"📊 PAPER TRADE — KERDOS ✅\n────────────────────\n"
                f"{direction} {entry:.4f} → {current_price:.4f}\n"
                f"Αποτέλεσμα: {pct:+.2f}%\nΔιάρκεια: {elapsed:.0f} λεπτά\n"
                f"⏰ {time.strftime('%H:%M:%S')}"
            )
            paper_trade = None
        elif pct <= -SL_PCT:
            log_trade(direction, entry, current_price, pct, "ZIMIA ❌", elapsed)
            telegram_send(
                f"📊 PAPER TRADE — ZIMIA ❌\n────────────────────\n"
                f"{direction} {entry:.4f} → {current_price:.4f}\n"
                f"Αποτέλεσμα: {pct:+.2f}%\nΔιάρκεια: {elapsed:.0f} λεπτά\n"
                f"⏰ {time.strftime('%H:%M:%S')}"
            )
            paper_trade = None
        else:
            log_fn(f"  📊 PAPER {direction} @ {entry:.4f} | τώρα {current_price:.4f} | {pct:+.2f}% | {elapsed:.0f}λ")

    # ── Main loop ─────────────────────────────────────────
    log_fn("=" * 48)
    log_fn(f"  NEAR/USDT Signal Bot v4 + Paper Trading")
    log_fn(f"  TP: +{TP_PCT}%  |  SL: -{SL_PCT}%  |  {PAIR}")
    log_fn(f"  Log: {LOG_FILE}")
    log_fn("=" * 48)

    while not stop_event.is_set():
        try:
            now = time.time()
            t   = time.strftime("%H:%M:%S")
            d   = analyze()
            if not d:
                log_fn(f"[{t}] ERR: δεν ήρθαν data")
                stop_event.wait(INTERVAL)
                continue

            for lst, key in [(j15_history, "j15"), (j5_history, "j5"),
                             (price_history, "price"), (bull5_history, "bull5"),
                             (bull15_history, "bull15")]:
                lst.append(d[key])
                if len(lst) > 10: lst.pop(0)

            signal, reason = decide(d)
            kdj_tag = "GX" if d["kdj15_bull"] else "DX"
            log_fn(
                f"[{t}] {d['price']:.4f}  "
                f"15m:{d['bull15']}↑{d['bear15']}↓  5m:{d['bull5']}↑{d['bear5']}↓  "
                f"{kdj_tag}  J15={d['j15']:.0f} J5={d['j5']:.0f}  "
                f"range={d['range_pct']:.0f}%  vol={d['vol_ratio']:.1f}x  → {signal}"
            )

            check_paper_trade(d["price"])

            fresh    = (last_signal == "WAIT")
            vol_ok   = d["vol_ratio"] >= 1.5
            long_ok  = (signal == "LONG"  and fresh and d["j15"] < 70
                        and d["range_pct"] < 80 and d["bull15"] >= 3 and vol_ok)
            short_ok = (signal == "SHORT" and fresh and d["j15"] > 30
                        and d["range_pct"] > 20 and d["bear15"] >= 3 and vol_ok)

            if long_ok or short_ok:
                last_signal      = signal
                last_signal_time = now
                if paper_trade is None:
                    paper_trade = {"direction": signal, "entry": d["price"], "time": now}
                    log_fn(f"  📊 PAPER TRADE ΑΝΟΙΞΕ: {signal} @ {d['price']:.4f}")
                if signal == "LONG":
                    telegram_send(
                        f"🟢 LONG — NEAR\n────────────────────\n"
                        f"Τιμή: {d['price']:.4f}\n"
                        f"15m: {d['bull15']}↑  5m: {d['bull5']}↑  J15={d['j15']:.0f}\n"
                        f"────────────────────\n"
                        f"Σήμα επιβεβαιώθηκε — κίνηση προς τα ΠΑΝΩ\n"
                        f"⏰ {time.strftime('%H:%M:%S')}"
                    )
                else:
                    telegram_send(
                        f"🔴 SHORT — NEAR\n────────────────────\n"
                        f"Τιμή: {d['price']:.4f}\n"
                        f"15m: {d['bear15']}↓  5m: {d['bear5']}↓  J15={d['j15']:.0f}\n"
                        f"────────────────────\n"
                        f"Σήμα επιβεβαιώθηκε — κίνηση προς τα ΚΑΤΩ\n"
                        f"⏰ {time.strftime('%H:%M:%S')}"
                    )
            elif signal == "WAIT" and last_signal in ("LONG", "SHORT"):
                if last_signal == "SHORT" and d["j15"] < 50:
                    telegram_send(
                        f"🔄 REVERSAL LONG — NEAR\n────────────────────\n"
                        f"Τιμή: {d['price']:.4f}\n"
                        f"J15={d['j15']:.0f}  range={d['range_pct']:.0f}%\n"
                        f"────────────────────\n"
                        f"Το SHORT τελείωσε — πιθανή ανατροπή ΠΑΝΩ\n"
                        f"⏰ {time.strftime('%H:%M:%S')}"
                    )
                elif last_signal == "LONG" and d["j15"] > 50:
                    telegram_send(
                        f"🔄 REVERSAL SHORT — NEAR\n────────────────────\n"
                        f"Τιμή: {d['price']:.4f}\n"
                        f"J15={d['j15']:.0f}  range={d['range_pct']:.0f}%\n"
                        f"────────────────────\n"
                        f"Το LONG τελείωσε — πιθανή ανατροπή ΚΑΤΩ\n"
                        f"⏰ {time.strftime('%H:%M:%S')}"
                    )
                last_signal = "WAIT"

        except Exception as e:
            log_fn(f"  ERR: {e}")

        stop_event.wait(INTERVAL)

    log_fn("  ⏹  Bot σταμάτησε.")


# ── GUI ────────────────────────────────────────────────────
class BotApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("NEAR/USDT Signal Bot v4")
        self.resizable(True, True)
        self.configure(bg="#1e1e2e")

        self._stop_event  = None
        self._bot_thread  = None
        self._cfg         = load_config()

        self._build_ui()
        self._load_fields()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Build UI ──────────────────────────────────────────
    def _build_ui(self):
        BG    = "#1e1e2e"
        PANEL = "#2a2a3e"
        ACC   = "#89b4fa"
        FG    = "#cdd6f4"
        RED   = "#f38ba8"
        GREEN = "#a6e3a1"

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame",       background=PANEL)
        style.configure("TLabel",       background=PANEL, foreground=FG,  font=("Helvetica", 10))
        style.configure("Header.TLabel",background=PANEL, foreground=ACC, font=("Helvetica", 11, "bold"))
        style.configure("TEntry",       fieldbackground="#313244", foreground=FG,
                        insertcolor=FG, borderwidth=0)
        style.configure("Start.TButton", background=GREEN, foreground="#1e1e2e",
                        font=("Helvetica", 10, "bold"), padding=6)
        style.configure("Stop.TButton",  background=RED,   foreground="#1e1e2e",
                        font=("Helvetica", 10, "bold"), padding=6)
        style.configure("Save.TButton",  background=ACC,   foreground="#1e1e2e",
                        font=("Helvetica", 10, "bold"), padding=6)
        style.map("Start.TButton", background=[("active", "#94e2a1")])
        style.map("Stop.TButton",  background=[("active", "#f5a0b5")])
        style.map("Save.TButton",  background=[("active", "#96c2f5")])

        # ── Settings panel ────────────────────────────────
        settings_frame = ttk.Frame(self, padding=16)
        settings_frame.pack(fill="x", padx=12, pady=(12, 0))

        ttk.Label(settings_frame, text="⚙  Ρυθμίσεις Bot", style="Header.TLabel"
                  ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))

        fields = [
            ("Telegram Token",   "telegram_token", 0, 1, 60, True),
            ("Telegram Chat ID", "telegram_chat",  1, 1, 20, False),
            ("Ζεύγος",           "pair",           0, 3,  16, False),
            ("Interval (δευτ.)", "interval",       1, 3,   6, False),
            ("TP %",             "tp_pct",         0, 5,   6, False),
            ("SL %",             "sl_pct",         1, 5,   6, False),
        ]

        self._vars = {}
        for label, key, row, col, width, show_star in fields:
            lbl_col = col - 1
            ttk.Label(settings_frame, text=label).grid(
                row=row + 1, column=lbl_col, sticky="e", padx=(0, 6), pady=4)
            var = tk.StringVar()
            show = "*" if show_star else ""
            ent = ttk.Entry(settings_frame, textvariable=var, width=width, show=show)
            ent.grid(row=row + 1, column=col, sticky="w", padx=(0, 20), pady=4)
            self._vars[key] = var

        # Toggle token visibility
        self._show_token = False
        self._token_entry = None
        for widget in settings_frame.winfo_children():
            if isinstance(widget, ttk.Entry):
                info = widget.grid_info()
                if info.get("row") == 1 and info.get("column") == 1:
                    self._token_entry = widget
                    break

        eye_btn = tk.Button(
            settings_frame, text="👁", bg=PANEL, fg=FG, bd=0,
            activebackground=PANEL, cursor="hand2",
            command=self._toggle_token
        )
        eye_btn.grid(row=1, column=2, sticky="w")

        # ── Buttons ───────────────────────────────────────
        btn_frame = ttk.Frame(self, padding=(16, 8))
        btn_frame.pack(fill="x", padx=12)

        self._start_btn = ttk.Button(
            btn_frame, text="▶  Εκκίνηση Bot",
            style="Start.TButton", command=self._start_bot)
        self._start_btn.pack(side="left", padx=(0, 8))

        self._stop_btn = ttk.Button(
            btn_frame, text="⏹  Παύση",
            style="Stop.TButton", command=self._stop_bot, state="disabled")
        self._stop_btn.pack(side="left", padx=(0, 8))

        ttk.Button(
            btn_frame, text="💾  Αποθήκευση",
            style="Save.TButton", command=self._save).pack(side="left")

        self._status_lbl = tk.Label(
            btn_frame, text="● Σταματημένο", bg=BG, fg=RED,
            font=("Helvetica", 10, "bold"))
        self._status_lbl.pack(side="right")

        # ── Log output ────────────────────────────────────
        log_frame = tk.Frame(self, bg=BG)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(4, 12))

        tk.Label(log_frame, text="📋  Live Output", bg=BG, fg=ACC,
                 font=("Helvetica", 10, "bold")).pack(anchor="w")

        self._log = scrolledtext.ScrolledText(
            log_frame, bg="#11111b", fg=FG, font=("Courier", 9),
            state="disabled", wrap="word", relief="flat", bd=0)
        self._log.pack(fill="both", expand=True)
        self._log.tag_config("long",  foreground="#a6e3a1")
        self._log.tag_config("short", foreground="#f38ba8")
        self._log.tag_config("paper", foreground="#f9e2af")
        self._log.tag_config("err",   foreground="#f38ba8")

        self.geometry("780x540")

    # ── Helpers ───────────────────────────────────────────
    def _toggle_token(self):
        if not self._token_entry: return
        self._show_token = not self._show_token
        self._token_entry.config(show="" if self._show_token else "*")

    def _load_fields(self):
        for key, var in self._vars.items():
            var.set(self._cfg.get(key, ""))

    def _collect_cfg(self):
        return {k: v.get().strip() for k, v in self._vars.items()}

    def _save(self):
        cfg = self._collect_cfg()
        save_config(cfg)
        self._cfg = cfg
        messagebox.showinfo("Αποθήκευση", "Οι ρυθμίσεις αποθηκεύτηκαν!")

    def _log_line(self, text):
        def _append():
            self._log.config(state="normal")
            tag = None
            tl  = text.lower()
            if "long" in tl:    tag = "long"
            elif "short" in tl: tag = "short"
            elif "paper" in tl: tag = "paper"
            elif "err" in tl:   tag = "err"
            self._log.insert("end", text + "\n", tag or "")
            self._log.see("end")
            self._log.config(state="disabled")
        self.after(0, _append)

    # ── Bot control ───────────────────────────────────────
    def _start_bot(self):
        cfg = self._collect_cfg()
        if not cfg["telegram_token"] or not cfg["telegram_chat"]:
            messagebox.showwarning(
                "Ελλιπείς ρυθμίσεις",
                "Συμπλήρωσε το Telegram Token και το Chat ID πριν ξεκινήσεις.")
            return
        save_config(cfg)
        self._cfg = cfg

        self._stop_event = threading.Event()
        self._bot_thread = threading.Thread(
            target=run_bot,
            args=(cfg, self._log_line, self._stop_event),
            daemon=True
        )
        self._bot_thread.start()

        self._start_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._status_lbl.config(text="● Τρέχει", fg="#a6e3a1")

    def _stop_bot(self):
        if self._stop_event:
            self._stop_event.set()
        self._start_btn.config(state="normal")
        self._stop_btn.config(state="disabled")
        self._status_lbl.config(text="● Σταματημένο", fg="#f38ba8")

    def _on_close(self):
        self._stop_bot()
        self.destroy()


if __name__ == "__main__":
    app = BotApp()
    app.mainloop()
