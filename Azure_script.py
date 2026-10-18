"""
Azure GPU VM pricing extractor (public Azure Retail Prices API - no login needed).

Optional: set AZURE_SUBSCRIPTION_ID (plus AZURE_CLIENT_ID / AZURE_TENANT_ID /
AZURE_CLIENT_SECRET) to also pull RAM and GPU count from the Azure SDK.
That needs: pip install azure-identity azure-mgmt-compute
"""
import os
import re
import time
import requests
import numpy as np
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

# ---------------- Settings ----------------
target_folder = os.path.dirname(os.path.abspath(__file__))
os.chdir(target_folder)

OUT_CSV = 'AZURE.csv'
API_URL = 'https://prices.azure.com/api/retail/prices'
# Azure GPU VM families: NC, ND, NV, NG
PREFIXES = ['Standard_NC', 'Standard_ND', 'Standard_NV', 'Standard_NG']
GPU_MODELS = ['MI300X', 'H200', 'H100', 'A100', 'A10', 'V620', 'V100', 'P100', 'P40', 'T4', 'M60', 'K80', 'MI25']

extracted_at = datetime.now(ZoneInfo('Asia/Singapore'))


# ---------------- 1. Fetch prices from the Retail Prices API ----------------
def get_json(url, params=None, retries=5):
    for attempt in range(retries):
        r = requests.get(url, params=params, timeout=60)
        if r.status_code == 429:                      # rate limited: wait and retry
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("Azure Retail Prices API kept returning 429 (rate limit).")


def fetch_prices():
    items = []
    for prefix in PREFIXES:
        flt = (f"serviceName eq 'Virtual Machines' and priceType eq 'Consumption' "
               f"and startswith(armSkuName, '{prefix}')")
        data = get_json(API_URL, params={'$filter': flt})
        n = 0
        while True:
            items.extend(data.get('Items', []))
            n += len(data.get('Items', []))
            next_link = data.get('NextPageLink')
            if not next_link:
                break
            data = get_json(next_link)               # NextPageLink is a full URL
        print(f"{prefix}*: {n} price records")
    return items


# ---------------- 2. Turn raw items into one tidy table ----------------
def parse_name(arm_sku):
    """vCPU and GPU model read from names like Standard_ND96isr_H100_v5."""
    base = arm_sku.replace('Standard_', '')
    m = re.match(r'^N[A-Z](\d+)', base)
    vcpu = int(m.group(1)) if m else np.nan
    model = next((t for t in base.split('_') if t in GPU_MODELS), None)
    return vcpu, model


def build_df(items):
    rows = []
    for it in items:
        if not it.get('isPrimaryMeterRegion', True):
            continue
        if it.get('retailPrice', 0) <= 0:
            continue
        sku_name = it.get('skuName', '')
        if 'Spot' in sku_name:
            ptype = 'SpotPrice'
        elif 'Low Priority' in sku_name:
            ptype = 'LowPriorityPrice'
        else:
            ptype = 'OnDemandPrice'
        rows.append({
            'Region': it['armRegionName'],
            'InstanceType': it['armSkuName'],
            'OS': 'Windows' if 'Windows' in it.get('productName', '') else 'Linux',
            'PriceType': ptype,
            'Price': float(it['retailPrice']),
            'Unit': it.get('unitOfMeasure'),
            'PriceLastChanged': pd.to_datetime(it.get('effectiveStartDate'), utc=True, errors='coerce'),
        })
    raw = pd.DataFrame(rows)
    if raw.empty:
        raise RuntimeError("No price records returned - check the API filter or your connection.")

    # One row per Region / InstanceType / OS, with a column per price type
    wide = raw.pivot_table(index=['Region', 'InstanceType', 'OS'], columns='PriceType',
                           values='Price', aggfunc='min').reset_index()
    wide.columns.name = None
    changed = (raw.groupby(['Region', 'InstanceType', 'OS'])['PriceLastChanged']
               .max().reset_index())
    df = wide.merge(changed, on=['Region', 'InstanceType', 'OS'], how='left')
    for col in ('OnDemandPrice', 'SpotPrice', 'LowPriorityPrice'):
        if col not in df.columns:
            df[col] = np.nan

    parsed = df['InstanceType'].apply(parse_name)
    df['vCPU'] = [p[0] for p in parsed]
    df['Accelerator'] = [p[1] for p in parsed]
    df.insert(0, 'Provider', 'Azure')
    return df


# ---------------- 3. Optional: RAM and GPU count from the Azure SDK ----------------
def get_specs_from_azure():
    sub = os.environ.get('AZURE_SUBSCRIPTION_ID')
    if not sub:
        print("AZURE_SUBSCRIPTION_ID not set - skipping RAM / GPU count (prices still collected).")
        return None
    try:
        from azure.identity import DefaultAzureCredential
        from azure.mgmt.compute import ComputeManagementClient
    except ImportError:
        print("azure-identity / azure-mgmt-compute not installed - skipping specs.")
        return None
    client = ComputeManagementClient(DefaultAzureCredential(), sub)
    rows = {}
    for sku in client.resource_skus.list():
        if sku.resource_type != 'virtualMachines' or not sku.name.startswith(tuple(PREFIXES)):
            continue
        caps = {c.name: c.value for c in (sku.capabilities or [])}
        rows[sku.name] = {
            'InstanceType': sku.name,
            'vCPU_spec': pd.to_numeric(caps.get('vCPUs'), errors='coerce'),
            'RAM_GiB': pd.to_numeric(caps.get('MemoryGB'), errors='coerce'),
            'AcceleratorCount': pd.to_numeric(caps.get('GPUs'), errors='coerce'),
        }
    return pd.DataFrame(rows.values())


# ---------------- 4. Save ----------------
def append_to_csv(new_df, path):
    day = new_df['ExtractionDate'].iloc[0]
    if os.path.exists(path) and os.path.getsize(path) > 0:
        old = pd.read_csv(path)
        old['ExtractionDate'] = pd.to_datetime(old['ExtractionDate'], errors='coerce').dt.strftime('%Y-%m-%d')
        old = old.dropna(subset=['ExtractionDate'])
        old = old[old['ExtractionDate'] != day]       # same-day re-run replaces, not duplicates
        combined = pd.concat([old, new_df], ignore_index=True)
    else:
        combined = new_df
    combined.to_csv(path, index=False)
    print(f"Saved {len(new_df)} rows for {day} -> {path} ({len(combined)} rows total)")


if __name__ == '__main__':
    df = build_df(fetch_prices())

    specs = get_specs_from_azure()
    if specs is not None and not specs.empty:
        df = df.merge(specs, on='InstanceType', how='left')
        df['vCPU'] = df['vCPU_spec'].fillna(df['vCPU'])
        df = df.drop(columns=['vCPU_spec'])
        df['SpotPricePerAccelerator'] = df['SpotPrice'] / df['AcceleratorCount'].where(df['AcceleratorCount'] > 0)

    df['ExtractionDate'] = extracted_at.date().isoformat()
    df['ExtractedAt'] = extracted_at.strftime('%Y-%m-%d %H:%M:%S')

    AZURE_df = [df]
    append_to_csv(AZURE_df[0], OUT_CSV)
    print(AZURE_df[0].head().to_string())
