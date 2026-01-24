from telegram import send_telegram

class PaperExecutor:
    def __init__(self, balance, taker_fee):
        self.balance = balance
        self.positions = []
        self.taker_fee = taker_fee

    # ─────────────────────────────
    # UTILIDAD
    # ─────────────────────────────
    def has_position(self, symbol):
        return any(p["symbol"] == symbol for p in self.positions)

    # ─────────────────────────────
    # OPEN
    # ─────────────────────────────
    def open_position(self, symbol, side, entry_price, size, tp, sl, time=None):
        fee = entry_price * size * self.taker_fee
        self.balance -= fee

        position = {
            "symbol": symbol,
            "side": side,
            "entry": entry_price,
            "size": size,
            "tp": tp,
            "sl": sl,
            "time": time
        }

        self.positions.append(position)

        print(
            f"🟢 OPEN {side} {symbol} | "
            f"entry={entry_price:.2f} | "
            f"tp={tp:.2f} | sl={sl:.2f} | "
            f"size={size:.4f}"
        )

    # ─────────────────────────────
    # CHECK TP / SL (USANDO HIGH / LOW)
    # ─────────────────────────────
    def check_positions(self, symbol, price):
        closed = []

        for pos in self.positions:
            if pos["symbol"] != symbol:
                continue

            hit_tp = price >= pos["tp"] if pos["side"] == "LONG" else price <= pos["tp"]
            hit_sl = price <= pos["sl"] if pos["side"] == "LONG" else price >= pos["sl"]

            if hit_tp or hit_sl:
                exit_reason = "TP 🎯" if hit_tp else "SL 🛑"

                pnl = (
                    (price - pos["entry"]) * pos["size"]
                    if pos["side"] == "LONG"
                    else (pos["entry"] - price) * pos["size"]
                )

                fee = price * pos["size"] * self.taker_fee
                net_pnl = pnl - fee
                self.balance += net_pnl

                print(
                    f"🔴 CLOSE {pos['side']} {symbol} | {exit_reason} | "
                    f"PNL={net_pnl:.2f} | BAL={self.balance:.2f}"
                )

                msg = (
                    f"{'🎯' if hit_tp else '🛑'} <b>Trade cerrado</b>\n\n"
                    f"📌 Symbol: {symbol}\n"
                    f"⏳ Side: {pos['side']}\n"
                    f"💰 Entry: {pos['entry']:.2f}\n"
                    f"📉 Exit: {price:.2f}\n"
                    f"📊 PNL: {net_pnl:.2f}\n"
                    f"💼 Balance: {self.balance:.2f}"
                )

                send_telegram(msg)

                closed.append(pos)

        for c in closed:
            self.positions.remove(c)

        return closed
