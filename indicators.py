import pandas as pd

def apply_indicators(df):
    # RSI
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))

    # MACD
    ema_fast = df["close"].ewm(span=12).mean()
    ema_slow = df["close"].ewm(span=26).mean()
    df["macd"] = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=9).mean()
    df["macd_slope"] = df["macd"].diff()

    # EMA 200 (HTF trend)
    df["ema200"] = df["close"].ewm(span=200).mean()

    # ATR
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    df["atr"] = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1).rolling(14).mean()

    df["signal"] = None

    # LONG
    df.loc[
        (df["close"] > df["ema200"]) &
        (df["rsi"] < 45) &
        (df["macd"] > df["macd_signal"]) &
        (df["macd_slope"] > -df["macd"].abs().mean() * 0.05),
        "signal"
    ] = "LONG"

    # SHORT
    df.loc[
        (df["close"] < df["ema200"]) &
        (df["rsi"] > 55) &
        (df["macd"] < df["macd_signal"]) &
        (df["macd_slope"] < df["macd"].abs().mean() * 0.05),
        "signal"
    ] = "SHORT"

    return df
