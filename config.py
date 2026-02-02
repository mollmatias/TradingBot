from execution.bitget_executor import BitgetExecutor
from access_token import API_KEY, API_SECRET, API_PASSPHRASE
import ccxt

exchange = ccxt.bitget({
        "apiKey":API_KEY,
        "secret":API_SECRET,
        "password":API_PASSPHRASE,
        "enableRateLimit":True,
        "options":{
            "defaultType":"swap"
        }
    })

executor = BitgetExecutor(API_KEY,API_SECRET,API_PASSPHRASE)




# ===== MODO =====
PAPER_TRADING = False   # False = LIVE

# ===== CAPITAL =====
INITIAL_BALANCE = executor.get_balance()

RISK_PER_TRADE = 0.2
MAX_RISK_PCT = 0.08
LEVERAGE = 20

# ===== TP / SL =====
TP_ATR_MULT = 3.5
SL_ATR_MULT = 1.75

# ===== FEES BITGET (futuros USDT) =====
TAKER_FEE = 0.0006     # 0.06%

# ===== TIMEFRAME =====
TIMEFRAME = "1H"

SYMBOLS = [
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "XRP/USDT:USDT",
    "LINK/USDT:USDT",
    "BNB/USDT:USDT",
    "RIVER/USDT:USDT",
    "ADA/USDT:USDT",
    "ASTER/USDT:USDT"
]
