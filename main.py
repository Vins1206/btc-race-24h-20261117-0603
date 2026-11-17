"""Dip-bounce bot for btc-race-24h-20261117-0603.

Trades one asset, CBBTC (Coinbase Wrapped BTC) against USDC, on 1-minute
bars, speaking agent-fun/bars-replay/v1: the harness sends a hello once,
then one batch per bar (the bar that just closed, the fills of the orders
sent on the previous batch, and the account), and the bot answers every
batch with exactly one reply echoing its seq and carrying its orders.

Strategy (long only, one open position, full size):
  * Entry: buy the whole cash balance when the latest close is at least
    1.0% below the close 60 bars ago -- an hourly dip -- provided the
    240-bar return is not worse than -2% (no catching a falling knife in
    a strong downtrend), fewer than 4 entries have filled so far, and we
    are not in the last 120 bars of the window.
  * Exit: sell the whole position when the latest close is 1.5% above
    the entry fill price (take profit) or 1.0% below it (stop loss).
    After a stop loss, wait 30 bars before the next entry. A position
    that has stalled for 360 bars (6 hours) without reaching either
    level is sold too, so the cash is free for the next dip instead of
    sitting in a flat trade until the window ends.
  * Fallback: if no entry has filled by bar 1080 and the 240-bar return
    is not worse than -0.5%, buy once and hold to the end, so a calm
    rising window still produces at least one trade.

Why this shape: on the dev window the 1-minute closes move only about
6 bps per minute (standard deviation), while a full-size round trip
costs about 37 bps in fee, slippage and price impact at the dev
window's pool depths (~800k USDC; the 26 bps in the rules assumes the
deeper frozen depth), so fast signals churn away money -- the EMA-cross
house bot loses over $100 on the dev window doing exactly that. Hourly
dips of ~1.0% that recover ~1.5% within hours are the moves big enough
to pay that cost. The bot therefore trades rarely (3 entries on the dev
window) and lets each trade run to a profit target instead of scalping
noise. Neighbouring parameters (dip 0.9-1.2%, take profit 1.2-1.5%)
are all profitable on the dev window, so the result is a plateau, not
a single tuned spike.

Determinism: integer arithmetic only, no clock, no environment, no
randomness, no network, no files. The same hello and batches always
produce the same orders, so the two graded executions agree byte for
byte. Standard library only.
"""

import json
import sys

SYMBOL = "CBBTC"
# Dip vs the close 60 bars ago: buy when close * 1000 <= close60 * 990.
DIP_NUM = 990
DIP_DEN = 1000
DIP_LOOKBACK = 60
# Take profit 1.5% above the entry fill price; stop loss 1.0% below it.
TP_NUM = 1015
TP_DEN = 1000
SL_NUM = 990
SL_DEN = 1000
# Trend guard: skip entries when the 240-bar return is below -2%.
TREND_LOOKBACK = 240
TREND_MIN_NUM = 98
TREND_MIN_DEN = 100
# Risk limits.
MAX_ENTRIES = 4
STOP_COOLDOWN_BARS = 30
# Time stop: exit a stalled position after this many bars.
TIME_STOP_BARS = 360
NO_ENTRY_AFTER_BARS = 1320
FALLBACK_BAR = 1080
FALLBACK_TREND_NUM = 995
FALLBACK_TREND_DEN = 1000
# Slippage allowance per order, in bps. A fill moves at most ~15 bps
# (2 bps modelled slippage plus ~13 bps of price impact at full size)
# away from the open, and the open itself sits within a few tens of bps
# of the latest close, so 200 bps is generous without being loose.
MAX_SLIPPAGE_BPS = 200


def main():
    hello = None
    warmup_bars = 0
    closes = []          # latest close per bar, as ints (price_scale units)
    cash = 0             # quote units, from the latest batch account
    qty = 0              # held quantity, from the latest batch account
    entry_price = None   # fill price of the open position, as an int
    entry_bar = -1       # bar count when the open position was filled
    entries_filled = 0
    stop_cooldown_until = -1
    next_client = 1

    out = sys.stdout
    for raw in sys.stdin.buffer:
        line = raw.strip()
        if not line:
            continue
        msg = json.loads(line.decode("utf-8"))
        kind = msg.get("type")
        if kind == "hello":
            hello = msg
            warmup_bars = int(msg.get("warmup_bars") or 0)
            cash = int(msg["capital"]["quote_units"])
            continue
        if kind == "done":
            break
        if kind != "batch":
            raise ValueError("unknown message type: %r" % (kind,))

        seq = int(msg["seq"])
        bars = msg.get("bars") or []
        bar = None
        for candidate in bars:
            if candidate.get("symbol") == SYMBOL:
                bar = candidate
                break
        if bar is not None:
            closes.append(int(bar["c"]))

        # Account first: it already reflects the fills of the previous
        # batch, so it is the ground truth for cash and position.
        account = msg.get("account") or {}
        cash = int(account.get("cash_quote_units") or 0)
        qty = 0
        cost = 0
        for position in account.get("positions") or []:
            if position.get("symbol") == SYMBOL:
                qty = int(position["qty"])
                cost = int(position["cost_quote_units"])
        if qty <= 0:
            entry_price = None
            entry_bar = -1
        elif entry_price is None and cost > 0 and hello is not None:
            # Implied entry price from the position cost, when no fill
            # price was seen: price = cost * 10^(qty+price scale) /
            # (qty * 10^quote scale). Cost includes fees, so this is a
            # slightly conservative entry reference.
            symbol_info = None
            for item in hello.get("symbols") or []:
                if item.get("symbol") == SYMBOL:
                    symbol_info = item
                    break
            if symbol_info is not None:
                quote_scale = int(hello["capital"]["quote_scale"])
                entry_price = (
                    cost
                    * 10 ** (int(symbol_info["qty_scale"]) + int(symbol_info["price_scale"]))
                    // (qty * 10 ** quote_scale)
                )
                if entry_bar < 0:
                    entry_bar = len(closes)

        # Fills of the previous batch: record entries and exits exactly.
        for fill in msg.get("fills") or []:
            if fill.get("symbol") != SYMBOL or fill.get("status") != "filled":
                continue
            if fill.get("side") == "buy":
                entries_filled += 1
                entry_price = int(fill["price"])
                entry_bar = len(closes)
            elif fill.get("side") == "sell":
                entry_price = None
                entry_bar = -1

        orders = []
        n_bars = len(closes)
        warming_up = seq < warmup_bars
        if bar is not None and not warming_up and n_bars > 0:
            close = closes[-1]
            if qty > 0 and entry_price is not None:
                # Long: take profit, stop loss with a cooldown, or the
                # time stop on a stalled position (no cooldown: the
                # exit was not a loss-driven one).
                if close * TP_DEN >= entry_price * TP_NUM:
                    orders.append(_sell(qty, next_client))
                    next_client += 1
                elif close * SL_DEN <= entry_price * SL_NUM:
                    orders.append(_sell(qty, next_client))
                    next_client += 1
                    stop_cooldown_until = n_bars + STOP_COOLDOWN_BARS
                elif entry_bar >= 0 and n_bars - entry_bar >= TIME_STOP_BARS:
                    orders.append(_sell(qty, next_client))
                    next_client += 1
            elif qty <= 0 and cash > 0:
                dip = (
                    n_bars > DIP_LOOKBACK
                    and close * DIP_DEN <= closes[-1 - DIP_LOOKBACK] * DIP_NUM
                )
                trend_ok = True
                if n_bars > TREND_LOOKBACK:
                    trend_ok = (
                        close * TREND_MIN_DEN
                        >= closes[-1 - TREND_LOOKBACK] * TREND_MIN_NUM
                    )
                cooldown_ok = n_bars >= stop_cooldown_until
                if (
                    dip
                    and trend_ok
                    and cooldown_ok
                    and entries_filled < MAX_ENTRIES
                    and n_bars <= NO_ENTRY_AFTER_BARS
                ):
                    orders.append(_buy(cash, next_client))
                    next_client += 1
                elif (
                    entries_filled == 0
                    and n_bars >= FALLBACK_BAR
                    and n_bars <= NO_ENTRY_AFTER_BARS
                ):
                    # No dip ever came: buy once into a non-falling
                    # market so the window still produces a trade.
                    fallback_ok = True
                    if n_bars > TREND_LOOKBACK:
                        fallback_ok = (
                            close * FALLBACK_TREND_DEN
                            >= closes[-1 - TREND_LOOKBACK] * FALLBACK_TREND_NUM
                        )
                    if fallback_ok:
                        orders.append(_buy(cash, next_client))
                        next_client += 1

        out.write(
            json.dumps(
                {"type": "reply", "seq": seq, "orders": orders},
                separators=(",", ":"),
            )
            + "\n"
        )
        out.flush()


def _buy(notional_quote_units, client_no):
    return {
        "side": "buy",
        "client_id": "o%d" % client_no,
        "symbol": SYMBOL,
        "notional_quote_units": str(int(notional_quote_units)),
        "max_slippage_bps": MAX_SLIPPAGE_BPS,
    }


def _sell(quantity, client_no):
    return {
        "side": "sell",
        "client_id": "o%d" % client_no,
        "symbol": SYMBOL,
        "qty": str(int(quantity)),
        "max_slippage_bps": MAX_SLIPPAGE_BPS,
    }


if __name__ == "__main__":
    main()
