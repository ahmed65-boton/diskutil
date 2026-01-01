#!/usr/bin/env python3
"""
DANGEROUS: This script ERASES a disk and recreates partitions (Windows only).

What it does:
- DiskPart:
  * select disk
  * clear readonly
  * (online if needed)
  * clean
  * convert GPT/MBR ONLY if needed (IMPORTANT)
  * create partitions (NO drive-letter assignment here)
- PowerShell:
  * Set-Partition -NewDriveLetter <letter>
  * Format-Volume exFAT with 16 KB clusters and chosen label

Why:
- DiskPart `assign letter=...` can fail on RAW/unformatted partitions ("There is no volume specified")
- DiskPart `convert gpt` fails if disk is already GPT ("not MBR formatted")
- So we:
  1) Only convert when needed
  2) Assign letters + format via PowerShell after partitions exist

Run in an elevated terminal (Administrator).
"""

import os
import re
import time
import math
import tempfile
import subprocess
from typing import List, Optional, Tuple, Dict, Any

ALLOC_UNIT_BYTES = 16 * 1024  # 16 KB fixed


# ----------------------------- Helpers ---------------------------------

def is_windows() -> bool:
    return os.name == "nt"


def run_cmd(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def is_admin() -> bool:
    """Best-effort admin check."""
    try:
        import ctypes  # type: ignore
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def ps_json(command: str) -> Optional[Any]:
    """Run a PowerShell command and parse JSON output (best-effort)."""
    try:
        ps = f"{command} | ConvertTo-Json -Depth 6"
        cp = run_cmd(["powershell", "-NoProfile", "-Command", ps])
        if cp.returncode != 0 or not cp.stdout.strip():
            return None
        import json
        return json.loads(cp.stdout)
    except Exception:
        return None


def normalize_style(style: str) -> str:
    s = (style or "").strip().lower()
    # Get-Disk returns: GPT / MBR / RAW
    if s in ("gpt", "mbr", "raw", "unknown"):
        return s
    if not s:
        return "unknown"
    return s


def bytes_to_gb_str(n_bytes: int, decimals: int = 2) -> str:
    gb = n_bytes / (1024 ** 3)  # 1GB in PowerShell = 1024^3 bytes
    return f"{gb:.{decimals}f} GB"


def prompt_int(prompt: str, min_value: int = 0, max_value: Optional[int] = None) -> int:
    while True:
        raw = input(prompt).strip()
        if not raw.isdigit():
            print("Please enter a whole number.")
            continue
        val = int(raw)
        if val < min_value:
            print(f"Must be >= {min_value}.")
            continue
        if max_value is not None and val > max_value:
            print(f"Must be <= {max_value}.")
            continue
        return val


def prompt_yes_no(prompt: str, default: bool = False) -> bool:
    d = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{prompt} [{d}]: ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("Type y or n.")


def prompt_partition_style() -> str:
    while True:
        raw = input("Partition style? Type MBR or GPT: ").strip().lower()
        if raw in ("mbr", "gpt"):
            return raw
        print("Please type exactly MBR or GPT.")


def ps_escape_single_quotes(s: str) -> str:
    return s.replace("'", "''")


# ----------------------------- Disk/Volume Info (PowerShell) -----------

def get_disks_table_text() -> str:
    ps = (
        "Get-Disk | "
        "Select-Object Number,FriendlyName,"
        "@{Name='Size';Expression={\"{0:N2} GB\" -f ($_.Size/1GB)}},"
        "PartitionStyle | "
        "Format-Table -AutoSize"
    )
    cp = run_cmd(["powershell", "-NoProfile", "-Command", ps])
    if cp.returncode == 0 and cp.stdout.strip():
        return cp.stdout
    return "(Could not list disks via PowerShell. Use Disk Management to verify.)"


def get_system_disk_number() -> Optional[int]:
    """Try to determine which disk contains the Windows OS volume."""
    sys_drive = os.environ.get("SystemDrive", "C:")
    letter = sys_drive.replace(":", "").strip()
    if not letter:
        return None
    cmd = f"(Get-Partition -DriveLetter {letter} | Select-Object -First 1 -ExpandProperty DiskNumber)"
    try:
        cp = run_cmd(["powershell", "-NoProfile", "-Command", cmd])
        if cp.returncode != 0:
            return None
        s = cp.stdout.strip()
        if not s:
            return None
        return int(s)
    except Exception:
        return None


def get_disk_info(disk_number: int) -> Optional[Dict[str, Any]]:
    data = ps_json(
        f"Get-Disk -Number {disk_number} | "
        "Select-Object Number,FriendlyName,Size,PartitionStyle,IsOffline,IsReadOnly"
    )
    if data is None:
        return None
    if isinstance(data, list):
        if not data:
            return None
        data = data[0]
    if not isinstance(data, dict):
        return None

    try:
        size_bytes = int(float(data.get("Size", 0)))
        return {
            "Number": int(data.get("Number")),
            "FriendlyName": str(data.get("FriendlyName", "")).strip(),
            "SizeBytes": size_bytes,
            "PartitionStyle": normalize_style(str(data.get("PartitionStyle", "")).strip()),
            "IsOffline": bool(data.get("IsOffline", False)),
            "IsReadOnly": bool(data.get("IsReadOnly", False)),
        }
    except Exception:
        return None


def get_disk_size_mib_ceiling(disk_number: int) -> Optional[int]:
    """Ceiling size in MiB used only for validation warnings."""
    info = get_disk_info(disk_number)
    if not info:
        return None
    size_bytes = int(info["SizeBytes"])
    mib = int(math.ceil(size_bytes / (1024 * 1024)))
    return max(1, mib)


def get_volume_info(letter: str) -> Optional[Dict[str, Any]]:
    """Return best-effort volume info for a drive letter."""
    letter = letter.strip().upper()
    if not re.fullmatch(r"[A-Z]", letter or ""):
        return None
    data = ps_json(
        f"Get-Volume -DriveLetter {letter} -ErrorAction SilentlyContinue | "
        "Select-Object DriveLetter,FileSystemLabel,FileSystem,Size,SizeRemaining,DriveType"
    )
    if data is None:
        return None
    if isinstance(data, list):
        if not data:
            return None
        data = data[0]
    if not isinstance(data, dict):
        return None
    return data


def wait_for_volume_letter(letter: str, timeout_seconds: float = 30.0) -> bool:
    """Wait until Get-Volume sees the letter."""
    letter = letter.upper()
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if get_volume_info(letter) is not None:
            return True
        time.sleep(0.5)
    return get_volume_info(letter) is not None


# ----------------------------- Prompts ---------------------------------

def prompt_drive_letter(i: int, used: List[str]) -> str:
    while True:
        raw = input(f"What drive letter for partition {i}? (A-Z): ").strip().upper()
        if not re.fullmatch(r"[A-Z]", raw or ""):
            print("Please type exactly ONE letter A-Z.")
            continue
        if raw in used:
            print("You already used that letter. Choose a different one.")
            continue
        if raw == "C":
            print("C: is the OS drive on almost all systems. Choose a different letter.")
            continue

        vol = get_volume_info(raw)
        if vol is not None:
            label = str(vol.get("FileSystemLabel") or "").strip()
            fs = str(vol.get("FileSystem") or "").strip()
            size = vol.get("Size")
            size_str = ""
            try:
                if size is not None:
                    size_str = bytes_to_gb_str(int(float(size)))
            except Exception:
                pass
            print(f"{raw}: is already in use"
                  f"{' (Label: ' + label + ')' if label else ''}"
                  f"{' (FS: ' + fs + ')' if fs else ''}"
                  f"{' (Size: ' + size_str + ')' if size_str else ''}.")
            print("For safety, this script requires an UNUSED drive letter. Choose another.\n")
            continue

        return raw


def prompt_label(i: int) -> str:
    while True:
        raw = input(f"What volume label for partition {i}? ").strip()
        if not raw:
            print("Label can't be empty.")
            continue
        return raw


def prompt_size_gb(i: int, is_last: bool) -> Optional[int]:
    """Return size in MiB; last partition may be 0 => remaining."""
    while True:
        raw = input(
            f"What size for partition {i} in GB? "
            f"{'(enter 0 to use remaining space)' if is_last else ''}: "
        ).strip()
        try:
            gb = float(raw)
            if gb < 0:
                print("Size must be >= 0.")
                continue
            if gb == 0:
                if is_last:
                    return None
                print("0 is only allowed for the LAST partition.")
                continue

            # DiskPart size= uses MiB; interpret input GB as GiB (1024 MiB)
            mib = int(round(gb * 1024))
            if mib < 1:
                print("That is too small.")
                continue
            return mib
        except ValueError:
            print("Please enter a number (examples: 20 | 20.5 | 0 for remaining on last).")


def validate_sizes(disk_mib: Optional[int], sizes_mib: List[Optional[int]]) -> bool:
    """If disk size is known, ensure sum of specified sizes <= disk size (with slack)."""
    if disk_mib is None:
        return True
    specified = sum(s for s in sizes_mib if s is not None)

    slack_mib = 32  # small slack for rounding/alignment
    if specified > disk_mib + slack_mib:
        print(f"\nWARNING: Your specified sizes total {specified} MiB, but disk is ~{disk_mib} MiB.")
        print("This will likely fail. Please re-enter sizes.\n")
        return False
    return True


# ----------------------------- DiskPart + PowerShell actions ------------

def diskpart_conversion_lines(current_style: str, target_style: str) -> List[str]:
    """
    IMPORTANT:
    - `convert gpt` requires an EMPTY MBR disk (or RAW).
    - `convert mbr` requires an EMPTY GPT disk (or RAW).
    - If it's already the target style, do NOT run convert (it errors like you saw).
    """
    cur = normalize_style(current_style)
    tgt = normalize_style(target_style)

    if tgt not in ("gpt", "mbr"):
        raise ValueError("target_style must be 'gpt' or 'mbr'")

    if cur == tgt:
        return []  # skip convert (prevents your error)

    # If RAW/unknown or opposite style, convert to target
    return [f"convert {tgt}"]


def build_diskpart_script(
    disk_number: int,
    disk_state: Dict[str, Any],
    target_style: str,
    partitions: List[Tuple[Optional[int], str, str]],
) -> Tuple[str, bool]:
    """
    DiskPart will:
    - clean
    - convert (ONLY if needed)
    - create partitions ONLY (no assign here)
    Drive letters + formatting happen in PowerShell.
    """
    lines: List[str] = []
    lines.append(f"select disk {disk_number}")
    lines.append("attributes disk clear readonly")
    if bool(disk_state.get("IsOffline", False)):
        lines.append("online disk")

    lines.append("clean")

    conv = diskpart_conversion_lines(str(disk_state.get("PartitionStyle", "unknown")), target_style)
    did_convert = len(conv) > 0
    lines.extend(conv)

    for (size_mib, _letter, _label) in partitions:
        if size_mib is None:
            lines.append("create partition primary")
        else:
            lines.append(f"create partition primary size={size_mib}")

    lines.append("rescan")
    return "\n".join(lines) + "\n", did_convert


def set_partition_letter_powershell(disk_number: int, partition_number: int, letter: str) -> subprocess.CompletedProcess:
    letter = letter.upper()
    ps = (
        "$ErrorActionPreference='Stop'; "
        f"Set-Partition -DiskNumber {disk_number} -PartitionNumber {partition_number} "
        f"-NewDriveLetter {letter}"
    )
    return run_cmd(["powershell", "-NoProfile", "-Command", ps])


def format_partition_powershell(disk_number: int, partition_number: int, label: str) -> subprocess.CompletedProcess:
    safe_label = ps_escape_single_quotes(label)
    ps = (
        "$ErrorActionPreference='Stop'; "
        f"Get-Partition -DiskNumber {disk_number} -PartitionNumber {partition_number} | "
        f"Format-Volume -FileSystem exFAT -AllocationUnitSize {ALLOC_UNIT_BYTES} "
        f"-NewFileSystemLabel '{safe_label}' -Confirm:$false -Force"
    )
    return run_cmd(["powershell", "-NoProfile", "-Command", ps])


# ----------------------------- Main ------------------------------------

def main() -> None:
    if not is_windows():
        print("This script only works on Windows (DiskPart required).")
        return

    if not is_admin():
        print("WARNING: You are NOT running as Administrator. DiskPart/PowerShell will likely fail.")
        print("Right-click PowerShell/Terminal and choose 'Run as administrator'.\n")

    print("=" * 72)
    print("WARNING: THIS WILL ERASE A DISK COMPLETELY.")
    print("Make sure you select the correct disk number.")
    print("=" * 72)

    print("\nAvailable disks (best-effort):\n")
    print(get_disks_table_text())

    disk_number = prompt_int("\nEnter disk number to ERASE (e.g., 1): ", min_value=0)

    os_disk = get_system_disk_number()
    if os_disk is not None and disk_number == os_disk:
        print(f"\nSAFETY BLOCK: Disk {disk_number} appears to be your OS disk (SystemDrive).")
        if not prompt_yes_no("Do you REALLY want to continue anyway?", default=False):
            print("Cancelled.")
            return
        override = input('Type EXACTLY "I UNDERSTAND" to override the OS disk block: ').strip()
        if override != "I UNDERSTAND":
            print("Cancelled.")
            return

    disk_state = get_disk_info(disk_number) or {
        "PartitionStyle": "unknown",
        "IsOffline": False,
        "IsReadOnly": False,
        "SizeBytes": 0,
        "FriendlyName": "",
        "Number": disk_number,
    }

    print("\nSelected disk details:")
    print(f"  Disk Number : {disk_state.get('Number', disk_number)}")
    print(f"  Name        : {disk_state.get('FriendlyName') or '(unknown)'}")
    if int(disk_state.get("SizeBytes", 0)) > 0:
        print(f"  Size        : {bytes_to_gb_str(int(disk_state.get('SizeBytes', 0)))}")
    print(f"  Current PS  : {str(disk_state.get('PartitionStyle', 'unknown')).upper()}")
    print(f"  Offline     : {bool(disk_state.get('IsOffline', False))}")
    print(f"  Read-only   : {bool(disk_state.get('IsReadOnly', False))}")

    if not prompt_yes_no("Is this the correct disk to ERASE?", default=False):
        print("Cancelled.")
        return

    n_parts = prompt_int("How many partitions do you want? (1-10): ", min_value=1, max_value=10)
    target_style = prompt_partition_style()

    disk_mib = get_disk_size_mib_ceiling(disk_number)

    # Collect per-partition config (repeat until sizes validate)
    while True:
        used_letters: List[str] = []
        sizes_mib: List[Optional[int]] = []
        letters: List[str] = []
        labels: List[str] = []

        print("\nNow entering per-partition details...")
        for i in range(1, n_parts + 1):
            letter = prompt_drive_letter(i, used_letters)
            used_letters.append(letter)

            label = prompt_label(i)
            size_mib = prompt_size_gb(i, is_last=(i == n_parts))

            letters.append(letter)
            labels.append(label)
            sizes_mib.append(size_mib)

        if validate_sizes(disk_mib, sizes_mib):
            break

    partitions = list(zip(sizes_mib, letters, labels))
    dp_script, did_convert = build_diskpart_script(disk_number, disk_state, target_style, partitions)

    print("\nDiskPart script that will run (CLEAN + optional CONVERT + CREATE partitions ONLY):")
    print("-" * 72)
    print(dp_script.strip())
    print("-" * 72)
    if did_convert:
        print(f"Note: Conversion WILL run to reach {target_style.upper()}.\n")
    else:
        print(f"Note: Disk already matches target style ({target_style.upper()}); conversion is skipped.\n")

    print("Drive letters will be assigned via PowerShell Set-Partition.")
    print("Formatting will be done via PowerShell Format-Volume: exFAT, 16 KB clusters.\n")

    confirm = input(f'Type EXACTLY "ERASE DISK {disk_number}" to proceed, or anything else to cancel: ').strip()
    if confirm != f"ERASE DISK {disk_number}":
        print("Cancelled.")
        return

    # Run DiskPart
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as f:
        f.write(dp_script)
        dp_path = f.name

    try:
        cp = run_cmd(["diskpart", "/s", dp_path])
        print("\nDiskPart output:\n")
        print(cp.stdout)
        if cp.stderr.strip():
            print("\nDiskPart errors (if any):\n")
            print(cp.stderr)
        if cp.returncode != 0:
            print(f"\nDiskPart returned exit code {cp.returncode}. Aborting.")
            return
    finally:
        try:
            os.remove(dp_path)
        except Exception:
            pass

    # Assign letters + format each partition
    print("\nAssigning drive letters + formatting partitions (PowerShell):")
    for part_num, (letter, label) in enumerate(zip(letters, labels), start=1):
        letter_u = letter.upper()

        print(f"  - Assigning {letter_u}: to partition {part_num} ...")
        ap = set_partition_letter_powershell(disk_number, part_num, letter_u)
        if ap.returncode != 0:
            print(f"    FAILED to assign letter {letter_u} to partition {part_num}:")
            if ap.stdout.strip():
                print(ap.stdout)
            if ap.stderr.strip():
                print(ap.stderr)
            print("    Aborting.")
            return

        if not wait_for_volume_letter(letter_u, timeout_seconds=30.0):
            print(f"    ERROR: {letter_u}: did not appear after assignment.")
            print("    Aborting.")
            return

        print(f"  - Formatting partition {part_num} ({letter_u}:) as exFAT, 16KB clusters, label '{label}' ...")
        fp = format_partition_powershell(disk_number, part_num, label)
        if fp.returncode == 0:
            print(f"    OK: {letter_u}: formatted.")
        else:
            print(f"    FAILED formatting partition {part_num} ({letter_u}:):")
            if fp.stdout.strip():
                print(fp.stdout)
            if fp.stderr.strip():
                print(fp.stderr)
            print("    Aborting remaining formats.")
            return

    print("\nAll done.")
    print("If Windows Explorer doesn't refresh immediately, unplug/replug the USB drive.")


if __name__ == "__main__":
    main()
