import time
import pandas as pd
from pair_selector import select_top_pairs
from dynamic_risk import dynamic_risk
from config import SYMBOLS,TIMEFRAME,INITIAL_BALANCE,TAKER_FEE,SL_ATR_MULT,TP_ATR_MULT,RISK_PER_TRADE,LEVERAGE
from data_feed import fetch_ohlcv
from indicators import apply_indicators
from risk import calculate_contract_size
from telegram import send_telegram
from utils.trade_logger import init_trade_log, log_trade
from dotenv import load_dotenv
from datetime import datetime
import os
import ccxt


FILE = "trades_live.csv"
FILE_POS = "open_positions.csv"
SIDE = "long"
SL = 0
DRY_RUN = False



init_trade_log(FILE,"time,symbol,side,entry,exit,net_pnl,balance\n")
init_trade_log(FILE_POS,"id,symbol\n")
load_dotenv()

def allowed_trading_hour():
    hour = datetime.utcnow().hour
    return 12 <= hour <= 22

if DRY_RUN:
    from execution.paper_executor import PaperExecutor
    executor = PaperExecutor(balance=INITIAL_BALANCE, taker_fee=TAKER_FEE)
else:
    from execution.bitget_executor import BitgetExecutor
    from config import TAKER_FEE

    API_KEY = os.getenv("BITGET_API_KEY")
    API_SECRET = os.getenv("BITGET_API_SECRET")
    API_PASSPHRASE = os.getenv("BITGET_API_PASSPHRASE")


    exchange = ccxt.bitget({
        "apiKey":API_KEY,
        "secret":API_SECRET,
        "password":API_PASSPHRASE,
        "enableRateLimit":True,
        "options":{
            "defaultType":"swap",
            "createMarketBuyOrderRequiresPrice": False  # 👈🔥 CLAVE
        }
    })
    executor = BitgetExecutor(
        API_KEY,
        API_SECRET,
        API_PASSPHRASE
    )

print("🤖 BOT INICIADO — LIVE" if not DRY_RUN else "🤖 BOT EN PAPER")

executor.positions = {}
open_positions = executor.load_open_positions()

for pos in open_positions:
    symbol = pos["symbol"]

    # Traer ATR actual
    ohlcv = fetch_ohlcv(symbol,TIMEFRAME)
    df = pd.DataFrame(
        ohlcv,
        columns = ["time","open","high","low","close","volume"]
    )
    df = apply_indicators(df)
    atr = df.iloc[-1]["atr"]

    entry = pos["entry"]
    side = pos["side"]

    
    # Reconstruir SL inicial (conservador)
    if side == "LONG":
        initial_sl = entry - atr * SL_ATR_MULT
    else:
        initial_sl = entry + atr * SL_ATR_MULT

    executor.positions[symbol] = {
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "size": pos["size"],
        "original_size": pos["size"],
        "sl": initial_sl,
        "initial_sl": initial_sl,
        "atr": atr,
        "trail_on": False,
        "be_set":False
    }

    print(f"🔄 Recovered position: {symbol} | {side} | entry={entry}")


while True:
    closed_positions = executor.check_closed_positions()
    
    for pos in closed_positions:
        net_pnl = pos["pnl"]
        send_telegram(
            f"📉 <b>Trade cerrado</b>\n"
            f"{pos['symbol']}\n"
            f"{pos['side']}\n"
            f"PnL: {net_pnl:.2f}"
        )

    # ───── GESTIÓN ACTIVA PROFESIONAL ─────

    BUFFER = 0.001  # 0.1% seguridad contra mark
    TRAIL_MULT = 1.1

    for symbol, pos in executor.positions.items():

        try:
            ticker = exchange.fetch_ticker(symbol)
            price = ticker["last"]
            mark = executor.get_mark_price(symbol)

            entry = pos["entry"]
            side = pos["side"]
            initial_sl = pos["initial_sl"]
            atr = pos["atr"]

            if atr is None or atr <= 0:
                continue

            # ───── CÁLCULO R BASADO EN SL ORIGINAL ─────
            sl_dist = abs(entry - initial_sl)
            if sl_dist == 0:
                continue

            rr = abs(price - entry) / sl_dist

            # ───── 1️⃣ MOVER A BREAK EVEN EN 1R ─────
            if rr >= 1 and not pos.get("be_set", False):

                new_sl = entry

                # Validar contra mark
                if side == "LONG" and new_sl >= mark:
                    new_sl = mark * (1 - BUFFER)

                if side == "SHORT" and new_sl <= mark:
                    new_sl = mark * (1 + BUFFER)

                executor.update_sl(symbol, side, pos["size"], new_sl)

                pos["sl"] = new_sl
                pos["be_set"] = True

                send_telegram(f"🔁 SL movido a BE | {symbol}")

            # ───── 2️⃣ ACTIVAR TRAILING EN 2R ─────
            if rr >= 2 and not pos.get("trail_on", False):
                pos["trail_on"] = True
                send_telegram(f"🚀 Trailing activado | {symbol}")

            # ───── 3️⃣ TRAILING DINÁMICO ─────
            if pos.get("trail_on", False):

                if side == "LONG":
                    new_sl = price - atr * TRAIL_MULT

                    # Validar contra mark
                    if new_sl >= mark:
                        new_sl = mark * (1 - BUFFER)

                    # Nunca bajar SL
                    if new_sl > pos["sl"]:
                        executor.update_sl(symbol, "LONG", pos["size"], new_sl)
                        pos["sl"] = new_sl
                        send_telegram(f"🔒 SL actualizado LONG | {symbol}")

                else:  # SHORT
                    new_sl = price + atr * TRAIL_MULT

                    if new_sl <= mark:
                        new_sl = mark * (1 + BUFFER)

                    # Nunca subir SL
                    if new_sl < pos["sl"]:
                        executor.update_sl(symbol, "SHORT", pos["size"], new_sl)
                        pos["sl"] = new_sl
                        send_telegram(f"🔒 SL actualizado SHORT | {symbol}")

        except Exception as e:
            print(f"⚠️ Error gestión {symbol}: {e}")

    symbols = select_top_pairs(TIMEFRAME)

    for symbol in symbols:
        try:

            ohlcv = fetch_ohlcv(symbol, TIMEFRAME)
            df = pd.DataFrame(
                ohlcv,
                columns=["time", "open", "high", "low", "close", "volume"]
            )

            df = apply_indicators(df)
            last = df.iloc[-1]
            atr_pct = last["atr"] / last["close"]

            if atr_pct < 0.002:
                continue

            if last["adx"] < 18:
                continue

            if last["signal"] is None:
                continue

            if executor.has_position(symbol):
                print(f"{symbol} ya tiene posicion abierta - skip")
                continue
            
            atr = last["atr"]
            price = last["close"]
            
            if atr is None or atr == 0:
                continue
            

            if last["signal"] == "LONG":
                sl = price - atr * SL_ATR_MULT
                tp = price + atr * TP_ATR_MULT
            else:
                sl = price + atr * SL_ATR_MULT
                tp = price - atr * TP_ATR_MULT

            balance = executor.get_total_balance()

            score = max(last["score_long"], last["score_short"])

            risk_amount = dynamic_risk(score, balance)

            sl_distance = abs(price - sl)

            size = risk_amount / sl_distance

            SIDE = last["signal"]
            SL = sl
            executor.open_position(
                symbol,
                last["signal"],
                size,
                tp,
                sl,
                atr
            )

        except Exception as e:
            print(f"⚠️ ERROR {symbol} | {SIDE} : {e}")
            send_telegram(f"⚠️ ERROR {symbol} | {SIDE} | {SL}: {e}")

    time.sleep(60)

    