import boto3
import pandas as pd
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import os

# ---------------- Settings ----------------
INSTANCE_FILTERS = ['g*', 'p*', 'inf*', 'trn*', 'vt*']
SPEC_REGION = 'us-east-1'   # region used to list regions and pull specs
DAYS_BACK = 0               # 0 = current price only; up to 90 = price history
 
# Credentials: do NOT hardcode them. Run `aws configure` once, or set the
# AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY environment variables.
session = boto3.Session(
    aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
    aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY'],
)
 
# ---------------- 1. Regions ----------------
def get_regions():
    ec2 = session.client('ec2', region_name=SPEC_REGION)
    return sorted(r['RegionName'] for r in ec2.describe_regions()['Regions'])
 
 
# ---------------- 2. Instance specs (vCPU, RAM, GPU, network) ----------------
def get_instance_specs(region=SPEC_REGION):
    ec2 = session.client('ec2', region_name=region)
    paginator = ec2.get_paginator('describe_instance_types')
    rows = []
    for page in paginator.paginate(
        Filters=[{'Name': 'instance-type', 'Values': INSTANCE_FILTERS}]
    ):
        for it in page['InstanceTypes']:
            gpu_info = it.get('GpuInfo', {})
            gpus = gpu_info.get('Gpus', [])
            infer = it.get('InferenceAcceleratorInfo', {}).get('Accelerators', [])
            neuron = it.get('NeuronInfo', {}).get('NeuronDevices', [])
 
            # Work out accelerator type/count (GPU, Inferentia or Trainium)
            if gpus:
                accel_name = f"{gpus[0].get('Manufacturer', '')} {gpus[0].get('Name', '')}".strip()
                accel_count = sum(g.get('Count', 0) for g in gpus)
                accel_mem_gib = gpu_info.get('TotalGpuMemoryInMiB', 0) / 1024
            elif neuron:
                accel_name = neuron[0].get('Name')
                accel_count = sum(n.get('Count', 0) for n in neuron)
                accel_mem_gib = it.get('NeuronInfo', {}).get('TotalNeuronDeviceMemoryInMiB', 0) / 1024
            elif infer:
                accel_name = infer[0].get('Name')
                accel_count = sum(a.get('Count', 0) for a in infer)
                accel_mem_gib = sum(
                    a.get('MemoryInfo', {}).get('SizeInMiB', 0) * a.get('Count', 0) for a in infer
                ) / 1024
            else:
                accel_name, accel_count, accel_mem_gib = None, 0, 0
 
            rows.append({
                'InstanceType': it['InstanceType'],
                'vCPU': it['VCpuInfo']['DefaultVCpus'],
                'RAM_GiB': it['MemoryInfo']['SizeInMiB'] / 1024,
                'Accelerator': accel_name,
                'AcceleratorCount': accel_count,
                'AcceleratorMemory_GiB': accel_mem_gib,
                'NetworkPerformance': it['NetworkInfo']['NetworkPerformance'],
                'MaxNetworkCards': it['NetworkInfo'].get('MaximumNetworkCards'),
                'InstanceStorage_GB': it.get('InstanceStorageInfo', {}).get('TotalSizeInGB', 0),
                'CPUArch': ','.join(it['ProcessorInfo']['SupportedArchitectures']),
            })
    return pd.DataFrame(rows)
 
 
# ---------------- 3. Spot prices across regions ----------------
def get_spot_prices(region):
    ec2 = session.client('ec2', region_name=region)
    start = datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)
    paginator = ec2.get_paginator('describe_spot_price_history')
    rows = []
    for page in paginator.paginate(
        Filters=[{'Name': 'instance-type', 'Values': INSTANCE_FILTERS}],
        ProductDescriptions=['Linux/UNIX', 'Windows'],
        StartTime=start,
    ):
        for item in page['SpotPriceHistory']:
            rows.append({
                'Region': region,
                'InstanceType': item['InstanceType'],
                'OS': 'Linux' if item['ProductDescription'] == 'Linux/UNIX' else 'Windows',
                'AvailabilityZone': item['AvailabilityZone'],
                'SpotPrice': float(item['SpotPrice']),
                'Timestamp': item['Timestamp'],
            })
    return rows
 
 
regions = get_regions()
all_rows = []
extracted_at = datetime.now(ZoneInfo('Asia/Singapore'))
for region in regions:
    try:
        rows = get_spot_prices(region)
        all_rows.extend(rows)
        print(f"{region}: {len(rows)} records")
    except Exception as e:
        print(f"{region}: skipped ({type(e).__name__}: {e})")
 
history_df = pd.DataFrame(all_rows)
 
# Latest price per Region / Zone / OS / Instance Type
snapshot_df = (
    history_df.sort_values('Timestamp', ascending=False)
    .drop_duplicates(subset=['Region', 'InstanceType', 'OS', 'AvailabilityZone'])
    .reset_index(drop=True)
)
 
# ---------------- 4. Merge with specs ----------------
specs_df = get_instance_specs()
df = snapshot_df.merge(specs_df, on='InstanceType', how='left')
df['ExtractionDate'] = extracted_at.date().isoformat()
df['ExtractedAt'] = extracted_at.strftime('%Y-%m-%d %H:%M:%S')
df = df.rename(columns={'Timestamp': 'PriceLastChanged'})
 
# Normalised price per accelerator-hour (useful for comparing across chips)
df['SpotPricePerAccelerator'] = df['SpotPrice'] / df['AcceleratorCount'].where(df['AcceleratorCount'] > 0)

AWS_df = [df] 
 
# ---------------- 5. Append to CSV ----------------
AMZN_csv = 'AMZN.csv'

def append_to_csv(new_df, path):
    day = new_df['ExtractionDate'].iloc[0]
    if os.path.exists(path) and os.path.getsize(path) > 0:
        old = pd.read_csv(path)
        old['ExtractionDate'] = pd.to_datetime(old['ExtractionDate'], errors='coerce').dt.strftime('%Y-%m-%d')
        old = old.dropna(subset=['ExtractionDate'])
        old = old[old['ExtractionDate'] != day]   # same-day re-run replaces, not duplicates
        combined = pd.concat([old, new_df], ignore_index=True)
    else:
        combined = new_df
    combined.to_csv(path, index=False)
    print(f"Saved {len(new_df)} rows for {day} -> {path} ({len(combined)} rows total)")

append_to_csv(AWS_df[0], AMZN_csv)
print(AWS_df[0].head())
