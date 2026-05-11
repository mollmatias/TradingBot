def dynamic_risk(adx, balance, open_positions_count=0, strength="NORMAL", atr_pct=None):
    """
    Calcula el monto en riesgo por operación según el ADX actual,
    la fuerza de la señal y la volatilidad del par (ATR%).

    Mejoras v2:
    - Escala el riesgo también por signal_strength (NORMAL / STRONG / SPLUS)
    - Si se pasa atr_pct, aplica un techo de riesgo cuando el par está muy volátil
      (ATR% > 3% → mercados explosivos donde el mismo % de balance implica
      un size mucho mayor al deseable)
    - Cap de exposición total simultánea: igual que antes, máx 8% agregado
    """

    # ── Riesgo base por ADX ───────────────────────────────────────────────────
    if adx < 20:
        risk_pct = 0.004          # mercado sin tendencia: riesgo mínimo
    elif adx < 35:
        risk_pct = 0.010          # tendencia moderada
    else:
        risk_pct = 0.020          # tendencia fuerte

    # ── Multiplicador por fuerza de señal ────────────────────────────────────
    # NORMAL no modifica. STRONG sube 25%. SPLUS sube 50%.
    # Esto concentra capital en las mejores oportunidades sin tocar el riesgo base.
    strength_mult = {
        "NORMAL": 1.00,
        "STRONG": 1.25,
        "SPLUS":  1.50,
    }.get(strength, 1.00)

    risk_pct *= strength_mult

    # ── Techo de volatilidad (ATR%) ───────────────────────────────────────────
    # Si el par está muy volátil (ATR > 3% del precio), reducimos el riesgo.
    # Evita que en altcoins explosivos terminemos con sizes enormes.
    if atr_pct is not None:
        if atr_pct > 0.05:        # ATR > 5%: mercado extremadamente volátil
            risk_pct = min(risk_pct, 0.005)
        elif atr_pct > 0.03:      # ATR entre 3–5%: moderamos
            risk_pct = min(risk_pct, 0.010)

    # ── Cap de exposición total máxima simultánea ─────────────────────────────
    # Nunca superar el 8% de exposición agregada entre todas las posiciones abiertas.
    MAX_TOTAL_RISK_PCT = 0.08

    if open_positions_count > 0:
        risk_per_pos_capped = MAX_TOTAL_RISK_PCT / open_positions_count
        risk_pct = min(risk_pct, risk_per_pos_capped)

    return balance * risk_pct