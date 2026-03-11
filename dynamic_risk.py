def dynamic_risk(score, balance, strength):

    if strength == "SPLUS":

        risk_pct = 0.02

    elif strength == "STRONG":

        risk_pct = 0.012

    elif score >= 5:

        risk_pct = 0.008

    else:

        risk_pct = 0.003

    return balance * risk_pct