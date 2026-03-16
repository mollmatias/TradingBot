def dynamic_risk(adx, balance, open_positions_count=0):
    """
    Calcula el monto en riesgo por operación según el ADX actual.

    FIX: Se agrega 'open_positions_count' para limitar la exposición
    total simultánea al 8% del balance, evitando que 8 posiciones
    en ADX alto expongan el 16% del capital en un solo evento de mercado.
    """

    if adx < 20:
        risk_pct = 0.004

    elif adx < 35:
        risk_pct = 0.01

    else:
        risk_pct = 0.02

    # FIX: Cap de exposición total máxima simultánea
    # Si hay posiciones abiertas, revisamos que no superemos el 8% del balance
    # en riesgo agregado. Si se supera, achicamos el riesgo por operación.
    MAX_TOTAL_RISK_PCT = 0.08

    if open_positions_count > 0:
        risk_per_pos_capped = MAX_TOTAL_RISK_PCT / open_positions_count
        risk_pct = min(risk_pct, risk_per_pos_capped)

    return balance * risk_pct
