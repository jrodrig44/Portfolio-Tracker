import pandas as pd
from app import _build_ai_export_payload

holdings = pd.DataFrame([
    {'ticker': 'HD', 'shares': 10, 'amount_invested': 1000, 'regular_biweekly_contribution': 0, 'espp_biweekly_contribution': 100}
])
metrics = pd.DataFrame([
    {'ticker': 'HD', 'market_value': 2000, 'annual_income': 9.6, 'regular_biweekly_contribution': 0, 'espp_biweekly_contribution': 100}
])
payload = _build_ai_export_payload(holdings, metrics)
print(payload['contribution_context']['holdings'][0])
print('---')
print(next(row for row in payload['calculated_metrics'] if row['ticker'] == 'HD'))
