import pandas as pd
import numpy as np


def apply_indicators(df, htf_df=None):
    """
    Motor de indicadores y señales.

    Args:
        df:     DataFrame OHLCV del timeframe operativo (ej: 1h)
        htf_df: DataFrame OHLCV del timeframe mayor (ej: 4h).
                Opcional. Si se pasa, activa el filtro de tendencia HTF.

    Mejoras respecto a la versión anterior:
        - Bug fix: el volumen ya no suma puntos a ambos lados a la vez
        - RSI reemplazado por Stochastic RSI (más sensible en tendencia)
        - MACD: se usa aceleración del histograma en vez de cruce de líneas
        - Filtro de tendencia HTF (4h) si se pasa htf_df
        - Pesos dinámicos: el ADX amplifica los scores en mercados con tendencia
        - Penalización por RSI extremo (evita entrar en sobrecompra/sobreventa)
        - Filtro ATR mínimo: no entrar si el mercado está demasiado quieto
        - Threshold SPLUS subido a 9 para reducir falsos setups premium
        - Score mínimo de señal sube a 6 para mayor selectividad
    """

    df = df.copy()

    # ── RSI ──────────────────────────────────────────────────────────────────

    delta    = df["close"].diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()   # Wilder smoothing
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    # Stochastic RSI — más sensible que RSI simple para detectar momentum
    rsi_min  = df["rsi"].rolling(14).min()
    rsi_max  = df["rsi"].rolling(14).max()
    rsi_rng  = (rsi_max - rsi_min).replace(0, np.nan)
    df["stoch_rsi"] = (df["rsi"] - rsi_min) / rsi_rng * 100
    df["stoch_rsi_signal"] = df["stoch_rsi"].rolling(3).mean()

    # ── MACD ─────────────────────────────────────────────────────────────────

    ema_fast          = df["close"].ewm(span=12, adjust=False).mean()
    ema_slow          = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"]        = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"]   = df["macd"] - df["macd_signal"]

    # Aceleración del histograma: ¿está creciendo o achicándose?
    # Esto captura el momentum del momentum — mucho más predictivo que el cruce
    df["macd_accel"] = df["macd_hist"].diff()

    # ── EMAs ─────────────────────────────────────────────────────────────────

    df["ema20"]  = df["close"].ewm(span=20).mean()
    df["ema50"]  = df["close"].ewm(span=50).mean()
    df["ema200"] = df["close"].ewm(span=200).mean()

    # Pendiente de EMA50: ¿la tendencia está acelerando o frenando?
    df["ema50_slope"] = df["ema50"].diff(3) / df["ema50"].shift(3) * 100

    # ── ATR ──────────────────────────────────────────────────────────────────

    high_low   = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close  = (df["low"]  - df["close"].shift()).abs()

    df["tr"]  = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = df["tr"].rolling(14).mean()

    df["atr_mean"]      = df["atr"].rolling(50).mean()
    df["atr_pct"]       = df["atr"] / df["close"]          # ATR como % del precio — usado por trailing dinámico
    df["atr_expansion"] = df["atr"] > df["atr_mean"] * 1.2

    # ── ADX ──────────────────────────────────────────────────────────────────

    plus_dm  = df["high"].diff()
    minus_dm = -df["low"].diff()

    plus_dm  = plus_dm.where((plus_dm > minus_dm)  & (plus_dm  > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr14     = df["tr"].rolling(14).sum()
    plus_di  = 100 * (plus_dm.rolling(14).sum()  / tr14)
    minus_di = 100 * (minus_dm.rolling(14).sum() / tr14)

    dx       = abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan) * 100
    df["adx"]      = dx.rolling(14).mean()
    df["adx_fast"] = dx.rolling(10).mean()   # más reactivo para señales en 1h
    df["plus_di"]  = plus_di
    df["minus_di"] = minus_di

    # ── VOLUMEN ──────────────────────────────────────────────────────────────

    df["vol_mean"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_mean"]

    # Volumen con dirección: positivo si cierra arriba, negativo si cierra abajo
    # Esto es la base de un OBV simplificado
    df["vol_delta"] = df["volume"] * df["close"].diff().apply(
        lambda x: 1 if x > 0 else (-1 if x < 0 else 0)
    )
    df["vol_delta_ma"] = df["vol_delta"].rolling(10).mean()

    # ── BOLLINGER ────────────────────────────────────────────────────────────

    bb_mid         = df["close"].rolling(20).mean()
    bb_std         = df["close"].rolling(20).std()
    df["bb_upper"] = bb_mid + 2 * bb_std
    df["bb_lower"] = bb_mid - 2 * bb_std
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["close"]
    df["bb_width_mean"] = df["bb_width"].rolling(50).mean()

    # Squeeze: las BBs están comprimidas (potencial de expansión)
    df["bb_squeeze"] = df["bb_width"] < df["bb_width_mean"] * 0.8

    # ── BREAKOUT LEVELS ──────────────────────────────────────────────────────

    df["high_35"] = df["high"].rolling(35).max()
    df["low_35"]  = df["low"].rolling(35).min()

    # Distancia al breakout como % — para saber si rompió limpio o por poco
    df["dist_to_high35"] = (df["close"] - df["high_35"].shift(1)) / df["close"]
    df["dist_to_low35"]  = (df["low_35"].shift(1) - df["close"]) / df["close"]

    # ── FILTRO HTF (timeframe mayor) ─────────────────────────────────────────
    # Si se pasa el DataFrame de 4h, calculamos la tendencia macro
    # y la mapeamos sobre el DataFrame operativo por timestamp

    df["htf_trend"] = 0  # 1 = alcista, -1 = bajista, 0 = sin dato

    if htf_df is not None:
        htf = htf_df.copy()
        htf["ema50_htf"]  = htf["close"].ewm(span=50).mean()
        htf["ema200_htf"] = htf["close"].ewm(span=200).mean()
        htf["rsi_htf"]    = _calc_rsi(htf["close"])
        htf["trend_htf"]  = 0
        htf.loc[
            (htf["ema50_htf"] > htf["ema200_htf"]) & (htf["rsi_htf"] > 45),
            "trend_htf"
        ] = 1
        htf.loc[
            (htf["ema50_htf"] < htf["ema200_htf"]) & (htf["rsi_htf"] < 55),
            "trend_htf"
        ] = -1

        # Merge por tiempo: merge_asof busca la última vela HTF cerrada
        # para cada vela LTF. Es O(n log n) en vez de O(n²) con apply().
        htf_slim = htf[["time", "trend_htf"]].sort_values("time")
        df = pd.merge_asof(
            df.sort_values("time"),
            htf_slim,
            on="time",
            direction="backward",
            suffixes=("", "_htf_merge")
        )
        # merge_asof deja NaN donde no hay HTF anterior → rellenar con 0
        df["trend_htf"] = df["trend_htf"].fillna(0).astype(int)
        df["htf_trend"] = df["trend_htf"]
        df.drop(columns=["trend_htf"], inplace=True)

    # ─────────────────────────────────────────────────────────────────────────
    # MOTOR DE SCORING
    # Max teórico sin ADX boost: 10 puntos por lado
    # Threshold señal normal : 6
    # Threshold STRONG       : 8
    # Threshold SPLUS        : 9
    # ─────────────────────────────────────────────────────────────────────────

    df["score_long"]  = 0.0
    df["score_short"] = 0.0

    # ── 1. TENDENCIA EMA (peso 3) ─────────────────────────────────────────────
    # EMA50 > EMA200 con pendiente positiva = tendencia alcista sólida
    df.loc[
        (df["ema50"] > df["ema200"]) & (df["ema50_slope"] > 0),
        "score_long"
    ] += 3
    df.loc[
        (df["ema50"] < df["ema200"]) & (df["ema50_slope"] < 0),
        "score_short"
    ] += 3

    # Tendencia presente pero sin aceleración: medio punto menos
    df.loc[
        (df["ema50"] > df["ema200"]) & (df["ema50_slope"] <= 0),
        "score_long"
    ] += 2
    df.loc[
        (df["ema50"] < df["ema200"]) & (df["ema50_slope"] >= 0),
        "score_short"
    ] += 2

    # ── 2. MOMENTUM MACD — histograma acelerando (peso 2) ────────────────────
    # FIX: reemplazamos el cruce (rezagado) por la aceleración del histograma
    # Si el histograma es positivo Y está creciendo, el momentum está aumentando
    df.loc[
        (df["macd_hist"] > 0) & (df["macd_accel"] > 0),
        "score_long"
    ] += 2
    df.loc[
        (df["macd_hist"] < 0) & (df["macd_accel"] < 0),
        "score_short"
    ] += 2

    # Histograma en dirección correcta pero desacelerando: 1 punto
    df.loc[
        (df["macd_hist"] > 0) & (df["macd_accel"] <= 0),
        "score_long"
    ] += 1
    df.loc[
        (df["macd_hist"] < 0) & (df["macd_accel"] >= 0),
        "score_short"
    ] += 1

    # ── 3. STOCHASTIC RSI (peso 2) ───────────────────────────────────────────
    # FIX: reemplazamos RSI>50 por StochRSI cruzando la señal en zona direccional
    # StochRSI > 50 y cruzando arriba = momentum real confirmado
    df.loc[
        (df["stoch_rsi"] > 50) & (df["stoch_rsi"] > df["stoch_rsi_signal"]),
        "score_long"
    ] += 2
    df.loc[
        (df["stoch_rsi"] < 50) & (df["stoch_rsi"] < df["stoch_rsi_signal"]),
        "score_short"
    ] += 2

    # ── 4. VOLUMEN CON DIRECCIÓN (peso 1) ────────────────────────────────────
    # FIX: ya no sumamos el mismo punto a ambos lados
    # Solo suma si el volumen es alto Y el delta va en la dirección correcta
    df.loc[
        (df["vol_ratio"] > 1.5) & (df["vol_delta_ma"] > 0),
        "score_long"
    ] += 1
    df.loc[
        (df["vol_ratio"] > 1.5) & (df["vol_delta_ma"] < 0),
        "score_short"
    ] += 1

    # ── 5. BREAKOUT DE ESTRUCTURA (peso 2) ───────────────────────────────────
    # Breakout limpio: superó el máximo de 35 velas con al menos 0.1% de margen
    df.loc[
        (df["close"] > df["high_35"].shift(1)) & (df["dist_to_high35"] > 0.001),
        "score_long"
    ] += 2
    df.loc[
        (df["close"] < df["low_35"].shift(1)) & (df["dist_to_low35"] > 0.001),
        "score_short"
    ] += 2

    # ── 6. FILTRO HTF — bonus/penalización (peso 1) ──────────────────────────
    # Si el timeframe mayor confirma la dirección, suma 1 punto extra
    # Si va en contra, resta 1 punto (penalización)
    if htf_df is not None:
        df.loc[df["htf_trend"] == 1,  "score_long"]  += 1
        df.loc[df["htf_trend"] == 1,  "score_short"] -= 1
        df.loc[df["htf_trend"] == -1, "score_short"] += 1
        df.loc[df["htf_trend"] == -1, "score_long"]  -= 1

    # ── PENALIZACIONES ───────────────────────────────────────────────────────

    # RSI en sobrecompra extrema: no entrar LONG (probable corrección inminente)
    df.loc[df["rsi"] > 78, "score_long"]  -= 2

    # RSI en sobreventa extrema: no entrar SHORT
    df.loc[df["rsi"] < 22, "score_short"] -= 2

    # ADX muy bajo (<18): mercado sin tendencia definida, penalizar ambos lados
    # Usamos adx_fast para detectar pérdida de tendencia más rápido
    df.loc[df["adx_fast"] < 18, "score_long"]  -= 1
    df.loc[df["adx_fast"] < 18, "score_short"] -= 1

    # ATR demasiado bajo: mercado quieto, no hay suficiente volatilidad para operar
    # Si el ATR está por debajo del 60% de su media histórica, penalizar
    df.loc[df["atr"] < df["atr_mean"] * 0.6, "score_long"]  -= 1
    df.loc[df["atr"] < df["atr_mean"] * 0.6, "score_short"] -= 1

    # Clampear scores: no permitir negativos
    df["score_long"]  = df["score_long"].clip(lower=0)
    df["score_short"] = df["score_short"].clip(lower=0)

    # ── BOOST POR ADX ────────────────────────────────────────────────────────
    # Usamos adx_fast (periodo 10) para que el boost sea más reactivo en 1h.
    # ADX estándar (14) llega tarde — cuando supera 35 el movimiento ya corrió.
    df.loc[
        (df["adx_fast"] > 35) & (df["plus_di"] > df["minus_di"]),
        "score_long"
    ] += 1
    df.loc[
        (df["adx_fast"] > 35) & (df["minus_di"] > df["plus_di"]),
        "score_short"
    ] += 1

    # ── SEÑAL FINAL ──────────────────────────────────────────────────────────
    # Threshold subido de 5 a 6 para mayor selectividad

    df["signal"] = None

    # Solo generar señal si el score del lado ganador supera al perdedor por al menos 2
    # Esto evita señales en mercados con ambos scores altos (ambigüedad)
    long_valid  = (df["score_long"]  >= 6) & (df["score_long"]  > df["score_short"]  + 1)
    short_valid = (df["score_short"] >= 6) & (df["score_short"] > df["score_long"]   + 1)

    df.loc[long_valid,  "signal"] = "LONG"
    df.loc[short_valid, "signal"] = "SHORT"

    # ── SIGNAL STRENGTH ──────────────────────────────────────────────────────

    df["signal_strength"] = "NORMAL"

    df.loc[df["score_long"]  >= 8, "signal_strength"] = "STRONG"
    df.loc[df["score_short"] >= 8, "signal_strength"] = "STRONG"

    # SPLUS: threshold subido a 9, requiere ADX fuerte Y volumen real
    # También requiere que el breakout esté confirmado (dist > 0.1%)
    # Usamos adx_fast para mayor reactividad
    splus_long = (
        (df["score_long"]  >= 9) &
        (df["adx_fast"]    >  28) &
        (df["vol_ratio"]   >  1.5) &
        (df["dist_to_high35"] > 0.001) &
        (df["signal"]      == "LONG")
    )
    splus_short = (
        (df["score_short"] >= 9) &
        (df["adx_fast"]    >  28) &
        (df["vol_ratio"]   >  1.5) &
        (df["dist_to_low35"]  > 0.001) &
        (df["signal"]      == "SHORT")
    )

    df.loc[splus_long,  "signal_strength"] = "SPLUS"
    df.loc[splus_short, "signal_strength"] = "SPLUS"

    return df


def _calc_rsi(close, period=14):
    """Helper para calcular RSI en el HTF sin depender del df completo."""
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))