def calculate_contract_size(balance, risk_pct, price, leverage):
    margin = balance * risk_pct
    notional = margin * leverage
    contracts = notional / price
    return round(contracts, 3)
