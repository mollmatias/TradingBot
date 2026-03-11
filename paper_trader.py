import time
import pandas as pd
from execution.bitget_executor import BitgetExecutor
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
from telegram_comandos import BOT_ACTIVE
from telegram_comandos import process_commands

import os
import ccxt
import threading


FILE = "trades_live.csv"
FILE_POS = "open_positions.csv"
SIDE = "long"
SL = 0
DRY_RUN = False



init_trade_log(FILE,"time,symbol,side,entry,exit,score,strength,net_pnl,balance\n")
init_trade_log(FILE_POS,"id,symbol\n")
load_dotenv()

exchange = ccxt.bitget({
    "apiKey": os.getenv("BITGET_API_KEY"),
    "secret": os.getenv("BITGET_API_SECRET"),
    "password": os.getenv("BITGET_API_PASSPHRASE"),
    "enableRateLimit": True,
    "options": {
        "defaultType": "swap",
        "createMarketBuyOrderRequiresPrice": False
    }
})

executor = BitgetExecutor(
    os.getenv("BITGET_API_KEY"),
    os.getenv("BITGET_API_SECRET"), 
    os.getenv("BITGET_API_PASSPHRASE")
)

def allowed_trading_hour():
    hour = datetime.utcnow().hour
    return 12 <= hour <= 22


executor.positions = {}

open_positions = executor.load_open_positions()

for pos in open_positions:

    symbol = pos["symbol"]

    ohlcv = fetch_ohlcv(symbol, TIMEFRAME)

    df = pd.DataFrame(
        ohlcv,
        columns=["time","open","high","low","close","volume"]
    )

    df = apply_indicators(df)

    atr = df.iloc[-1]["atr"]

    entry = pos["entry"]

    side = pos["side"]

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
        "be_set": False
    }

    print(f"🔄 Recovered position: {symbol} | {side} | entry={entry}")

def telegram_loop(executor):

    while True:

        try:

            process_commands(SYMBOLS, executor, FILE)

        except Exception as e:

            print(f"⚠️ Telegram error: {e}")

        time.sleep(1)
   

def trading_loop(executor, exchange):

    while True:

        for symbol in SYMBOLS:

            try:

                ohlcv = fetch_ohlcv(symbol, TIMEFRAME)

                df = pd.DataFrame(
                    ohlcv,
                    columns=["time","open","high","low","close","volume"]
                )

                df = apply_indicators(df)

                last = df.iloc[-1]

                if last["signal"] is None:
                    continue

                if executor.has_position(symbol):
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
                strength = last["signal_strength"]
                risk_amount = dynamic_risk(score, balance,strength)

                sl_distance = abs(price - sl)

                size = risk_amount / sl_distance

                if size * price < 5:
                    continue

                executor.open_position(
                    symbol,
                    last["signal"],
                    size,
                    tp,
                    sl,
                    atr,
                    score,
                    strength
                )

            except Exception as e:

                print(f"⚠️ ERROR trading {symbol}: {e}")

        time.sleep(60)

def position_manager(executor, exchange):

    BUFFER = 0.001
    TRAIL_MULT = 1.1

    while True:

        try:

            closed_positions = executor.check_closed_positions()

            for pos in closed_positions:

                net_pnl = pos["pnl"]

                balance = executor.get_total_balance()

                log_trade(
                    FILE,
                    pos["symbol"],
                    pos["side"],
                    pos["entry"],
                    pos["exit"],
                    pos["score"],
                    pos["strength"],
                    net_pnl,
                    balance
                )

                send_telegram(
                    f"📉 Trade cerrado\n"
                    f"{pos['symbol']} {pos['side']}\n"
                    f"PnL: {net_pnl:.2f}"
                )

            for symbol, pos in list(executor.positions.items()):

                ticker = exchange.fetch_ticker(symbol)
                time.sleep(0.2)
                price = ticker["last"]

                mark = executor.get_mark_price(symbol)

                entry = pos["entry"]

                side = pos["side"]

                initial_sl = pos["initial_sl"]

                atr = pos["atr"]

                sl_dist = abs(entry - initial_sl)

                if sl_dist == 0:
                    continue

                rr = abs(price - entry) / sl_dist

                if rr >= 1 and not pos.get("be_set", False):

                    new_sl = entry

                    executor.update_sl(symbol, side, pos["size"], new_sl)

                    pos["sl"] = new_sl
                    pos["be_set"] = True

                if rr >= 2 and not pos.get("trail_on", False):

                    pos["trail_on"] = True

                if pos.get("trail_on", False):

                    if side == "LONG":

                        new_sl = price - atr * TRAIL_MULT

                        if new_sl > pos["sl"]:

                            executor.update_sl(symbol, "LONG", pos["size"], new_sl)

                            pos["sl"] = new_sl

                    else:

                        new_sl = price + atr * TRAIL_MULT

                        if new_sl < pos["sl"]:

                            executor.update_sl(symbol, "SHORT", pos["size"], new_sl)

                            pos["sl"] = new_sl

        except Exception as e:

            print(f"⚠️ Position manager error: {e}")

        time.sleep(5)

telegram_thread = threading.Thread(
    target=telegram_loop,
    args=(executor,),
    daemon=True
)

trading_thread = threading.Thread(
    target=trading_loop,
    args=(executor, exchange),
    daemon=True
)

position_thread = threading.Thread(
    target=position_manager,
    args=(executor, exchange),
    daemon=True
)

telegram_thread.start()
trading_thread.start()
position_thread.start()

while True:
    time.sleep(60)
