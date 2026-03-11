import pandas as pd

def apply_indicators(df):

    # ================= RSI =================

    delta = df["close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))

    # ================= MACD =================

    ema_fast = df["close"].ewm(span=12, adjust=False).mean()
    ema_slow = df["close"].ewm(span=26, adjust=False).mean()

    df["macd"] = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()

    # ================= EMA =================

    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["ema200"] = df["close"].ewm(span=200).mean()

    # ================= ATR =================

    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()

    df["tr"] = pd.concat(
        [high_low, high_close, low_close], axis=1
    ).max(axis=1)

    df["atr"] = df["tr"].rolling(14).mean()
    df["atr_mean"] = df["atr"].rolling(50).mean()
    df["atr_expansion"] = df["atr"] > df["atr_mean"] * 1.2
    # ================= ADX =================

    plus_dm = df["high"].diff()
    minus_dm = df["low"].diff()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

    tr14 = df["tr"].rolling(14).sum()

    plus_di = 100 * (plus_dm.rolling(14).sum() / tr14)
    minus_di = 100 * (minus_dm.rolling(14).sum() / tr14)

    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100
    df["adx"] = dx.rolling(14).mean()

    # ================= VOLUME =================

    df["vol_mean"] = df["volume"].rolling(20).mean()

    # ================= BOLLINGER =================

    bb_mid = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()

    df["bb_upper"] = bb_mid + 2 * bb_std
    df["bb_lower"] = bb_mid - 2 * bb_std

    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["close"]
    df["bb_width_mean"] = df["bb_width"].rolling(50).mean()

    # ================= BREAKOUT LEVELS =================

    df["high_20"] = df["high"].rolling(20).max()
    df["low_20"] = df["low"].rolling(20).min()

    # ================= SIGNAL ENGINE =================

    df["score_long"] = 0
    df["score_short"] = 0

    # TREND

    df.loc[df["ema50"] > df["ema200"], "score_long"] += 2
    df.loc[df["ema50"] < df["ema200"], "score_short"] += 2

    # MOMENTUM

    df.loc[df["macd"] > df["macd_signal"], "score_long"] += 1
    df.loc[df["macd"] < df["macd_signal"], "score_short"] += 1

    # RSI

    df.loc[df["rsi"] > 50, "score_long"] += 1
    df.loc[df["rsi"] < 50, "score_short"] += 1

    # VOLUME

    df.loc[df["volume"] > df["vol_mean"], "score_long"] += 1
    df.loc[df["volume"] > df["vol_mean"], "score_short"] += 1

    # BREAKOUT

    df.loc[df["close"] > df["high_20"].shift(1), "score_long"] += 2
    df.loc[df["close"] < df["low_20"].shift(1), "score_short"] += 2

    # VOLATILITY EXPANSION

    df.loc[df["bb_width"] > df["bb_width_mean"], "score_long"] += 1
    df.loc[df["bb_width"] > df["bb_width_mean"], "score_short"] += 1

    # ATR Expansion Filter
    df.loc[df["atr_expansion"], "score_long"] += 1
    df.loc[df["atr_expansion"], "score_short"] += 1

    # ================= SIGNAL =================

    df["signal"] = None

    df.loc[df["score_long"] >= 4, "signal"] = "LONG"
    df.loc[df["score_short"] >= 4, "signal"] = "SHORT"

    # ================= SIGNAL STRENGTH =================

    df["signal_strength"] = "NORMAL"

    df.loc[
        (df["score_long"] >= 6),
        "signal_strength"
    ] = "STRONG"

    df.loc[
        (df["score_short"] >= 6),
        "signal_strength"
    ] = "STRONG"

    # ================= SPLUS SETUP =================

    df.loc[
        (df["score_long"] >= 7) &
        (df["adx"] > 25) &
        (df["volume"] > df["vol_mean"] * 1.5),
        "signal_strength"
    ] = "SPLUS"

    df.loc[
        (df["score_short"] >= 7) &
        (df["adx"] > 25) &
        (df["volume"] > df["vol_mean"] * 1.5),
        "signal_strength"
    ] = "SPLUS"

    return df