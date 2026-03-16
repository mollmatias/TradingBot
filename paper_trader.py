import time
import math
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
from data_feed import fetch_btc

import os
import ccxt
import threading


FILE     = "trades_live.csv"
FILE_POS = "open_positions.csv"
SIDE     = "long"
SL       = 0
DRY_RUN  = False

# ──────────────────────────────────────────────
# FIX 429 — caché de precios
# En lugar de llamar fetch_ticker() por cada posición en cada ciclo de 5s,
# guardamos el último precio y lo reutilizamos si tiene menos de TTL segundos.
# Con 8 posiciones abiertas, esto reduce de ~96 llamadas/min a ~8 llamadas/min.
# ──────────────────────────────────────────────
price_cache     = {}
PRICE_CACHE_TTL = 10  # segundos

# FIX amount precision — caché de market info
# Los mínimos y precision de cada par no cambian. Los cargamos una vez al inicio
# y los reutilizamos para siempre, sin llamar load_markets() en cada orden.
market_cache = {}


init_trade_log(FILE, "time,symbol,side,entry,exit,score,strength,net_pnl,balance\n")
init_trade_log(FILE_POS, "id,symbol\n")
load_dotenv()

exchange = ccxt.bitget({
    "apiKey":    os.getenv("BITGET_API_KEY"),
    "secret":    os.getenv("BITGET_API_SECRET"),
    "password":  os.getenv("BITGET_API_PASSPHRASE"),
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


# ──────────────────────────────────────────────
# Pre-carga de markets al inicio
# Llena market_cache de una sola vez para no repetir la llamada después.
# ──────────────────────────────────────────────
try:
    print("📡 Cargando market info de Bitget...")
    raw_markets = exchange.load_markets()
    for sym, data in raw_markets.items():
        market_cache[sym] = data
    print(f"✅ {len(market_cache)} markets cargados")
except Exception as e:
    print(f"⚠️ Error cargando markets: {repr(e)}")


def allowed_trading_hour():
    hour = datetime.utcnow().hour
    return 12 <= hour <= 22


def get_cached_price(symbol):
    """
    FIX 429: Devuelve precio desde caché si es reciente (< PRICE_CACHE_TTL seg).
    Solo llama fetch_ticker() cuando el caché está vencido.
    """
    now    = time.time()
    cached = price_cache.get(symbol)

    if cached and (now - cached["ts"]) < PRICE_CACHE_TTL:
        return cached["price"]

    ticker = exchange.fetch_ticker(symbol)
    price_cache[symbol] = {"price": ticker["last"], "ts": now}
    return ticker["last"]


def round_to_precision(size, symbol):
    """
    FIX amount precision: Redondea el tamaño al step mínimo del par según
    la info de Bitget. Devuelve 0 si el tamaño queda por debajo del mínimo
    permitido (señal para descartar la orden).

    Ejemplo con ETH (min=0.01, step=0.01):
      0.0073 -> 0.00 -> descartada
      0.034  -> 0.03
      0.157  -> 0.15
    """
    market     = market_cache.get(symbol, {})
    precision  = market.get("precision", {}).get("amount", None)
    min_amount = market.get("limits",    {}).get("amount", {}).get("min", None)

    if precision is not None:
        if isinstance(precision, int):
            # precision es número de decimales: 2 -> step 0.01
            factor = 10 ** precision
            size   = math.floor(size * factor) / factor
        else:
            # precision es el step directo: 0.01, 0.001, etc.
            size = math.floor(size / precision) * precision
            size = round(size, 10)  # limpiar floating point noise

    if min_amount is not None and size < min_amount:
        return 0

    return size


# ──────────────────────────────────────────────
# Recuperar posiciones abiertas al arrancar
# ──────────────────────────────────────────────
executor.positions = {}

open_positions = executor.load_open_positions()

for pos in open_positions:

    symbol = pos["symbol"]
    ohlcv  = fetch_ohlcv(symbol, TIMEFRAME)

    df = pd.DataFrame(
        ohlcv,
        columns=["time","open","high","low","close","volume"]
    )

    df    = apply_indicators(df)
    atr   = df.iloc[-1]["atr"]
    entry = pos["entry"]
    side  = pos["side"]

    if side == "LONG":
        initial_sl = entry - atr * SL_ATR_MULT
    else:
        initial_sl = entry + atr * SL_ATR_MULT

    executor.positions[symbol] = {
        "symbol":        symbol,
        "side":          side,
        "entry":         entry,
        "size":          pos["size"],
        "original_size": pos["size"],
        "sl":            initial_sl,
        "initial_sl":    initial_sl,
        "atr":           atr,
        "trail_on":      False,
        "be_set":        False
    }

    print(f"🔄 Recovered position: {symbol} | {side} | entry={entry}")


# ──────────────────────────────────────────────
# Threads
# ──────────────────────────────────────────────

def telegram_loop(executor):

    while True:
        try:
            process_commands(SYMBOLS, executor, FILE)
        except Exception as e:
            print(f"⚠️ Telegram error: {repr(e)}")
        time.sleep(1)


def trading_loop(executor, exchange):

    while True:

        # FIX: allowed_trading_hour() estaba definida pero nunca se llamaba
        if not allowed_trading_hour():
            print("⏸️ Fuera de horario (12-22 UTC). Esperando...")
            time.sleep(60)
            continue

        try:
            btc_ohlcv = fetch_btc(TIMEFRAME)
            btc_df = pd.DataFrame(
                btc_ohlcv,
                columns=["time","open","high","low","close","volume"]
            )
            btc_df["btc_ema200"] = btc_df["close"].ewm(span=200).mean()
            btc_close  = btc_df.iloc[-1]["close"]
            btc_ema200 = btc_df.iloc[-1]["btc_ema200"]

        except Exception as e:
            print(f"⚠️ Error obteniendo datos de BTC: {repr(e)}")
            time.sleep(60)
            continue

        for symbol in SYMBOLS:

            try:

                ohlcv = fetch_ohlcv(symbol, TIMEFRAME)

                df = pd.DataFrame(
                    ohlcv,
                    columns=["time","open","high","low","close","volume"]
                )

                df = apply_indicators(df)
                df["btc_close"]  = btc_close
                df["btc_ema200"] = btc_ema200

                last = df.iloc[-2]

                if last["signal"] is None:
                    continue

                if executor.has_position(symbol):
                    continue

                if btc_close <= btc_ema200:
                    continue

                atr   = last["atr"]
                price = last["close"]

                if atr is None or atr == 0:
                    continue

                if last["signal"] == "LONG":
                    sl = price - atr * SL_ATR_MULT
                    tp = price + atr * TP_ATR_MULT
                else:
                    sl = price + atr * SL_ATR_MULT
                    tp = price - atr * TP_ATR_MULT

                balance  = executor.get_total_balance()
                score    = max(last["score_long"], last["score_short"])
                strength = last["signal_strength"]

                # FIX exposición: pasamos el número de posiciones abiertas
                open_positions_count = len(executor.positions)
                risk_amount = dynamic_risk(last["adx"], balance, open_positions_count)

                sl_distance = abs(price - sl)
                raw_size    = risk_amount / sl_distance

                # FIX amount precision: redondear antes de enviar la orden
                size = round_to_precision(raw_size, symbol)

                if size == 0:
                    print(f"⚠️ {symbol}: size ({raw_size:.6f}) menor al mínimo del par. Orden descartada.")
                    continue

                if size * price < 5:
                    print(f"⚠️ {symbol}: notional ({size * price:.2f} USDT) menor a $5. Orden descartada.")
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

    while True:

        try:

            # FIX 429: BTC se obtiene una sola vez por ciclo, no dentro del loop
            try:
                btc_ohlcv = fetch_btc(TIMEFRAME)
                btc_df = pd.DataFrame(
                    btc_ohlcv,
                    columns=["time","open","high","low","close","volume"]
                )
                btc_df["btc_ema200"] = btc_df["close"].ewm(span=200).mean()
                btc_close     = btc_df.iloc[-1]["close"]
                btc_ema200    = btc_df.iloc[-1]["btc_ema200"]
                btc_below_ema = btc_close <= btc_ema200
            except Exception as e:
                print(f"⚠️ Error BTC en position_manager: {repr(e)}")
                btc_below_ema = False

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

                # FIX 429: get_cached_price en lugar de fetch_ticker directo.
                # Con 8 posiciones y ciclo de 5s, fetch_ticker directo generaba
                # ~96 llamadas/min. Con caché de 10s baja a ~8 llamadas/min.
                price = get_cached_price(symbol)

                entry      = pos["entry"]
                side       = pos["side"]
                initial_sl = pos["initial_sl"]
                sl_dist    = abs(entry - initial_sl)

                if sl_dist == 0:
                    continue

                # FIX: si BTC rompe la EMA200 protegemos LONGs abiertos
                # moviendo el SL a breakeven aunque no hayamos llegado a RR1
                if btc_below_ema and side == "LONG" and not pos.get("be_set", False):
                    print(f"⚠️ BTC bajo EMA200 — protegiendo {symbol} con SL en breakeven")
                    executor.update_sl(symbol, side, pos["size"], entry)
                    pos["sl"]     = entry
                    pos["be_set"] = True
                    send_telegram(
                        f"⚠️ BTC bajo EMA200\n"
                        f"SL movido a breakeven en {symbol}"
                    )

                rr = abs(price - entry) / sl_dist

                if rr >= 1 and not pos.get("be_set", False):
                    executor.update_sl(symbol, side, pos["size"], entry)
                    pos["sl"]     = entry
                    pos["be_set"] = True

                profit = (
                    (price - entry) / entry if side == "LONG"
                    else (entry - price) / entry
                )

                if profit >= 0.04 and not pos.get("trail_on", False):
                    pos["trail_on"] = True
                    if side == "LONG":
                        pos["trail_high"] = price
                    else:
                        pos["trail_low"] = price

                # FIX: trailing stop correcto para LONG y SHORT
                if pos.get("trail_on", False):

                    if side == "LONG":

                        if price > pos.get("trail_high", price):
                            pos["trail_high"] = price

                        trailing_sl = pos["trail_high"] * (1 - 0.022)

                        if trailing_sl > pos["sl"]:
                            executor.update_sl(symbol, "LONG", pos["size"], trailing_sl)
                            pos["sl"] = trailing_sl

                    elif side == "SHORT":

                        if price < pos.get("trail_low", price):
                            pos["trail_low"] = price

                        trailing_sl = pos["trail_low"] * (1 + 0.022)

                        if trailing_sl < pos["sl"]:
                            executor.update_sl(symbol, "SHORT", pos["size"], trailing_sl)
                            pos["sl"] = trailing_sl

                # FIX 429: pequeño delay entre posiciones para espaciar
                # las llamadas a la API cuando hay muchas posiciones abiertas
                time.sleep(0.3)

        except Exception as e:
            print(f"⚠️ Position manager error: {e}")

        time.sleep(5)


# ──────────────────────────────────────────────
# Arranque de threads
# ──────────────────────────────────────────────

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