def dynamic_risk(adx, balance):

    if adx < 20:
        risk_pct = 0.004

    elif adx < 35:
        risk_pct = 0.01

    else:
        risk_pct = 0.02

    return balance * risk_pct