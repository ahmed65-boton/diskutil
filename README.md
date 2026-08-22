
# Diskcraft (`diskcraft.py`)

**A robust, cross-platform CLI tool for wiping, repartitioning, and formatting disks to exFAT across Windows and Linux.**

> ⚠️ **DANGEROUS / DATA LOSS WARNING:**
> Diskcraft **PERMANENTLY ERASES** all partitions, partition tables, and file signatures on the targeted drive. Double-check your disk selection before proceeding.

---

## Features

* **Cross-Platform Execution:** Dynamic runtime support for both **Windows** and **Linux**.
* **Modern PowerShell Backend (Windows):** Bypasses legacy, error-prone `diskpart` scripts by utilizing direct PowerShell storage cmdlets (`Clear-Disk`, `Initialize-Disk`, `New-Partition`, `Format-Volume`).
* **Linux Native Tooling:** Wipes drive signatures and layouts using standard Linux utilities (`wipefs`, `parted`, `udevadm`, `mkfs.exfat`).
* **16 KB exFAT Cluster Allocation:** Enforces 16 KB allocation unit sizes by default to optimize flash drive performance and read/write durability across multi-device environments.
* **Safety & System Guards:**
* Auto-detects OS installation drives (`C:` or `/`) and enforces explicit confirmation overrides before allowing disk wipe actions.
* Verifies non-assigned drive letters (Windows) to prevent drive letter conflicts.
* Runs dependency sanity checks on launch to ensure missing system tools do not fail mid-operation.



---

## Prerequisites & Dependencies

### Windows

* **OS:** Windows 10/11 or Windows Server.
* **Python:** Python 3.8 or higher.
* **Privileges:** Elevated terminal (**Run as Administrator**).

### Linux

* **Python:** Python 3.8 or higher.
* **Privileges:** `root` or `sudo` rights.
* **Required Binaries:** `lsblk`, `parted`, `wipefs`, `udevadm`, `mkfs.exfat` (from `exfatprogs` or `exfat-utils`).

If dependencies are missing on Linux, install them via your distribution's package manager:

```bash
# Ubuntu / Debian
sudo apt update && sudo apt install parted util-linux exfatprogs

# Fedora / RHEL
sudo dnf install parted util-linux exfatprogs

# Arch Linux
sudo pacman -S parted util-linux exfatprogs

# openSUSE
sudo zypper install parted util-linux exfatprogs

```

---

## Usage

### 1. Launch the Script

**Windows (Administrator PowerShell / CMD):**

```powershell
python diskcraft.py

```

**Linux (Terminal with Sudo):**

```bash
sudo python3 diskcraft.py

```

### 2. Interactive Prompt Workflow

1. **Select OS Mode:** Choose `[1] Windows` or `[2] Linux` (auto-detects host OS by default).
2. **Select Target Disk:** View the attached disks table and input the corresponding Disk ID:
* **Windows Example:** `1` or `2`
* **Linux Example:** `/dev/sdb` or `/dev/nvme1n1`


3. **Partition Configuration:**
* Select Partition Style: **MBR** or **GPT**.
* Set total partition count (1–10).
* Specify volume labels and drive letters (Windows).
* Assign sizing in **GB** per partition (the final partition can automatically consume all remaining disk space).


4. **Final Confirmation:** Type `ERASE <Disk_ID>` when prompted to execute disk wiping and formatting.

---

## Technical Architecture Overview

| Operation | Windows Backend | Linux Backend |
| --- | --- | --- |
| **Disk Detection** | `Get-Disk` via PowerShell JSON | `lsblk` |
| **Drive Erasure** | `Clear-Disk -RemoveData -RemoveOEM` | `wipefs -a <dev>` |
| **Table Initialization** | `Initialize-Disk -PartitionStyle` | `parted mklabel` |
| **Partition Creation** | `New-Partition` | `parted mkpart` |
| **exFAT Formatting** | `Format-Volume -AllocationUnitSize 16384` | `mkfs.exfat -s 32` |

also dosnt use import os.py as its no longer a supported version

