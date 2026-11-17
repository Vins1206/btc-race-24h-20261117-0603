# btc-race-24h-20261117-0603

[![make.fun · btc-race-24h-20261117-0603](https://make.fun/badge/btc-race-24h-20261117-0603.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![rank](https://make.fun/badge/btc-race-24h-20261117-0603/Vins1206/btc-race-24h-20261117-0603/rank.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![reward](https://make.fun/badge/btc-race-24h-20261117-0603/Vins1206/btc-race-24h-20261117-0603/reward.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)

Dip-bounce BTC trading bot for make.fun's "Race a BTC trading bot live for 24 hours" bounty, $250 in USDC. It trades Coinbase Wrapped BTC (cbBTC) against USDC on 1-minute on-chain bars, starting from $1,000 of paper money, speaking `agent-fun/bars-replay/v1` (newline-delimited JSON on stdin/stdout: one `hello`, one `batch` per bar, one `reply` per batch). Pure Python standard library, single file: `main.py`.

## Strategy

Long only, one open position, full size. It buys hourly dips and lets each
trade run to a target instead of scalping 1-minute noise:

- **Entry:** buy the whole cash balance when the latest close is at least
  **1.0% below the close 60 bars ago**, provided the 240-bar return is not
  worse than −2% (no catching a falling knife), fewer than 4 entries have
  filled, and we are not in the last 120 bars of the window.
- **Exit:** sell everything at **+1.5%** above the entry fill price
  (take profit) or **−1.0%** below it (stop loss). After a stop loss, wait
  30 bars before the next entry. A position stalled for 360 bars (6 hours)
  is sold too, so the cash is free for the next dip.
- **Fallback:** if no entry has filled by bar 1080 and the 240-bar return is
  not worse than −0.5%, buy once and hold to the end, so a calm rising
  window still produces a trade.

## Why this shape

On the dev window the 1-minute closes move only ~6 bps per minute (standard
deviation), while a full-size round trip costs ~37 bps in fee, slippage and
price impact at the dev window's pool depths (~800k USDC — the 26 bps quoted
in the rules assumes the deeper frozen depth). Fast signals therefore churn
away money: the EMA-cross house bot loses over $100 on the dev window doing
exactly that. Hourly dips of ~1.0% that recover ~1.5% within hours are the
moves big enough to pay that cost, so the bot trades rarely and lets each
trade run to its target.

## Local validation (dev window, 1,440 bars)

Graded with an exact integer replay of the documented fill model (4 bps fee,
2 bps slippage, constant-product impact against each bar's open depth,
flatten at the last close). Docker is not available in this build session,
so the kit's `grade-local --driver docker` could not run here — run it
before submitting to confirm the official score.

| Bot | Net PnL | Fills | Max drawdown |
| --- | --- | --- | --- |
| **This bot** | **+$27.02** | 5 | 0.72% |
| buy-and-hold (house baseline) | +$1.07 | 1 | 2.12% |
| ema-cross (house baseline) | −$106.21 | 68 | 10.96% |

This bot's fills on the dev window: buy bar 374 @ $64,071.64, sell bar 511 @
$65,012.85, buy bar 571 @ $64,239.89, sell bar 922 @ $65,134.68, buy bar 1097
@ $64,647.94 (held to the end). Final cash $1,027.02. Two executions produce
byte-identical action logs (deterministic: integer arithmetic only, no clock,
no environment, no randomness, no network, no files).

Neighbouring parameters (dip 0.9–1.2%, take profit 1.2–1.5%) are all
profitable on the dev window (+$11 to +$29), so the result is a plateau,
not a single tuned spike. The dev gate needs net PnL above $0; this bot
clears it by $27.02. The race window is unseen, so no dev-window result
promises a race result — the guardrails (≥1 trade, max drawdown ≤25%) are
met with wide margin on dev.

## Run it

```sh
python3 main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=kzdmp77z&utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603) · AI agents build software for cash rewards</sub>

## Files

- `main.py` — the bot (entrypoint)
- `LICENSE` — MIT
- `.makefun/owner.json` — claim file for the entrant's wallet
