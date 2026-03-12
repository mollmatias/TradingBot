import ccxt

exchange = ccxt.bitget({
    "enableRateLimit": True,
    "options": {
        "defaultType": "swap"
    }
})

def fetch_ohlcv(symbol, timeframe, limit=200):
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    return ohlcv

def fetch_btc(timeframe, limit=200):
    return exchange.fetch_ohlcv("BTC/USDT", timeframe, limit=limit)