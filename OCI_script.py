"""
Oracle Cloud (OCI) GPU shapes - VM and bare metal (BM) - prices matched to hardware specs, one table.

  1. Prices   <- Oracle's public price-list API (no login needed), USD pay-as-you-go
  2. Hardware <- the "VM GPU Shapes" and "Bare Metal GPU Shapes" tables on Oracle's Compute Shapes docs page
                 (each falls back to a built-in copy if the page layout changes)
  3. Each shape is matched to its GPU price (OCI mostly bills GPU shapes per GPU per hour)
  4. Result (column Type = VM or BM) is appended to OCI.csv

Note: OCI uses one price for all commercial regions, so there is no per-region price.
"""
import os
import re
import time
import requests
import numpy as np
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime
from zoneinfo import ZoneInfo

# ---------------- Settings ----------------
target_folder = os.path.dirname(os.path.abspath(__file__))
os.chdir(target_folder)

OUT_CSV = 'OCI.csv'
PRICE_API = 'https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/'
DOCS_URL = 'https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}

extracted_at = datetime.now(ZoneInfo('Asia/Singapore'))

# Per shape family: GPU model, CPU (from the Compute Shapes docs page) and how to find its price.
#   parts   = price-list part numbers confirmed from Oracle documents (tried first)
#   name_re = fallback: regex on the product name (checked among GPU products only)
#   vcpu_per_ocpu = 2 for x86 (Oracle's definition); None for Arm (Grace) where it is not stated
X7 = 'Intel Xeon Platinum 8167M (2.0 GHz base, 2.4 GHz turbo)'
SPR = 'Intel Sapphire Rapids 8480+ 2 x 56-core'
SERIES_INFO = {
    'VM.GPU2':         dict(GPU='P100',          CPU=X7,                                                 parts=['B88518'], name_re=r'p100|gpu2'),
    'VM.GPU3':         dict(GPU='V100',          CPU=X7,                                                 parts=[],         name_re=r'v100|gpu3'),
    'VM.GPU.A10':      dict(GPU='A10',           CPU='Intel Xeon Platinum 8358 (2.6 GHz base, 3.4 GHz turbo)', parts=['B95909'], name_re=r'\ba10\b'),
    'BM.GPU2':         dict(GPU='P100',          CPU=X7,                                                 parts=['B88517'], name_re=r'p100|gpu2'),
    'BM.GPU3':         dict(GPU='V100',          CPU=X7,                                                 parts=[],         name_re=r'v100|gpu3'),
    'BM.GPU4':         dict(GPU='A100 40GB',     CPU='AMD EPYC 7542 (2.9 GHz base, 3.4 GHz boost)',      parts=['B92740'], name_re=r'\be3\b|a100(?!.*v2)'),
    'BM.GPU.A10':      dict(GPU='A10',           CPU='Intel Xeon Platinum 8358 (2.6 GHz base, 3.4 GHz turbo)', parts=['B95909'], name_re=r'\ba10\b'),
    'BM.GPU.A100':     dict(GPU='A100 80GB',     CPU='AMD EPYC 7J13 (2.55 GHz base, 3.7 GHz boost)',     parts=['B95907'], name_re=r'a100.*v2'),
    'BM.GPU.MI300X':   dict(GPU='MI300X',        CPU=SPR + ' (2.0 GHz base, 3.8 GHz boost)',             parts=[],         name_re=r'mi300'),
    'BM.GPU.MI355X':   dict(GPU='MI355X',        CPU='2 x 64-core 5th Gen AMD EPYC',                     parts=[],         name_re=r'mi355'),
    'BM.GPU.L40S':     dict(GPU='L40S',          CPU=SPR,                                                parts=[],         name_re=r'l40s'),
    'BM.GPU.RTXPRO':   dict(GPU='RTX PRO 6000',  CPU='2 x 72-core Intel Xeon 6',                         parts=[],         name_re=r'rtx\s*pro|rtxpro'),
    'BM.GPU.H100':     dict(GPU='H100',          CPU=SPR + ' (2.0 GHz base, 3.8 GHz boost)',             parts=[],         name_re=r'\bh100\b'),
    'BM.GPU.H200':     dict(GPU='H200',          CPU=SPR,                                                parts=[],         name_re=r'\bh200\b'),
    'BM.GPU.B200':     dict(GPU='B200',          CPU='Intel Emerald Rapids 8592+ 2 x 64-core',           parts=[],         name_re=r'\bb200\b'),
    'BM.GPU.B300':     dict(GPU='B300',          CPU='Intel Emerald Rapids 8592+ 2 x 64-core',           parts=[],         name_re=r'\bb300\b'),
    'BM.GPU.GB200':    dict(GPU='GB200',         CPU='2 x 72-core NVIDIA Grace',    vcpu_per_ocpu=None,  parts=[],         name_re=r'gb200'),
    'BM.GPU.GB300':    dict(GPU='GB300',         CPU='2 x 72-core NVIDIA Grace',    vcpu_per_ocpu=None,  parts=[],         name_re=r'gb300'),
}

# Built-in copy of Oracle's tables (docs page version of Aug 2026), used if scraping fails.
# shape, OCPU, GPU mem total (GB), CPU mem (GB), local disk, network lines, max VNICs Linux, max VNICs Windows, GPUs
FALLBACK_SPECS = [
    ('VM.GPU2.1',        12,   16,   72, 'Block storage only', ['8 Gbps'],                                        12,  12, 1),
    ('VM.GPU3.1',         6,   16,   90, 'Block storage only', ['4 Gbps'],                                         6,   6, 1),
    ('VM.GPU3.2',        12,   32,  180, 'Block storage only', ['8 Gbps'],                                        12,  12, 2),
    ('VM.GPU3.4',        24,   64,  360, 'Block storage only', ['24.6 Gbps'],                                     24,  24, 4),
    ('VM.GPU.A10.1',     15,   24,  240, 'Block storage only', ['24 Gbps'],                                       15,  15, 1),
    ('VM.GPU.A10.2',     30,   48,  480, 'Block storage only', ['48 Gbps'],                                       24,  24, 2),
    ('BM.GPU2.2',        28,   32,  192, 'Block storage only', ['2 x 25 Gbps'],                                   28,  15, 2),
    ('BM.GPU3.8',        52,  128,  768, 'Block storage only', ['2 x 25 Gbps'],                                   52,  27, 8),
    ('BM.GPU4.8',        64,  320, 2048, '27.2 TB NVMe SSD (4 drives)', ['1 x 50 Gbps', '8 x 200 Gbps RDMA'],     64, np.nan, 8),
    ('BM.GPU.A10.4',     64,   96, 1024, '7.68 TB NVMe SSD (2 drives)', ['2 x 50 Gbps'],                         256, np.nan, 4),
    ('BM.GPU.A100-v2.8', 128, 640, 2048, '27.2 TB NVMe SSD (4 drives)', ['2 x 50 Gbps', '16 x 100 Gbps RDMA'],   256, np.nan, 8),
    ('BM.GPU.MI300X.8',  112, 1536, 2048, '8 x 3.84 TB NVMe',  ['1 x 100 Gbps', '8 x 1 x 400 Gbps RDMA'],        256, np.nan, 8),
    ('BM.GPU.MI355X.8',  128,  288, 3072, '8 x 7.68 TB NVMe',  ['2 x 200 Gbps', '8 x 400 Gbps RDMA'],            256, np.nan, 8),
    ('BM.GPU.L40S.4',    112,  192, 1024, '2 x 3.84 TB NVMe',  ['1 x 200 Gbps', '800 Gbps RDMA'],                256, np.nan, 4),
    ('BM.GPU.RTXPRO.8',  144,  768, 3072, '8 x 7.68 TB NVMe',  ['2 x 200 Gbps', '8 x 400 Gbps RDMA'],            256, np.nan, 8),
    ('BM.GPU.H100.8',    112,  640, 2048, '16 x 3.84 TB NVMe', ['1 x 100 Gbps', '8 x 2 x 200 Gbps RDMA'],        256, np.nan, 8),
    ('BM.GPU.H200.8',    112, 1128, 3072, '8 x 3.84 TB NVMe',  ['1 x 200 Gbps', '8 x 400 Gbps RDMA'],            256, np.nan, 8),
    ('BM.GPU.B200.8',    128, 1440, 4096, '8 x 3.84 TB NVMe',  ['2 x 200 Gbps', '8 x 400 Gbps RDMA'],            256, np.nan, 8),
    ('BM.GPU.B300.8',    128, 2100, 4096, '8 x 3.84 TB NVMe',  ['2 x 200 Gbps', '8 x 800 Gbps RDMA'],            256, np.nan, 8),
    ('BM.GPU.GB200.4',   144,  768,  960, '4 x 7.68 TB NVMe',  ['2 x 200 Gbps', '4 x 400 Gbps RDMA'],            512, np.nan, 4),
    ('BM.GPU.GB300.4',   144, 1112,  960, '4 x 7.68 TB NVMe',  ['2 x 200 Gbps', '8 x 400 Gbps RDMA'],            512, np.nan, 4),
]


# ---------------- helpers ----------------
def txt(el):
    return ' '.join(el.get_text(' ').split()) if el else ''


def lines_of(el):
    """Cell text split at <br> into tidy lines."""
    return [' '.join(l.split()) for l in el.get_text('\n').split('\n') if l.strip()]


def num(s):
    m = re.search(r'\d[\d,]*\.?\d*', s or '')
    return float(m.group().replace(',', '')) if m else np.nan


def gpu_memory_total(s):
    """'640' -> 640 ; '8 x 96 GB' -> 768."""
    m = re.search(r'(\d+)\s*x\s*(\d+)', s or '')
    return float(m.group(1)) * float(m.group(2)) if m else num(s)


def gbps(line):
    """'2 x 25 Gbps' -> 50 ; '8 x 2 x 200 Gbps RDMA' -> 3200 ; '24.6 Gbps' -> 24.6"""
    head = line.split('Gbps')[0]
    nums = [float(n) for n in re.findall(r'\d+\.?\d*', head)]
    return float(np.prod(nums)) if nums else np.nan


def network_numbers(lines):
    front = [gbps(l) for l in lines if 'rdma' not in l.lower()]
    rdma = [gbps(l) for l in lines if 'rdma' in l.lower()]
    return (front[0] if front else np.nan), (sum(rdma) if rdma else np.nan)


def series_of(shape):
    """Longest matching family prefix (so BM.GPU.A100... is not mistaken for BM.GPU.A10)."""
    keys = [k for k in SERIES_INFO if shape.startswith(k)]
    return max(keys, key=len) if keys else None


def get(url, retries=4):
    for attempt in range(retries):
        r = requests.get(url, headers=HEADERS, timeout=60)
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f"{url} kept failing (last status {r.status_code})")


# ---------------- 1. Prices ----------------
def parse_catalog(data):
    """API JSON -> one row per product with its USD pay-as-you-go price."""
    rows = []
    for it in data.get('items', []):
        usd = next((c for c in it.get('currencyCodeLocalizations', []) if c.get('currencyCode') == 'USD'), None)
        if not usd:
            continue
        pay = [p for p in usd.get('prices', []) if p.get('model') == 'PAY_AS_YOU_GO' and not p.get('rangeMin')]
        if not pay:
            continue
        rows.append({
            'partNumber': it.get('partNumber'),
            'displayName': it.get('displayName', ''),
            'metricName': it.get('metricName', ''),
            'serviceCategory': it.get('serviceCategory', ''),
            'price': float(pay[0]['value']),
        })
    return pd.DataFrame(rows)


def gpu_items_of(catalog):
    """Products billed per GPU (excludes VMware bundles)."""
    return catalog[catalog['metricName'].str.contains('GPU', case=False)
                   & ~catalog['serviceCategory'].str.contains('VMware', case=False)]


def find_price(catalog, gpu_items, shape, info, gpus):
    """Returns (product row, basis, price per GPU-hour, price of whole shape per hour) or None."""
    hit = gpu_items[gpu_items['partNumber'].isin(info.get('parts', []))]
    if hit.empty and info.get('name_re'):
        hit = gpu_items[gpu_items['displayName'].str.contains(info['name_re'], case=False, regex=True)]
    if not hit.empty:
        if hit['price'].nunique() > 1:
            print(f"WARNING: several prices match {shape}: "
                  f"{hit[['partNumber', 'displayName', 'price']].to_dict('records')} - using the lowest")
        row = hit.sort_values('price').iloc[0]
        return row, 'per GPU hour', float(row['price']), float(row['price']) * gpus
    # fallback: a per-node product named after the shape
    node = catalog[catalog['displayName'].str.contains(re.escape(shape), case=False)
                   & ~catalog['serviceCategory'].str.contains('VMware', case=False)
                   & catalog['metricName'].str.contains('Hour', case=False)]
    if not node.empty:
        row = node.iloc[0]
        return row, 'per node hour', float(row['price']) / gpus, float(row['price'])
    return None


# ---------------- 2. Hardware specs ----------------
def parse_gpu_table(table):
    header = [txt(c) for c in table.find('tr').find_all(['th', 'td'])]

    def col(cells, prefix):
        for h, c in zip(header, cells):
            if h.lower().startswith(prefix.lower()):
                return c
        return None

    rows = []
    for tr in table.find_all('tr')[1:]:
        tds = tr.find_all(['td', 'th'])
        if len(tds) != len(header):
            continue
        first = txt(tds[0])
        m = re.match(r'((?:VM|BM)\.[\w.\-]+)', first)
        if not m:
            continue
        shape = m.group(1)
        g = re.search(r'(\d+)\s*x\s*[A-Za-z0-9]+', first)
        gpus = int(g.group(1)) if g else int(num(shape.split('.')[-1]))
        t = lambda p: txt(col(tds, p)) if col(tds, p) is not None else ''
        net_lines = lines_of(col(tds, 'Max Network')) if col(tds, 'Max Network') is not None else []
        rows.append({
            'Shape': shape, 'GPUs': gpus,
            'OCPU': num(t('OCPU')),
            'GPUMemory_GB': gpu_memory_total(t('GPU Memory')),
            'CPUMemory_GB': num(t('CPU Memory')),
            'LocalDisk': t('Local Disk'),
            'NetworkLines': net_lines,
            'MaxVNICs_Linux': num(t('Max VNICs Total: Linux')),
            'MaxVNICs_Windows': num(t('Max VNICs Total: Windows')),
        })
    return pd.DataFrame(rows)


def fallback_rows(prefix):
    return pd.DataFrame([{
        'Shape': s[0], 'OCPU': s[1], 'GPUMemory_GB': s[2], 'CPUMemory_GB': s[3], 'LocalDisk': s[4],
        'NetworkLines': s[5], 'MaxVNICs_Linux': s[6], 'MaxVNICs_Windows': s[7], 'GPUs': s[8],
    } for s in FALLBACK_SPECS if s[0].startswith(prefix)])


def fetch_specs():
    try:
        soup = BeautifulSoup(get(DOCS_URL).text, 'html.parser')
    except Exception as e:
        print(f"WARNING: could not read the docs page ({type(e).__name__}: {e})")
        soup = None
    parts = []
    for anchor, prefix in (('vm-gpu', 'VM.'), ('bm-gpu', 'BM.')):
        specs = pd.DataFrame()
        try:
            if soup is not None:
                specs = parse_gpu_table(soup.find(id=anchor).find_next('table'))
                specs = specs[specs['Shape'].str.startswith(prefix)]
        except Exception as e:
            print(f"WARNING: could not parse the {anchor} table ({type(e).__name__}: {e})")
        if specs.empty:
            print(f"Specs {prefix}: using the built-in copy of Oracle's table (Aug 2026)")
            specs = fallback_rows(prefix)
        else:
            print(f"Specs {prefix}: {len(specs)} GPU shapes read from the Oracle docs page")
        parts.append(specs)
    return pd.concat(parts, ignore_index=True)


# ---------------- 3. Match shapes to prices ----------------
def build_df(catalog, specs, api_last_updated):
    gpu_items = gpu_items_of(catalog)
    rows = []
    for s in specs.to_dict('records'):
        fam = series_of(s['Shape'])
        info = SERIES_INFO.get(fam, {})
        found = find_price(catalog, gpu_items, s['Shape'], info, s['GPUs']) if info else None
        row, basis, per_gpu, per_shape = found if found else (None, None, np.nan, np.nan)
        front, rdma = network_numbers(s['NetworkLines'])
        per_ocpu = info.get('vcpu_per_ocpu', 2) if info else 2
        rows.append({
            'Provider': 'OCI',
            'Type': 'BM' if s['Shape'].startswith('BM.') else 'VM',
            'Region': 'All commercial regions',
            'InstanceType': s['Shape'],
            'OS': 'Linux',
            'Accelerator': info.get('GPU'),
            'AcceleratorCount': s['GPUs'],
            'AcceleratorMemory_GiB': s['GPUMemory_GB'],
            'AcceleratorMemoryPerGPU_GiB': s['GPUMemory_GB'] / s['GPUs'],
            'OCPU': s['OCPU'],
            'vCPU': s['OCPU'] * per_ocpu if per_ocpu else np.nan,   # x86: 1 OCPU = 2 vCPUs (per Oracle)
            'RAM_GiB': s['CPUMemory_GB'],
            'CPU': info.get('CPU'),
            'LocalDisk': s['LocalDisk'],
            'NetworkBandwidth': ' | '.join(s['NetworkLines']),
            'NetworkBandwidth_Gbps': front,
            'RDMA_Gbps': rdma,
            'MaxVNICs_Linux': s['MaxVNICs_Linux'],
            'MaxVNICs_Windows': s['MaxVNICs_Windows'],
            'PriceBasis': basis,
            'GpuPartNumber': row['partNumber'] if row is not None else None,
            'GpuProductName': row['displayName'] if row is not None else None,
            'PricePerGPU_hr_USD': per_gpu,
            'OnDemandPrice': per_shape,                  # price of the whole shape per hour
            'PriceLastChanged': api_last_updated,        # when Oracle last updated its price list
        })
    return pd.DataFrame(rows)


# ---------------- 4. Save ----------------
def append_to_csv(new_df, path):
    day = new_df['ExtractionDate'].iloc[0]
    if os.path.exists(path) and os.path.getsize(path) > 0:
        old = pd.read_csv(path)
        old['ExtractionDate'] = pd.to_datetime(old['ExtractionDate'], errors='coerce').dt.strftime('%Y-%m-%d')
        old = old.dropna(subset=['ExtractionDate'])
        old = old[old['ExtractionDate'] != day]          # same-day re-run replaces, not duplicates
        combined = pd.concat([old, new_df], ignore_index=True)
    else:
        combined = new_df
    combined.to_csv(path, index=False)
    print(f"Saved {len(new_df)} rows for {day} -> {path} ({len(combined)} rows total)")


if __name__ == '__main__':
    data = get(PRICE_API).json()
    catalog = parse_catalog(data)
    print(f"Price API: {len(catalog)} products, last updated {data.get('lastUpdated')}")
    print("\nAll GPU products in the API (use this to check the matching):")
    print(gpu_items_of(catalog)[['partNumber', 'displayName', 'metricName', 'price']].to_string(index=False))

    specs = fetch_specs()
    df = build_df(catalog, specs, data.get('lastUpdated'))
    df['ExtractionDate'] = extracted_at.date().isoformat()
    df['ExtractedAt'] = extracted_at.strftime('%Y-%m-%d %H:%M:%S')

    unpriced = df[df['PricePerGPU_hr_USD'].isna()]['InstanceType'].tolist()
    if unpriced:
        print(f"\nWARNING: no price matched for {unpriced} - adjust name_re/parts in SERIES_INFO using the list above")

    OCI_df = [df]
    append_to_csv(OCI_df[0], OUT_CSV)
    print()
    print(OCI_df[0][['Type', 'InstanceType', 'Accelerator', 'AcceleratorCount', 'vCPU', 'RAM_GiB',
                     'PriceBasis', 'PricePerGPU_hr_USD', 'OnDemandPrice']].to_string(index=False))
