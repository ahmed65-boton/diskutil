#!/usr/bin/env python3
"""
diskcraft.py - Cross-platform disk wiping, repartitioning, and exFAT formatting utility.
Supports Windows (PowerShell) and Linux (parted/mkfs.exfat).
"""

import sys
import os
import re
import math
import json
import shutil
import subprocess
from typing import List, Optional, Dict, Any, Tuple


# ----------------------------- Mode & Spec Selection -----------------------------

def select_target_os() -> str:
    """Prompt the user to choose target operating system mode."""
    detected = "Windows" if os.name == "nt" else "Linux"
    
    print("=" * 70)
    print(" DISKCRAFT: Disk Partitioning & exFAT Formatting Utility")
    print("=" * 70)
    print("\nSelect Operating System Mode:")
    print(" [1] Windows")
    print(" [2] Linux")
    
    while True:
        choice = input(f"\nType 1 for Windows or 2 for Linux (default: {detected}): ").strip()
        if not choice:
            return detected
        if choice in ("1", "windows", "win"):
            return "Windows"
        if choice in ("2", "linux"):
            return "Linux"
        print("Invalid selection. Please type '1' for Windows or '2' for Linux.")


def prompt_cluster_size() -> int:
    """Prompt user to choose exFAT Allocation Unit Size (cluster size in bytes)."""
    print("\nSelect exFAT Allocation Unit (Cluster Size):")
    print(" [1] 16 KB   - Maximum compatibility (Dashcams, Cameras, Consoles, Embedded Devices)")
    print(" [2] 32 KB   - Standard default for small/medium drives (< 32 GB)")
    print(" [3] 128 KB  - Recommended for standard flash drives & external SSDs (Default)")
    print(" [4] 512 KB  - Optimized for large media / 4K video files")
    print(" [5] 1024 KB - Maximum performance for massive file transfers / backups")

    mapping = {
        "1": 16 * 1024,
        "2": 32 * 1024,
        "3": 128 * 1024,
        "4": 512 * 1024,
        "5": 1024 * 1024,
    }

    while True:
        choice = input("\nSelect cluster size [1-5] (default: 3 [128 KB]): ").strip()
        if not choice:
            return 128 * 1024  # 128 KB default
        if choice in mapping:
            return mapping[choice]
        print("Invalid selection. Choose a option from 1 to 5.")


# ----------------------------- System Helpers -----------------------------

def run_cmd(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)

def is_root_or_admin(target_os: str) -> bool:
    if target_os == "Windows":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    else:
        return os.geteuid() == 0

def bytes_to_gb_str(n_bytes: int, decimals: int = 2) -> str:
    gb = n_bytes / (1024 ** 3)
    return f"{gb:.{decimals}f} GB"


# ----------------------------- Input Prompts -----------------------------

def prompt_int(prompt: str, min_value: int = 0, max_value: Optional[int] = None) -> int:
    while True:
        raw = input(prompt).strip()
        if not raw.isdigit():
            print("Please enter a valid whole number.")
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
        print("Type 'y' or 'n'.")

def prompt_partition_style() -> str:
    while True:
        raw = input("Partition style (MBR/GPT): ").strip().lower()
        if raw in ("mbr", "gpt"):
            return raw
        print("Type 'MBR' or 'GPT'.")


# ----------------------------- Windows Backend -----------------------------

class WindowsBackend:
    @staticmethod
    def check_dependencies() -> None:
        if not shutil.which("powershell"):
            print("ERROR: PowerShell is required on Windows systems but was not found in PATH.")
            sys.exit(1)

    @staticmethod
    def get_disks() -> List[Dict[str, Any]]:
        ps = "Get-Disk | Select-Object Number, FriendlyName, Size, PartitionStyle | ConvertTo-Json"
        cp = run_cmd(["powershell", "-NoProfile", "-Command", ps])
        if cp.returncode != 0 or not cp.stdout.strip():
            return []
        data = json.loads(cp.stdout)
        if isinstance(data, dict):
            data = [data]
        return [
            {
                "id": str(d["Number"]),
                "name": d.get("FriendlyName", "Unknown"),
                "size_bytes": int(d.get("Size", 0)),
                "style": str(d.get("PartitionStyle", "unknown")).lower()
            }
            for d in data
        ]

    @staticmethod
    def is_system_disk(target_id: str) -> bool:
        sys_drive = os.environ.get("SystemDrive", "C:").replace(":", "").strip()
        cmd = f"(Get-Partition -DriveLetter {sys_drive} | Select-Object -First 1 -ExpandProperty DiskNumber)"
        cp = run_cmd(["powershell", "-NoProfile", "-Command", cmd])
        return cp.returncode == 0 and cp.stdout.strip() == target_id

    @staticmethod
    def wipe_and_partition(disk_id: str, style: str, partitions: List[Tuple[Optional[int], str, str]], alloc_unit_bytes: int) -> None:
        style_param = "GPT" if style.lower() == "gpt" else "MBR"
        
        # Clear disk and set style
        ps_prep = f"Clear-Disk -Number {disk_id} -RemoveData -RemoveOEM -Confirm:$false; Initialize-Disk -Number {disk_id} -PartitionStyle {style_param}"
        cp = run_cmd(["powershell", "-NoProfile", "-Command", ps_prep])
        if cp.returncode != 0:
            raise RuntimeError(f"Disk initialization failed:\n{cp.stderr}")

        # Create partitions and format with custom cluster size
        for size_mib, letter, label in partitions:
            size_arg = f"-Size {size_mib}MB" if size_mib else "-UseMaximumSize"
            letter_arg = f"-DriveLetter {letter}" if letter else ""
            
            ps_part = (
                f"New-Partition -DiskNumber {disk_id} {size_arg} {letter_arg} | "
                f"Format-Volume -FileSystem exFAT -AllocationUnitSize {alloc_unit_bytes} "
                f"-NewFileSystemLabel '{label}' -Confirm:$false"
            )
            cp = run_cmd(["powershell", "-NoProfile", "-Command", ps_part])
            if cp.returncode != 0:
                raise RuntimeError(f"Failed to create partition:\n{cp.stderr}")


# ----------------------------- Linux Backend -----------------------------

class LinuxBackend:
    REQUIRED_TOOLS = ["lsblk", "parted", "wipefs", "udevadm", "mkfs.exfat"]

    @classmethod
    def check_dependencies(cls) -> None:
        missing = [tool for tool in cls.REQUIRED_TOOLS if shutil.which(tool) is None]
        if missing:
            print("\nERROR: Missing required system utilities for Linux mode:")
            for tool in missing:
                print(f" - {tool}")
            print("\nPlease install missing package(s):")
            print("  Ubuntu/Debian:  sudo apt install parted util-linux exfatprogs")
            print("  Fedora/RHEL:    sudo dnf install parted util-linux exfatprogs")
            print("  Arch Linux:     sudo pacman -S parted util-linux exfatprogs")
            sys.exit(1)

    @staticmethod
    def get_disks() -> List[Dict[str, Any]]:
        cp = run_cmd(["lsblk", "-dno", "NAME,MODEL,SIZE,BYTES,PTTYPE", "-b", "-J"])
        if cp.returncode != 0 or not cp.stdout.strip():
            return []
        data = json.loads(cp.stdout).get("blockdevices", [])
        return [
            {
                "id": f"/dev/{d['name']}",
                "name": d.get("model", "Unknown") or "Unknown",
                "size_bytes": int(d.get("bytes", 0)),
                "style": str(d.get("pttype", "unknown")).lower()
            }
            for d in data if not d["name"].startswith("loop")
        ]

    @staticmethod
    def is_system_disk(target_id: str) -> bool:
        cp = run_cmd(["findmnt", "-n", "-o", "SOURCE", "/"])
        root_dev = cp.stdout.strip()
        return root_dev.startswith(target_id)

    @staticmethod
    def wipe_and_partition(disk_id: str, style: str, partitions: List[Tuple[Optional[int], str, str]], alloc_unit_bytes: int) -> None:
        # Calculate sectors per cluster for Linux mkfs.exfat (-s flag assumes 512-byte sector size)
        sectors_per_cluster = max(1, alloc_unit_bytes // 512)

        # Wipe signatures
        run_cmd(["wipefs", "-a", disk_id])

        # Create label
        label_type = "gpt" if style.lower() == "gpt" else "msdos"
        run_cmd(["parted", "-s", disk_id, "mklabel", label_type])

        # Layout partitions
        start_mib = 1
        disk_size_bytes = int(run_cmd(["blockdev", "--getsize64", disk_id]).stdout.strip() or 0)
        disk_size_mib = disk_size_bytes // (1024 * 1024)

        created_parts = []
        for idx, (size_mib, _, label) in enumerate(partitions, start=1):
            if size_mib is None or (start_mib + size_mib > disk_size_mib):
                end_str = "100%"
            else:
                end_str = f"{start_mib + size_mib}MiB"

            part_type = "primary"
            run_cmd(["parted", "-s", disk_id, "mkpart", part_type, "exfat", f"{start_mib}MiB", end_str])
            
            part_node = f"{disk_id}p{idx}" if any(c.isdigit() for c in disk_id[-1]) else f"{disk_id}{idx}"
            created_parts.append((part_node, label))
            
            if size_mib:
                start_mib += size_mib

        # Sync dev nodes
        run_cmd(["udevadm", "settle"])

        # Format partitions with chosen sector ratio
        for part_node, label in created_parts:
            cmd = ["mkfs.exfat", "-s", str(sectors_per_cluster), "-n", label, part_node]
            cp = run_cmd(cmd)
            if cp.returncode != 0:
                raise RuntimeError(f"Failed to format {part_node}:\n{cp.stderr}")


# ----------------------------- Main Program Loop -----------------------------

def main() -> None:
    target_os = select_target_os()
    backend = WindowsBackend() if target_os == "Windows" else LinuxBackend()

    print(f"\n[Running in {target_os} Mode]")
    backend.check_dependencies()

    if not is_root_or_admin(target_os):
        print("WARNING: Insufficient privileges. Run as Administrator (Windows) or root/sudo (Linux).")
        sys.exit(1)

    disks = backend.get_disks()
    if not disks:
        print("No configurable disks identified.")
        return

    print("\nAvailable Disks:\n")
    for d in disks:
        print(f" [{d['id']}] {d['name']} - {bytes_to_gb_str(d['size_bytes'])} ({d['style'].upper()})")

    example_id = "1" if target_os == "Windows" else "/dev/sdb"
    target_id = input(f"\nEnter Target Disk ID (e.g., '{example_id}'): ").strip()
    selected_disk = next((d for d in disks if d["id"] == target_id), None)

    if not selected_disk:
        print("Invalid Disk Selection.")
        return

    if backend.is_system_disk(target_id):
        print("\nSAFETY WARNING: Target selected matches the ACTIVE OS Installation.")
        if not prompt_yes_no("Proceed despite safety override?", default=False):
            return
        if input('Type "OVERRIDE" to confirm: ').strip() != "OVERRIDE":
            print("Operation aborted.")
            return

    # Choose partition setup & modular cluster size
    n_parts = prompt_int("\nNumber of partitions to construct (1-10): ", min_value=1, max_value=10)
    style = prompt_partition_style()
    alloc_unit_bytes = prompt_cluster_size()

    partitions: List[Tuple[Optional[int], str, str]] = []
    used_letters: List[str] = []

    for i in range(1, n_parts + 1):
        print(f"\n--- Partition {i} ---")
        letter = ""
        if target_os == "Windows":
            while True:
                letter = input("Drive Letter (A-Z): ").strip().upper()
                if re.fullmatch(r"[A-Z]", letter) and letter not in used_letters and letter != "C":
                    used_letters.append(letter)
                    break
                print("Invalid or already assigned letter.")
        
        label = input("Volume Label: ").strip() or f"Volume{i}"
        
        size_mib = None
        if i < n_parts:
            gb = float(input("Size in GB: ").strip())
            size_mib = int(round(gb * 1024))
        else:
            if prompt_yes_no("Fill remaining disk capacity for final partition?", default=True):
                size_mib = None
            else:
                gb = float(input("Size in GB: ").strip())
                size_mib = int(round(gb * 1024))

        partitions.append((size_mib, letter, label))

    confirm_msg = f"ERASE {target_id}"
    if input(f'\nType EXACTLY "{confirm_msg}" to perform operation: ').strip() != confirm_msg:
        print("Operation canceled.")
        return

    print("\nWiping disk and applying filesystem configurations...")
    try:
        backend.wipe_and_partition(target_id, style, partitions, alloc_unit_bytes)
        print("\nDisk repartitioning and exFAT formatting completed successfully.")
    except Exception as e:
        print(f"\nExecution Error: {e}")

if __name__ == "__main__":
    main()
