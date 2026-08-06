# lares-sense

mmWave radar (IWR6843AoP) point-cloud sensing over UART on a Raspberry Pi,
streamed over UDP. Radar code lives in [`radar_pi/`](radar_pi/README.md).

## Repository layout

```
lares-sense/
├── radar_pi/            radar Python tools (see radar_pi/README.md)
├── docs_and_tools/      vendor tools + datasheets — NOT in git (see below)
├── ESP32_CONNECTION.md
├── KNOWLEDGE_BASE.md
├── fetch_vendor.sh      idempotent vendor downloader (optional)
└── README.md
```

## Vendor tools & docs (`docs_and_tools/`)

`docs_and_tools/` (~1.8 GB) holds TI vendor binaries and datasheets. It is
**gitignored and not versioned** — intentionally, to keep the repo small.

### How to get it

Pick one:

1. **Scripted** (dev machines) — download everything idempotently:

   ```bash
   ./fetch_vendor.sh      # skips items already present
   ```

2. **Manual** — grab from TI and drop into `docs_and_tools/`:
   - TI Radar Toolbox 4.00.00.05
   - UniFlash (`uniflash_sl.<version>.run`)
   - CP210x VCP USB driver
   - Device datasheets / reference PDFs

   Source URLs: `docs_and_tools/USEFUL_LINKS.txt` and
   <https://www.ti.com/tool/RADAR-TOOLBOX>.
