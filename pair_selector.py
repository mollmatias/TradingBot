import pandas as pd
from data_feed import fetch_ohlcv


SCAN_PAIRS = [

"BTC/USDT:USDT",
"ETH/USDT:USDT",
"SOL/USDT:USDT",
"XRP/USDT:USDT",
"LINK/USDT:USDT",
"BNB/USDT:USDT",
"ADA/USDT:USDT",
"AVAX/USDT:USDT",
"INJ/USDT:USDT",
"SUI/USDT:USDT",
"APT/USDT:USDT",
"OP/USDT:USDT",
"ARB/USDT:USDT",
"DOGE/USDT:USDT",
"LTC/USDT:USDT"

]


def calculate_atr(df):

    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()

    tr = pd.concat(
        [high_low, high_close, low_close], axis=1
    ).max(axis=1)

    atr = tr.rolling(14).mean()

    return atr


def score_pair(symbol, timeframe):

    try:

        ohlcv = fetch_ohlcv(symbol, timeframe)

        df = pd.DataFrame(
            ohlcv,
            columns=[
                "time","open","high","low","close","volume"
            ]
        )

        df["atr"] = calculate_atr(df)

        atr_pct = df.iloc[-1]["atr"] / df.iloc[-1]["close"]

        volume_mean = df["volume"].rolling(20).mean()
        vol_ratio = df.iloc[-1]["volume"] / volume_mean.iloc[-1]

        score = atr_pct * 100 + vol_ratio

        return score

    except:

        return 0


def select_top_pairs(timeframe, top_n=8):

    scores = []

    for symbol in SCAN_PAIRS:

        score = score_pair(symbol, timeframe)

        scores.append(
            (symbol, score)
        )

    scores.sort(
        key=lambda x: x[1],
        reverse=True
    )

    selected = [
        s[0]
        for s in scores[:top_n]
    ]

    print("TOP VOLATILITY PAIRS")

    for s in scores[:top_n]:

        print(s)

    return selected