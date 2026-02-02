import ccxt
import time
from telegram import send_telegram
from utils.trade_logger import log_position

MAX_RISK_PCT = 0.08

class BitgetExecutor:
    def __init__(self, api_key, api_secret, passphrase, taker_fee=0.0006):
        self.exchange = ccxt.bitget({
            "apiKey": api_key,
            "secret": api_secret,
            "password": passphrase,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
                "createMarketBuyOrderRequiresPrice": False
            }
        })

        self.taker_fee = taker_fee
        self.positions = {}

        # One-way mode
        self.exchange.set_position_mode(False)

    def get_balance(self):
        balance = self.exchange.fetch_balance()
        return balance["USDT"]["free"]
    
    def get_total_balance(self):
        balance = self.exchange.fetch_balance()
        return balance["USDT"]["total"]

    def has_position(self, symbol):
        symbol = symbol if ":USDT" in symbol else f"{symbol}:USDT"
        positions = self.exchange.fetch_positions([symbol])
        
        for p in positions:
            contracts = float(p.get("contracts",0))
            if contracts > 0:
                return True
            
        return False

    def open_position(self, symbol, side, size, tp, sl):
        order_side = "buy" if side == "LONG" else "sell"

        params = {
            'stopLoss':{
                'triggerPrice':sl,
                'price':sl
            }
        }

        balance = self.get_total_balance()
        ticker = self.exchange.fetch_ticker(symbol)
        entry_price = ticker["last"]
        risk_usdt = abs(entry_price - sl) * size
        risk_pct = (risk_usdt / balance) * 100

        if risk_pct > MAX_RISK_PCT:
            send_telegram(
                f" TRADE BLOQUEADO {symbol} - {side}| Riesgo {risk_pct:.2f} USDT "
                f"> Max permitido {MAX_RISK_PCT:.2f}"
            )
        else:
            # 1️⃣ ORDEN MARKET
            order = self.exchange.create_order(
                symbol=symbol,
                type="market",
                side=order_side,
                amount=size,
                params=params
            )        

            self.positions[symbol] = {
                "symbol": symbol,
                "side": side,
                "entry": entry_price,
                "size": size,
                "original_size": size,  # 👈 clave
                "sl": sl,
                "initial_sl":sl,
                "trail_on":False,
                "partial_closed": False,
                "be_set":False
            }


            print(f"🟢 OPEN {side} {symbol} | entry={entry_price:.2f}")

            print(f"🎯 TP & SL colocados | TP={tp:.2f} | SL={sl:.2f} | Riesgo USDT: {risk_usdt:.4f} | Riesgo %: {risk_pct:.2f}")
            send_telegram(
                f"🟢 <b>OPEN {side}</b>\n"
                f"📌 {symbol}\n"
                f"💰 Entry: {entry_price:.2f}"
                f"🎯 TP: {tp:.2f}"
                f"🎯 SL: {sl:.2f}"
                f"Riesgo USDT: {risk_usdt:.4f} | Riesgo %: {risk_pct:.2f}"
            )

    def check_closed_positions(self):
        closed = []

        for symbol, pos in list(self.positions.items()):
            positions = self.exchange.fetch_positions([symbol])
            still_open = any(
                float(p["contracts"]) > 0
                for p in positions
                if p["symbol"] == symbol
            )

            if not still_open:
                ticker = self.exchange.fetch_ticker(symbol)
                exit_price = ticker["last"]

                pnl = (
                    (exit_price - pos["entry"]) * pos["size"]
                    if pos["side"] == "LONG"
                    else (pos["entry"] - exit_price) * pos["size"]
                )

                pos["exit"] = exit_price
                pos["pnl"] = pnl
                pos["closed_at"] = time.time()

                closed.append(pos)
                del self.positions[symbol]

        return closed

    def fetch_open_orders(self):
        for id in self.positions:
            order = self.exchange.fetchOrder(id)
            print(f"Orden {order}")

    def place_tp_sl(self, symbol, side, size, tp, sl):
        return

    def _format_symbol(self, symbol):
        market = self.exchange.market(symbol)
        return market["id"]  # 🔥 ESTE es el symbol real


    def close_partial(self, symbol, side, size):
        market_id = self.exchange.market(symbol)["id"]
        close_side = "sell" if side == "LONG" else "buy"

        self.exchange.create_order(
            symbol,
            "market",
            close_side,
            size,
            None,
            {
                "reduceOnly": True
            }
        )

        print(f"🟡 Parcial cerrada | {symbol} | size={size}")

    def update_sl(self, symbol, side, size, new_sl):
        close_side = "sell" if side == "LONG" else "buy"

        params = {
            "stopLossPrice": round(new_sl, 6),
            "reduceOnly": True
        }

        self.exchange.create_order(
            symbol=symbol,
            type="market",
            side=close_side,
            amount=size,
            price=None,
            params=params
        )


    def load_open_positions(self):
        positions = self.exchange.fetch_positions()
        open_positions = []

        for p in positions:
            if p["contracts"] and float(p["contracts"]) > 0:
                open_positions.append({
                    "symbol": p["symbol"],
                    "side": "LONG" if p["side"] == "long" else "SHORT",
                    "size": float(p["contracts"]),
                    "entry": float(p["entryPrice"]),
                    "mark_price": float(p["markPrice"])
                })

        return open_positions

    def get_mark_price(self, symbol):
        ticker = self.exchange.fetch_ticker(symbol)

        # 1️⃣ Preferido (Bitget real)
        if "markPrice" in ticker.get("info", {}):
            return float(ticker["info"]["markPrice"])

        # 2️⃣ Fallback (si ccxt lo mapea)
        if "mark" in ticker:
            return float(ticker["mark"])

        # 3️⃣ Último recurso (NO ideal, pero evita crash)
        return float(ticker["last"])
