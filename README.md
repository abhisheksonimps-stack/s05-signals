# S05-Overlay — Daily Swing Signals (semi-automatic)

Roz auto: NSE data → S05-Overlay rules → BUY/SELL/HOLD signals → Telegram + dashboard.
**Signals only. Orders aap khud broker pe daalte ho. Not investment advice.**

## Files
- `daily_signals.py` — signal engine (sizing + costs logic = backtest jaisa)
- `universe.txt` — 200 NSE symbols (no .NS)
- `portfolio.json` — aapke holdings (BUY pe add karo, SELL pe hatao)
- `index.html` — dashboard (GitHub Pages pe live website)
- `.github/workflows/daily.yml` — roz 10:00 IST auto-run

## One-time setup (15 min)
1. **Naya GitHub repo banao**, ye saari files usme daalo (upload/drag).
2. **Telegram secrets** daalo: repo → Settings → Secrets and variables → Actions → New repository secret:
   - `TELEGRAM_TOKEN` = BotFather wala token
   - `TELEGRAM_CHAT_ID` = @userinfobot wala number
3. **Capital variable**: same page → Variables tab → New variable: `CAPITAL` = `50000`
4. **Dashboard on karo**: Settings → Pages → Source: "Deploy from branch" → main / root.
   Website milegi: `https://<username>.github.io/<repo>/`
5. **Test**: Actions tab → "S05 Daily Signals" → Run workflow. Telegram pe message aana chahiye.

## Roz ka kaam
- Subah Telegram pe signal aayega (ya dashboard kholo).
- BUY aaye → broker pe order daalo → `portfolio.json` mein add karo:
  ```json
  "positions": { "TICKER": {"shares": 22, "entry_price": 450, "stop": 408} }
  ```
- SELL aaye → broker pe bech do → `portfolio.json` se hata do.
- portfolio.json update karke repo mein commit/push karo (ya GitHub web se edit).

## Zaroori
- ₹50k = 5 positions (chhoti rakam; seekhne/paper phase ke liye).
- Pehle 2-3 mahine paper/half-risk. Live drawdown 30% > to ruko.
- Schedule 10:00 IST hai (market open ke baad); chaaho to daily.yml mein cron badlo.
