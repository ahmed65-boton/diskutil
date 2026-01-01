import os
import sys
import tempfile
import subprocess
import textwrap
import ctypes

def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def run_diskpart(commands: str) -> subprocess.CompletedProcess:
    # diskpart reads commands from a script file
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt", encoding="utf-8") as f:
        f.write(commands)
        script_path = f.name

    try:
        # /s runs a script
        cp = subprocess.run(
            ["diskpart", "/s", script_path],
            capture_output=True,
            text=True
        )
        return cp
    finally:
        try:
            os.remove(script_path)
        except OSError:
            pass

def main():
    if os.name != "nt":
        print("This script is intended for Windows (diskpart).")
        sys.exit(1)

    if not is_admin():
        print("ERROR: Please run this script as Administrator.")
        print("Tip: Start Menu -> type 'cmd' -> Run as administrator -> then run: python yourscript.py")
        sys.exit(1)

    print("=== STEP 1: Listing disks (diskpart -> list disk) ===\n")
    list_script = "list disk\nexit\n"
    cp = run_diskpart(list_script)

    output = (cp.stdout or "") + ("\n" + cp.stderr if cp.stderr else "")
    print(output)

    if cp.returncode != 0:
        print("\nDiskpart returned a non-zero exit code. Aborting.")
        sys.exit(cp.returncode)

    print("\n=== STEP 2: Select the USB disk number ===")
    disk_num = input("Enter the DISK NUMBER of your USB (e.g., 1): ").strip()

    if not disk_num.isdigit():
        print("Invalid disk number. Aborting.")
        sys.exit(1)

    print("\n⚠️  DANGER ZONE ⚠️")
    print(f"You are about to ERASE Disk {disk_num}.")
    print("Make sure this is your USB drive by size in the list above.")
    confirm = input(f"Type ERASE-{disk_num} to continue: ").strip()

    if confirm != f"ERASE-{disk_num}":
        print("Confirmation did not match. Aborting.")
        sys.exit(0)

    print("\n=== STEP 3: Wiping and formatting as MBR + single FAT32 partition ===")
    # Notes:
    # - 'clean' wipes partition table (data is gone)
    # - unit=32768 sets allocation unit size (cluster size) to 32KB
    # - You can add label=USB if you want: format fs=fat32 quick label=USB unit=32768
    format_script = textwrap.dedent(f"""\
        select disk {disk_num}
        clean
        convert mbr
        create partition primary
        format fs=fat32 quick unit=32768
        assign
        exit
    """)

    cp2 = run_diskpart(format_script)
    output2 = (cp2.stdout or "") + ("\n" + cp2.stderr if cp2.stderr else "")
    print(output2)

    if cp2.returncode == 0:
        print("\nDone. Safely eject the drive from Windows, then try it in your phone again.")
    else:
        print("\nDiskpart reported an error. Nothing else was changed by this script beyond what diskpart did.")
        sys.exit(cp2.returncode)

if __name__ == "__main__":
    main()
