import pandas as pd

def apply_indicators(df):


    # ===============================
    # INDICADORES
    # ===============================

    # RSI
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))

    # MACD
    ema_fast = df["close"].ewm(span=12, adjust=False).mean()
    ema_slow = df["close"].ewm(span=26, adjust=False).mean()

    df["macd"] = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_slope"] = df["macd"].diff()

    # EMA 200 (tendencia HTF)
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

    # ATR
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()

    df["tr"] = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = df["tr"].rolling(14).mean()

    # Normalización MACD slope
    df["macd_abs_mean"] = df["macd"].abs().rolling(50).mean()

    # ===============================
    # PARÁMETROS AJUSTADOS
    # ===============================

    EMA_MARGIN = 0.01          # antes implícito / duro
    RSI_LONG = 42              # antes 45
    RSI_SHORT = 58             # antes 55

    MACD_SLOPE_FACTOR = 0.06   # antes 0.05 → más trades

    # ===============================
    # SEÑALES
    # ===============================

    df["signal"] = None

    # -------- LONG --------
    df.loc[
        (
            (df["close"] > df["ema200"] * (1 - EMA_MARGIN)) &
            (df["rsi"] < RSI_LONG) &
            (df["macd"] > df["macd_signal"]) &
            (df["macd_slope"] >
            -df["macd_abs_mean"] * MACD_SLOPE_FACTOR)
        ),
        "signal"
    ] = "LONG"

    # -------- SHORT --------
    df.loc[
        (
            (df["close"] < df["ema200"] * (1 + EMA_MARGIN)) &
            (df["rsi"] > RSI_SHORT) &
            (df["macd"] < df["macd_signal"]) &
            (df["macd_slope"] <
            df["macd_abs_mean"] * MACD_SLOPE_FACTOR)
        ),
        "signal"
    ] = "SHORT"

    return df