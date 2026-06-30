def get_drawdown_multiplier(portfolio_value, peak_value):
    if not peak_value or peak_value <= 0:
        return 1.0, "normal"
    dd = (peak_value - portfolio_value) / peak_value * 100
    if dd < 5:   return 1.0, "normal"
    if dd < 10:  return 0.75, "warning"
    if dd < 15:  return 0.50, "severe (only score>=80)"
    return 0.0, "halt"
