"""
S05-OVERLAY — Daily Signal Generator (semi-automatic deployment)
=================================================================
Roz chalega (GitHub Actions ya manually). Karta kya hai:
  1. Latest NSE data download (yfinance)
  2. S05-Overlay ke exact rules aaj ke din pe lagata hai
  3. Tumhare portfolio (portfolio.json) se compare karke BUY/SELL/HOLD nikalta hai
  4. Position sizing + stops calculate karta hai (wahi backtest wala logic)
  5. Telegram pe report bhejta hai + signals.json / dashboard data save karta hai

NOTE: Ye SIGNALS deta hai. Orders TUM khud broker pe daloge. Paisa script nahi chalata.
"""
import os, json, math, sys, datetime as dt
import numpy as np
import pandas as pd

# ----------------------------------------------------------------- CONFIG (₹50k, 5 positions)
CAPITAL_DEFAULT = 50_000.0
CFG = dict(
    risk_per_trade = 0.0075,      # 0.75% equity risk per trade
    max_positions  = 5,           # ₹50k -> 5 slots (₹10k each ~) instead of 12
    max_pos_weight = 0.22,        # up to 22% per name at this size
    min_trade_value= 5_000,
    min_price      = 20.0,
    stop_atr_mult  = 3.0,         # initial stop = entry - 3*ATR14
    prox_52w       = 0.95,        # within 5% of 52-week high
    mom_top_pct    = 0.75,        # 6-month momentum in top quartile
    universe_size  = 200,         # point-in-time top-N by turnover
    etf_symbol     = "NIFTYBEES", # idle-cash overlay (regime up)
)

UNIVERSE_FILE  = "universe.txt"     # NSE symbols (no .NS). Falls back to a built-in list.
PORTFOLIO_FILE = "portfolio.json"   # your current holdings (script reads + suggests updates)
SIGNALS_FILE   = "signals.json"     # machine-readable output (dashboard consumes this)

# ----------------------------------------------------------------- INDICATORS
def sma(s, n): return s.rolling(n).mean()
def atr14(h, l, c):
    tr = np.maximum(h-l, np.maximum((h-c.shift()).abs(), (l-c.shift()).abs()))
    return tr.ewm(alpha=1/14, adjust=False).mean()
def roc(s, n): return s/s.shift(n) - 1.0

# ----------------------------------------------------------------- DATA
def load_universe():
    if os.path.exists(UNIVERSE_FILE):
        return [x.strip().upper() for x in open(UNIVERSE_FILE) if x.strip()]
    return ["RELIANCE","TCS","HDFCBANK","ICICIBANK","INFY","ITC","SBIN","BHARTIARTL","LT",
            "AXISBANK","KOTAKBANK","HINDUNILVR","MARUTI","SUNPHARMA","TITAN","BAJFINANCE",
            "ASIANPAINT","NESTLEIND","ULTRACEMCO","M&M","TATAMOTORS","TATASTEEL","HCLTECH",
            "ADANIENT","NTPC","POWERGRID","JSWSTEEL","COALINDIA","TECHM","WIPRO","DRREDDY",
            "CIPLA","BAJAJFINSV","EICHERMOT","BRITANNIA","HEROMOTOCO","BPCL","TRENT","DIVISLAB",
            "GRASIM","HINDALCO","APOLLOHOSP","TATACONSUM","ADANIPORTS","BAJAJ-AUTO","SBILIFE",
            "HDFCLIFE","INDUSINDBK","DLF","SIEMENS","PIDILITIND","HAVELLS","DABUR","GODREJCP",
            "AMBUJACEM","BANKBARODA","CHOLAFIN","TVSMOTOR","VBL","ZOMATO","DMART","PNB","GAIL",
            "IOC","LUPIN","MARICO","BERGEPAINT","MUTHOOTFIN","PAGEIND","PIIND","ABB","BOSCHLTD",
            "TORNTPHARM","NAUKRI","INDIGO","HAL","BEL","IRCTC","JINDALSTEL","POLYCAB","SRF",
            "TATAPOWER","LTIM","PERSISTENT","COFORGE","MAXHEALTH","ZYDUSLIFE","MOTHERSON",
            "BHARATFORG","CUMMINSIND","ASTRAL","SUPREMEIND","DIXON","JUBLFOOD","PATANJALI"]

def download(symbols):
    import yfinance as yf
    tickers = [s + ".NS" for s in symbols] + ["^NSEI"]
    df = yf.download(tickers, period="2y", auto_adjust=True, progress=False, group_by="ticker")
    out = {}
    for s in symbols:
        try:
            sub = df[s + ".NS"].dropna(how="all")
            if len(sub) > 260:
                out[s] = sub[["Open","High","Low","Close","Volume"]]
        except Exception:
            pass
    nifty = df["^NSEI"]["Close"].dropna()
    return out, nifty

# ----------------------------------------------------------------- SIGNAL ENGINE
def compute_signals(data, nifty, capital, portfolio):
    closes = pd.DataFrame({s: d["Close"] for s, d in data.items()})
    highs  = pd.DataFrame({s: d["High"]  for s, d in data.items()})
    lows   = pd.DataFrame({s: d["Low"]   for s, d in data.items()})
    vols   = pd.DataFrame({s: d["Volume"]for s, d in data.items()})
    idx = closes.index
    nc = nifty.reindex(idx).ffill()

    regime_up = nc.iloc[-1] > nc.rolling(200).mean().iloc[-1]
    sma200 = sma(closes, 200)
    hi252  = closes.rolling(252).max()
    roc126 = roc(closes, 126)
    turnover = (closes * vols).rolling(20).mean()
    liquid = (turnover.rank(axis=1, ascending=False) <= CFG["universe_size"]) & \
             (turnover > 5e6) & (closes > CFG["min_price"])
    # 6-month momentum percentile within investable universe (today)
    mom_today = roc126.iloc[-1].where(liquid.iloc[-1])
    mom_pct = mom_today.rank(pct=True)

    near_hi = closes.iloc[-1] >= CFG["prox_52w"] * hi252.shift(1).iloc[-1]
    up_day  = closes.iloc[-1] > closes.iloc[-2]
    above   = closes.iloc[-1] > sma200.iloc[-1]
    entry_ok = near_hi & (mom_pct > CFG["mom_top_pct"]) & up_day & above & liquid.iloc[-1] & regime_up

    atr = {s: atr14(data[s]["High"], data[s]["Low"], data[s]["Close"]).iloc[-1] for s in data}

    held = set(portfolio.get("positions", {}).keys())
    n_held = len(held)
    free_slots = max(0, CFG["max_positions"] - n_held)

    # ---- SELL: held stock fell below 85% of its 52w high, or regime turned down
    sells = []
    for s, p in portfolio.get("positions", {}).items():
        if s not in closes.columns:
            continue
        px = closes[s].iloc[-1]
        h52 = hi252[s].iloc[-1]
        reason = None
        if not regime_up:
            reason = "Market regime DOWN (Nifty < 200-DMA) — sab band"
        elif px < 0.85 * h52:
            reason = f"52w-high (₹{h52:.0f}) se 15% neeche gira"
        elif px <= p.get("stop", 0):
            reason = f"Stop-loss hit (₹{p.get('stop',0):.0f})"
        if reason:
            entry = p.get("entry_price", px)
            sells.append(dict(symbol=s, price=round(float(px),2),
                              entry_price=entry, pnl_pct=round((px/entry-1)*100,1),
                              reason=reason))

    # ---- BUY: rank eligible candidates by momentum, take top free_slots
    buys = []
    if regime_up and free_slots > 0:
        cand = mom_today[entry_ok.fillna(False)].sort_values(ascending=False)
        cand = [s for s in cand.index if s not in held][:free_slots]
        for s in cand:
            px = float(closes[s].iloc[-1]); a = float(atr[s])
            if a <= 0: continue
            stop = px - CFG["stop_atr_mult"] * a
            if stop <= 0: continue
            risk_sh = (capital * CFG["risk_per_trade"]) / (px - stop)
            cap_sh  = (capital * CFG["max_pos_weight"]) / px
            sh = int(math.floor(min(risk_sh, cap_sh)))
            if sh * px < CFG["min_trade_value"] or sh < 1: continue
            buys.append(dict(symbol=s, price=round(px,2), shares=sh,
                             stop=round(stop,2), value=round(sh*px,0),
                             momentum_6m=round(float(roc126[s].iloc[-1])*100,1),
                             pct_from_52wh=round((px/hi252[s].iloc[-1]-1)*100,1)))

    invested = sum(p.get("shares",0)*float(closes[s].iloc[-1])
                   for s,p in portfolio.get("positions",{}).items() if s in closes.columns)
    idle = max(0.0, capital - invested)
    buy_value = sum(b["value"] for b in buys)
    cash_after_buys = max(0.0, idle - buy_value)
    if regime_up:
        cash_action = dict(
            where="NIFTYBEES (Nifty 50 ETF)",
            why="Market UP hai — idle cash ETF mein rakho taaki market ke saath badhe (overlay).",
            note="Jab agla stock BUY signal aaye, utna NIFTYBEES bech ke stock kharidna.")
    else:
        cash_action = dict(
            where="Liquid Fund / Short-duration Gilt",
            why="Market DOWN hai — capital surakshit rakho, ETF nahi. ~6% milega, kabhi bhi nikal sakte ho.",
            note="Jab Nifty 200-DMA paar kare, tab NIFTYBEES mein shift karna.")
    return dict(
        date=str(idx[-1].date()),
        regime="UP ✅ (trades allowed)" if regime_up else "DOWN 🛑 (no new buys — cash defensive)",
        regime_up=bool(regime_up),
        positions_held=n_held, free_slots=free_slots,
        buys=buys, sells=sells,
        idle_cash=round(idle,0),
        cash_after_buys=round(cash_after_buys,0),
        cash_action=cash_action,
        overlay=(CFG["etf_symbol"] if regime_up else "Liquid fund / short-duration gilt"),
        nifty=round(float(nc.iloc[-1]),1),
        nifty_200dma=round(float(nc.rolling(200).mean().iloc[-1]),1),
    )


# ----------------------------------------------------------------- AI COMMENTARY (Gemini + fallback)
def rule_based_commentary(r):
    """Free, always-works fallback. Reads the signal data and explains it plainly."""
    gap = (r["nifty"]/r["nifty_200dma"] - 1) * 100
    if not r["regime_up"]:
        c = (f"Market downtrend mein hai — Nifty ({r['nifty']:,.0f}) apne 200-DMA "
             f"({r['nifty_200dma']:,.0f}) se {abs(gap):.1f}% neeche. System ne sahi cash bachaya hai; "
             f"momentum systems mein bear market ka matlab nuksaan se bachna hai. "
             f"Jab tak Nifty 200-DMA paar nahi karta, koi naya buy nahi aayega — cash bhi ek position hai.")
    elif r["buys"]:
        names = ", ".join(b["symbol"] for b in r["buys"])
        c = (f"Market uptrend mein hai (Nifty 200-DMA se {gap:+.1f}%). Aaj {len(r['buys'])} naya "
             f"momentum leader signal mein aaya: {names}. Ye 52-week high ke paas, top-quartile "
             f"momentum wale stocks hain. Stop-loss zaroor lagao jaisa signal mein diya hai — "
             f"risk har trade pe sirf 0.75% rakha gaya hai.")
    elif r["sells"]:
        c = (f"Kuch positions exit ho rahi hain — ya to 52-week high se 15% gir gaye ya regime badla. "
             f"Momentum systems mein kamzor stocks jaldi chhodna hi edge hai. Baaki holdings hold karo.")
    else:
        held = r["positions_held"]
        c = (f"Market uptrend mein hai (Nifty 200-DMA se {gap:+.1f}%) par aaj koi naya qualifying "
             f"setup nahi — {held} position hold karo. Naye signal ka intezaar; force karke trade mat dhoondo.")
    return c

def gemini_commentary(r):
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        return None
    import urllib.request, json as _json
    facts = (f"Date {r['date']}. Nifty {r['nifty']}, 200-DMA {r['nifty_200dma']}, "
             f"regime {'UP' if r['regime_up'] else 'DOWN'}. "
             f"Positions held {r['positions_held']}/{r['positions_held']+r['free_slots']}. "
             f"Buys: {[b['symbol'] for b in r['buys']]}. Sells: {[s['symbol'] for s in r['sells']]}. "
             f"Idle cash Rs{r['idle_cash']:.0f}.")
    prompt = ("You are a disciplined quant assistant for an Indian equity momentum swing system "
              "called S05-Overlay (52-week-high momentum, 200-DMA regime gate). "
              "Write a SHORT 2-3 sentence plain-language daily note in simple Hinglish explaining "
              "today's situation to the trader. RULES: Only explain the given facts. Do NOT invent "
              "news, prices, or predictions. Do NOT recommend any stock the system did not signal. "
              "Do NOT override the system. Be calm and educational.\n\nFacts: " + facts)
    body = _json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"maxOutputTokens": 200, "temperature": 0.4}}).encode()
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           "gemini-2.0-flash:generateContent?key=" + key)
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        resp = _json.loads(urllib.request.urlopen(req, timeout=30).read())
        txt = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
        return txt if txt else None
    except Exception as e:
        print("Gemini failed, using rule-based fallback:", str(e)[:150])
        return None

def get_commentary(r):
    return gemini_commentary(r) or rule_based_commentary(r)

# ----------------------------------------------------------------- REPORT + TELEGRAM
def format_report(r):
    L = [f"📊 *S05-Overlay Daily Signals*", f"_{r['date']}_", ""]
    L.append(f"*Market:* {r['regime']}")
    L.append(f"Nifty {r['nifty']:,.0f} | 200-DMA {r['nifty_200dma']:,.0f}")
    L.append(f"Positions: {r['positions_held']}/{CFG['max_positions']} | Free slots: {r['free_slots']}")
    L.append("")
    if r["sells"]:
        L.append("🔴 *BECHO (SELL):*")
        for s in r["sells"]:
            L.append(f"  • {s['symbol']} @ ₹{s['price']} (P&L {s['pnl_pct']:+.1f}%) — {s['reason']}")
        L.append("")
    if r["buys"]:
        L.append("🟢 *KHARIDO (BUY):*")
        for b in r["buys"]:
            L.append(f"  • {b['symbol']}: {b['shares']} sh @ ₹{b['price']} "
                     f"(≈₹{b['value']:,.0f}) | Stop ₹{b['stop']} | mom {b['momentum_6m']:+.0f}%")
        L.append("")
    if not r["buys"] and not r["sells"]:
        L.append("✋ Aaj koi action nahi — HOLD.")
        L.append("")
    ca = r.get("cash_action", {})
    parked = r.get("cash_after_buys", r["idle_cash"])
    L.append("💰 *AAJ CASH KAHAN RAKHO:*")
    L.append(f"  ₹{parked:,.0f} → *{ca.get('where','-')}*")
    L.append(f"  _{ca.get('why','')}_")
    if ca.get("note"): L.append(f"  ↳ {ca['note']}")
    L.append("")
    if r.get("commentary"):
        L.append("🤖 *Analysis:*")
        L.append(r["commentary"])
        L.append("")
    L.append("_Signals only. Orders khud broker pe daalo. Not investment advice._")
    return "\n".join(L)

def send_telegram(text):
    token = (os.environ.get("TELEGRAM_TOKEN") or "").strip()
    chat  = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        print("(Telegram secrets not set — skipping send)")
        return
    import urllib.request, urllib.parse, json as _json
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # try Markdown first; if Telegram rejects (400), retry as plain text
    for mode in ("Markdown", None):
        payload = dict(chat_id=chat, text=text)
        if mode: payload["parse_mode"] = mode
        data = urllib.parse.urlencode(payload).encode()
        try:
            r = urllib.request.urlopen(url, data=data, timeout=20)
            print("Telegram sent OK" + (" (plain)" if mode is None else ""))
            return
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="ignore")
            print(f"Telegram {mode or 'plain'} attempt failed: {e.code} {body[:200]}")
        except Exception as e:
            print("Telegram failed:", e); return

# ----------------------------------------------------------------- MAIN
def main():
    capital = float(os.environ.get("CAPITAL", CAPITAL_DEFAULT))
    portfolio = {"positions": {}}
    if os.path.exists(PORTFOLIO_FILE):
        portfolio = json.load(open(PORTFOLIO_FILE))
    print("Downloading data ...")
    data, nifty = download(load_universe())
    print(f"  {len(data)} symbols loaded")
    r = compute_signals(data, nifty, capital, portfolio)
    r["commentary"] = get_commentary(r)
    report = format_report(r)
    print("\n" + report + "\n")
    json.dump(r, open(SIGNALS_FILE, "w"), indent=2)
    # append to history for the dashboard equity/chart
    hist = []
    if os.path.exists("signals_history.json"):
        hist = json.load(open("signals_history.json"))
    hist = [h for h in hist if h.get("date") != r["date"]] + [r]
    json.dump(hist[-260:], open("signals_history.json", "w"), indent=2)
    send_telegram(report)

if __name__ == "__main__":
    main()
