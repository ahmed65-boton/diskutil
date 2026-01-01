# rep.py — Windows Disk Repartition + Format Tool (DANGEROUS)

⚠️ **WARNING: THIS SCRIPT CAN ERASE A DISK COMPLETELY.**
Use at your own risk. Always double-check the disk number.

This is a Windows-only command-line tool that:
- **ERASES** a selected disk (`diskpart clean`)
- Optionally **converts** partition style **GPT/MBR** (only when needed)
- Creates **1–10 partitions**
- Assigns **drive letters** via PowerShell (**after** partition creation)
- Formats each partition as **exFAT** with **16 KB** allocation units (cluster size)
- Applies per-partition **volume labels**

## Why it uses DiskPart + PowerShell
DiskPart has a couple of common pitfalls on Windows:
- `assign letter=X` can fail on RAW/unformatted partitions (“There is no volume specified”)
- `convert gpt` fails if the disk is already GPT (“disk not MBR formatted”)

This script:
- uses **DiskPart** only for layout (`clean`, `convert` if needed, `create partition`)
- uses **PowerShell** for drive letters + formatting (`Set-Partition`, `Format-Volume`)

## Requirements
- Windows 10/11
- Python 3.8+
- Must run in an **elevated** terminal (Administrator)

## Usage

### Run
Open PowerShell/Windows Terminal **as Administrator**:

```powershell
py rep.py
