import time
import math
import pandas as pd
from execution.bitget_executor import BitgetExecutor
from execution.paper_executor import PaperExecutor
from pair_selector import select_top_pairs
from dynamic_risk import dynamic_risk
from config import SYMBOLS,TIMEFRAME,TAKER_FEE,SL_ATR_MULT,TP_ATR_MULT
from data_feed import fetch_ohlcv

# Timeframe mayor para el filtro HTF.
# Si tu TIMEFRAME operativo es "1h", usar "4h".
# Si es "15m", usar "1h". Ajustar según config.
HTF_TIMEFRAME = "4h"
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

# ──────────────────────────────────────────────
# MODO PAPER TRADING
# True  → simula todo, sin órdenes reales a Bitget
# False → trading real
# ──────────────────────────────────────────────
DRY_RUN       = True
PAPER_BALANCE = 1000.0  # balance virtual inicial en USDT

# ──────────────────────────────────────────────
# FIX 429 — caché de precios
# En lugar de llamar fetch_ticker() por cada posición en cada ciclo de 5s,
# guardamos el último precio y lo reutilizamos si tiene menos de TTL segundos.
# Con 8 posiciones abiertas, esto reduce de ~96 llamadas/min a ~8 llamadas/min.
# ──────────────────────────────────────────────
price_cache     = {}
PRICE_CACHE_TTL = 10  # segundos
market_cache    = {}

# ──────────────────────────────────────────────
# Cooldown post-stop-loss
# Después de que un SL cierra una posición, el símbolo queda bloqueado
# por COOLDOWN_AFTER_SL segundos para evitar re-entradas inmediatas
# en mercados en chop que generan rachas de pérdidas consecutivas.
# ──────────────────────────────────────────────
cooldown_until  = {}           # {symbol: timestamp_unix hasta el que está bloqueado}
COOLDOWN_AFTER_SL = 3600       # 1 hora de pausa tras un stop-loss

def api_call_with_retry(fn, *args, max_retries=3, **kwargs):
    """
    FIX 429: Envuelve llamadas a Bitget con retry + backoff exponencial.
    Si la API responde 429, espera 2^intento segundos antes de reintentar.
    Tiempos: 2s -> 4s -> 8s. Así el position_manager nunca explota por rate limit.
    """
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if "429" in str(e):
                wait = 2 ** (attempt + 1)
                print(f"⏳ Rate limit 429 — esperando {wait}s (intento {attempt+1}/{max_retries})...")
                time.sleep(wait)
            else:
                raise
    raise Exception(f"Falló después de {max_retries} intentos por rate limit")


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

if DRY_RUN:
    executor = PaperExecutor(
        os.getenv("BITGET_API_KEY"),
        os.getenv("BITGET_API_SECRET"),
        os.getenv("BITGET_API_PASSPHRASE"),
        initial_balance=PAPER_BALANCE,
        taker_fee=TAKER_FEE
    )
    print("📋 Modo PAPER TRADING activado — ninguna orden se enviará a Bitget")
else:
    executor = BitgetExecutor(
        os.getenv("BITGET_API_KEY"),
        os.getenv("BITGET_API_SECRET"),
        os.getenv("BITGET_API_PASSPHRASE")
    )
    print("🔴 Modo REAL activado")


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
    Solo llama fetch_ticker() cuando el caché está vencido, y con retry backoff.
    """
    now    = time.time()
    cached = price_cache.get(symbol)

    if cached and (now - cached["ts"]) < PRICE_CACHE_TTL:
        return cached["price"]

    ticker = api_call_with_retry(exchange.fetch_ticker, symbol)
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
        return min_amount

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
        "atr_pct":       float(df.iloc[-1].get("atr_pct", 0.022)),
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

                # Obtener datos del timeframe mayor para el filtro HTF
                # Si falla, apply_indicators corre sin filtro HTF (htf_df=None)
                htf_df = None
                try:
                    htf_ohlcv = fetch_ohlcv(symbol, HTF_TIMEFRAME)
                    htf_df = pd.DataFrame(
                        htf_ohlcv,
                        columns=["time","open","high","low","close","volume"]
                    )
                except Exception as e:
                    print(f"⚠️ No se pudo obtener HTF para {symbol}: {repr(e)}")

                df = apply_indicators(df, htf_df=htf_df)
                df["btc_close"]  = btc_close
                df["btc_ema200"] = btc_ema200

                last = df.iloc[-2]

                if last["signal"] is None:
                    continue

                if executor.has_position(symbol):
                    continue

                # Chequeo de cooldown post-SL
                if cooldown_until.get(symbol, 0) > time.time():
                    remaining = int((cooldown_until[symbol] - time.time()) / 60)
                    print(f"⏸ {symbol}: en cooldown, {remaining} min restantes")
                    continue

                # Correcto — solo bloquea LONGs en mercado bajista
                if last["signal"] == "LONG" and btc_close <= btc_ema200:
                    continue
                if last["signal"] == "SHORT" and btc_close >= btc_ema200:
                    continue
                
                atr   = last["atr"]
                price = last["close"]

                if atr is None or atr == 0:
                    continue

                # FIX 40834: el SL/TP se calcula sobre la vela cerrada (iloc[-2])
                # pero el precio actual puede haberse movido. Obtenemos el precio
                # real actual y validamos que el SL siga siendo coherente.
                # Si el mercado se movió demasiado, descartamos la señal.
                try:
                    current_price = get_cached_price(symbol)
                except Exception:
                    current_price = price

                if last["signal"] == "LONG":
                    sl = round_price_to_precision(current_price - atr * SL_ATR_MULT, symbol)
                    tp = round_price_to_precision(current_price + atr * TP_ATR_MULT, symbol)
                    # Validación: el SL de un LONG debe estar por debajo del precio actual
                    if sl >= current_price:
                        print(f"⚠️ {symbol}: SL LONG ({sl}) >= precio actual ({current_price}). Señal descartada.")
                        continue
                else:
                    sl = round_price_to_precision(current_price + atr * SL_ATR_MULT, symbol)
                    tp = round_price_to_precision(current_price - atr * TP_ATR_MULT, symbol)
                    # Validación: el SL de un SHORT debe estar por encima del precio actual
                    if sl <= current_price:
                        print(f"⚠️ {symbol}: SL SHORT ({sl}) <= precio actual ({current_price}). Señal descartada.")
                        continue

                price = current_price  # usamos el precio real para calcular el size

                balance  = executor.get_total_balance()
                score    = max(last["score_long"], last["score_short"])
                strength = last["signal_strength"]

                # FIX exposición: pasamos número de posiciones abiertas,
                # fuerza de señal y volatilidad del par para dynamic_risk v2
                open_positions_count = len(executor.positions)
                risk_amount = dynamic_risk(
                    last["adx"],
                    balance,
                    open_positions_count,
                    strength=strength,
                    atr_pct=last.get("atr_pct", None)
                )

                sl_distance = abs(price - sl)
                raw_size    = risk_amount / sl_distance

                # Si el size calculado es menor al mínimo del par, round_to_precision
                # lo sube automáticamente al mínimo permitido por Bitget.
                size = round_to_precision(raw_size, symbol)

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
                    strength,
                    atr_pct=float(last.get("atr_pct", 0.022))
                )

            except Exception as e:
                print(f"⚠️ ERROR trading {symbol}: {e}")

        # DIAGNÓSTICO TEMPORAL — sacar cuando el bot esté funcionando bien
        for symbol in SYMBOLS:
            try:
                ohlcv = fetch_ohlcv(symbol, TIMEFRAME)
                df = pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])
                df = apply_indicators(df)
                last = df.iloc[-2]
                sl = last["score_long"]
                ss = last["score_short"]
                adx = last["adx"]
                rsi = last["rsi"]
                print(f"📊 {symbol} | SL={sl:.1f} SS={ss:.1f} | ADX={adx:.1f} RSI={rsi:.1f} | señal={last['signal']}")
            except Exception as e:
                print(f"⚠️ Diag error {symbol}: {e}")

        time.sleep(60)


def round_price_to_precision(price, symbol):
    """
    FIX SL/TP precision: Bitget también valida la precisión del precio en SL/TP.
    Igual que con el amount, redondeamos al tick size del par antes de enviar.
    """
    market     = market_cache.get(symbol, {})
    tick_size  = market.get("precision", {}).get("price", None)

    if tick_size is None:
        return price

    if isinstance(tick_size, int):
        factor = 10 ** tick_size
        return round(price * factor) / factor
    else:
        price = round(price / tick_size) * tick_size
        return round(price, 10)


def position_manager(executor, exchange):

    while True:

        try:

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

                # Si la posición cerró con pérdida (SL tocado), activar cooldown
                if net_pnl < 0:
                    cooldown_until[pos["symbol"]] = time.time() + COOLDOWN_AFTER_SL
                    print(f"🛑 SL en {pos['symbol']} — cooldown de {COOLDOWN_AFTER_SL//60} min activado")

                send_telegram(
                    f"📉 Trade cerrado\n"
                    f"{pos['symbol']} {pos['side']}\n"
                    f"PnL: {net_pnl:.2f}"
                    + (f"\n⏸ Cooldown {COOLDOWN_AFTER_SL//60} min" if net_pnl < 0 else "")
                )

            for symbol, pos in list(executor.positions.items()):

                # FIX 429: get_cached_price con retry backoff interno
                try:
                    price = get_cached_price(symbol)
                except Exception as e:
                    print(f"⚠️ No se pudo obtener precio de {symbol}: {repr(e)}")
                    time.sleep(1)
                    continue

                entry      = pos["entry"]
                side       = pos["side"]
                initial_sl = pos["initial_sl"]
                sl_dist    = abs(entry - initial_sl)

                if sl_dist == 0:
                    continue

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

             
                # Trailing se activa cuando el profit supera RR 1.5
                # (antes era 4% fijo — se activaba muy temprano en mercados volátiles
                # y cortaba ganancias antes de llegar al TP de 3.5x ATR)
                atr        = pos.get("atr", 0)
                sl_dist    = abs(entry - pos["initial_sl"])
                rr_current = abs(price - entry) / sl_dist if sl_dist > 0 else 0
 
                if rr_current >= 1.5 and not pos.get("trail_on", False):
                    pos["trail_on"] = True
                    if side == "LONG":
                        pos["trail_high"] = price
                    else:
                        pos["trail_low"] = price
                        
                if pos.get("trail_on", False):

                    if side == "LONG":

                        if price > pos.get("trail_high", price):
                            pos["trail_high"] = price

                        # Trailing dinámico: distancia = ATR_pct * multiplicador
                        # En vez del 2.2% fijo, se adapta a la volatilidad real del par.
                        # Multiplicador 1.2 → trailing más ajustado que el SL inicial (1.5x ATR).
                        atr_pct_live = pos.get("atr_pct", 0.022)   # fallback al 2.2% si no hay dato
                        trail_dist   = max(atr_pct_live * 1.2, 0.010)   # mínimo 1% para no ser demasiado ajustado
                        trailing_sl  = round_price_to_precision(
                            pos["trail_high"] * (1 - trail_dist), symbol
                        )

                        if trailing_sl > pos["sl"]:
                            executor.update_sl(symbol, "LONG", pos["size"], trailing_sl)
                            pos["sl"] = trailing_sl

                    elif side == "SHORT":

                        if price < pos.get("trail_low", price):
                            pos["trail_low"] = price

                        atr_pct_live = pos.get("atr_pct", 0.022)
                        trail_dist   = max(atr_pct_live * 1.2, 0.010)
                        trailing_sl  = round_price_to_precision(
                            pos["trail_low"] * (1 + trail_dist), symbol
                        )

                        if trailing_sl < pos["sl"]:
                            executor.update_sl(symbol, "SHORT", pos["size"], trailing_sl)
                            pos["sl"] = trailing_sl

                # Delay entre posiciones para espaciar llamadas a la API
                time.sleep(0.5)

        except Exception as e:
            # FIX 429: si el position_manager entero explota por rate limit,
            # esperamos con backoff en lugar de reintentar inmediatamente.
            err = str(e)
            if "429" in err:
                print(f"⚠️ Position manager rate limit — esperando 10s...")
                time.sleep(10)
            else:
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

# Contador para imprimir resumen de paper trading cada 30 minutos
_summary_counter = 0

while True:
    time.sleep(60)
    if DRY_RUN:
        _summary_counter += 1
        if _summary_counter >= 30:
            executor.print_summary()
            _summary_counter = 0