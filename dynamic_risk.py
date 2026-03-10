def dynamic_risk(score, balance):

    if score >= 6:
        risk_pct = 0.014

    elif score == 5:
        risk_pct = 0.01

    elif score == 4:
        risk_pct = 0.007

    else:
        risk_pct = 0.004

    risk_amount = balance * risk_pct

    return risk_amount