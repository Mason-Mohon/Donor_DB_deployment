import pandas as pd
import os
from datetime import datetime

# Define appeal code mappings
appeal_mappings = {
    'N': 'SUBS P.S. REPORT',
    'M': 'PURCH MATERIALS ETF',
    'G': 'EAGLE TRUST FUND',
    'L': 'EFELDF (TAX-DEDUCTIBLE)',
    'E': 'PS EAGLES',
    'C': 'REG EAGLE COUNCIL',
    'O': 'PURCH MATERIALS EFELDF'
}

# Read the CSV file
input_file = '2025_transactions_update.csv'
df = pd.read_csv(input_file)

# Clean up the data
df['Amount'] = df['Amount'].str.strip().astype(float)
df['Date'] = pd.to_datetime(df['Date'])
df['appeal_code'] = df['appeal_code'].str.strip()
df['payment_type'] = df['payment_type'].str.strip()
df['payment_method'] = df['payment_method'].str.strip()
df['update_batch_num'] = df['update_batch_num'].str.strip()

# Apply appeal code mappings
df['bluebook_job_description'] = df['appeal_code'].map(appeal_mappings)
df['bluebook_list_description'] = df['appeal_code'].map(appeal_mappings)

# Drop empty columns
df = df.dropna(axis=1, how='all')

# Generate timestamp for filename
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
output_file = f'transactions_2025_updated_{timestamp}.csv'

# Save the updated CSV
df.to_csv(output_file, index=False)

print(f"✅ Successfully processed and saved {len(df)} transactions to '{output_file}'")
print(f"📊 Total transaction amount: ${df['Amount'].sum():,.2f}")
print(f"📈 Date range: {df['Date'].min().strftime('%Y-%m-%d')} to {df['Date'].max().strftime('%Y-%m-%d')}")

# Show first few rows as preview
print("\n🔍 Preview of first 3 rows:")
preview_cols = ['Date', 'Amount', 'appeal_code', 'bluebook_job_description']
print(df[preview_cols].head(3).to_string(index=False)) 