from execution.bitget_executor import BitgetExecutor
from access_token import API_KEY, API_SECRET, API_PASSPHRASE

executor = BitgetExecutor(API_KEY, API_SECRET, API_PASSPHRASE)

balance = executor.get_balance()

positions = executor.exchange.fetch_open_orders()
print(f"{positions}")
