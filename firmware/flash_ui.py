#!/usr/bin/env python3
"""Concise public UI for the frozen qualified firmware deployment backend."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "firmware"))
import qualified_flash as qf  # noqa: E402


def color(code: str, text: str) -> str:
    if sys.stdout.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM", "dumb") != "dumb":
        return f"\033[{code}m{text}\033[0m"
    return text


def ok(text: str) -> None:
    print(f"{color('32', '✓')} {text}")


def step(text: str) -> None:
    print(f"{color('36', '→')} {text}")


def warn(text: str) -> None:
    print(f"{color('33', '!')} {text}", file=sys.stderr)


def fail(text: str, log: Path) -> None:
    print(f"{color('31', '✗')} {text}", file=sys.stderr)
    print(f"Log: {log}", file=sys.stderr)


def make_log() -> Path:
    env = os.environ.get("YWD_TNC_LOG_FILE", "").strip()
    if env:
        path = Path(env)
    else:
        path = Path("/var/log/ywd-mmdvm-tnc") / f"firmware-{time.strftime('%Y%m%d-%H%M%S')}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    try:
        path.chmod(0o640)
    except OSError:
        pass
    return path


def read_interactive_confirmation(prompt: str) -> str:
    """Read a destructive-action confirmation from a real terminal only."""
    if sys.stdin.isatty():
        sys.stdout.write(prompt)
        sys.stdout.flush()
        return sys.stdin.readline().strip()
    try:
        with open("/dev/tty", "r+", encoding="utf-8", buffering=1) as tty:
            tty.write(prompt)
            return tty.readline().strip()
    except OSError as exc:
        raise qf.FlashError("interactive firmware confirmation requires a TTY") from exc


def main() -> int:
    parser = argparse.ArgumentParser(prog="ywd-mmdvm-tnc firmware")
    parser.add_argument("mode", choices=("probe", "backup", "flash"))
    parser.add_argument("--device", default="/dev/ttyAMA0")
    parser.add_argument("--firmware", default="")
    parser.add_argument("--stock-backup-dir", default="")
    parser.add_argument("--authorize", default="")
    parser.add_argument("--force-reflash", action="store_true")
    args = parser.parse_args()
    log_path = make_log()
    log = log_path.open("a", encoding="utf-8", buffering=1)

    def quiet_run(cmd: list[str], *, check: bool = True, capture: bool = False):
        print(f"$ {' '.join(cmd)}", file=log)
        if capture:
            proc = subprocess.run(cmd, text=True, capture_output=True, check=check)
            if proc.stdout:
                print(proc.stdout, end="", file=log)
            if proc.stderr:
                print(proc.stderr, end="", file=log)
            return proc
        return subprocess.run(cmd, text=True, stdout=log, stderr=subprocess.STDOUT, check=check)

    qf.run = quiet_run

    def quiet_call(func, *fargs, **fkwargs):
        with redirect_stdout(log), redirect_stderr(log):
            return func(*fargs, **fkwargs)

    print()
    print(color("1;36", "YWD-MMDVM-TNC firmware"))
    print("-------------------------")
    try:
        qf.require_root()
        qf.require_tools()
        if args.force_reflash and args.mode != "flash":
            raise qf.FlashError("--force-reflash is valid only with flash mode")
        profile = qf.load_profile(qf.PROFILE_PATH)
        qf.verify_core_pin(profile.qualified_core_commit)
        target = qf.load_target(profile.target_id)
        if not Path(args.device).exists():
            raise qf.FlashError(f"modem UART does not exist: {args.device}")
        firmware = Path(args.firmware) if args.firmware else ROOT / profile.artifact_relative_path
        ok("Qualified source, tools and target profile verified")

        with qf.stopped_known_modem_owners(args.device, restore=args.mode != "flash"):
            step("Reading HAT firmware identity")
            identity = qf.probe_identity(args.device, profile.target_id)
            if not qf.accepted_identity(target, identity):
                raise qf.FlashError("running firmware identity is outside the qualified target lineage")
            ok("HAT identity accepted")
            print(f"  device: {args.device}")

            if args.mode == "probe":
                print(f"  runtime: {identity}")
                ok("Firmware probe complete")
                return 0

            if args.mode == "backup":
                step("Capturing two independent stock-flash reads")
                directory = quiet_call(qf.make_stock_backup, profile, target, args.device, identity)
                ok("Golden stock rollback backup verified")
                print(f"  backup: {directory}")
                return 0

            if args.authorize != profile.flash_authorization_token:
                raise qf.FlashError(f"flash mode requires --authorize {profile.flash_authorization_token}")

            step("Verifying exact qualified firmware artifact")
            qf.verify_artifact(profile, firmware)
            ok("Firmware artifact SHA-256 verified")

            backup_dir: Path | None
            if args.stock_backup_dir:
                backup_dir = Path(args.stock_backup_dir)
                qf.verify_stock_backup(profile, backup_dir)
                ok("Specified stock rollback backup verified")
            elif qf.stock_identity(target, identity):
                step("Backing up original stock firmware")
                backup_dir = quiet_call(qf.make_stock_backup, profile, target, args.device, identity)
                ok("Golden stock rollback backup verified")
                print(f"  backup: {backup_dir}")
            else:
                backup_dir = qf.find_verified_stock_backup(profile)
                if backup_dir:
                    ok("Existing verified stock rollback backup found")

            flash_written = False
            boot = qf.Bootloader(device=args.device, target_id=profile.target_id, expected_version=profile.expected_bootloader_version, expected_device=profile.expected_device_id)
            fd, readback_name = tempfile.mkstemp(prefix="ywd-mmdvm-tnc-readback.", suffix=".bin")
            os.close(fd)
            readback = Path(readback_name)
            try:
                if identity == profile.expected_identity and not args.force_reflash:
                    step("Qualified firmware already installed; verifying programmed bytes")
                    quiet_call(boot.enter)
                    qf.readback_product(profile, args.device, readback)
                    quiet_call(boot.restart_application)
                    ok("Installed firmware readback verified")
                else:
                    if backup_dir is None:
                        raise qf.FlashError("a verified stock rollback backup is required before any main-flash write")
                    qf.verify_stock_backup(profile, backup_dir)
                    print()
                    if args.force_reflash and identity == profile.expected_identity:
                        warn("A forced reflash of the currently accepted firmware was explicitly requested.")
                    warn("A firmware write is ready. The verified stock backup is safe.")
                    prompt = f"Type {profile.final_write_confirmation} to write the qualified image: "
                    response = read_interactive_confirmation(prompt)
                    if response != profile.final_write_confirmation:
                        raise qf.FlashError("firmware write cancelled")
                    step("Programming qualified firmware")
                    quiet_call(boot.enter)
                    qf.run(["stm32flash", "-b", "115200", "-w", str(firmware), "-v", args.device])
                    flash_written = True
                    ok("Firmware programmed")
                    step("Reading programmed bytes back independently")
                    qf.readback_product(profile, args.device, readback)
                    quiet_call(boot.restart_application)
                    ok("Programmed readback verified")
            finally:
                quiet_call(boot.cleanup)
                readback.unlink(missing_ok=True)

            step("Verifying running firmware after restart")
            post_identity = qf.probe_identity(args.device, profile.target_id)
            if post_identity != profile.expected_identity:
                raise qf.FlashError(f"post-operation runtime identity mismatch: {post_identity}")
            quiet_call(qf.write_ready_record, profile, identity=post_identity, backup_dir=backup_dir, flash_written=flash_written)
            ok("Qualified YWD-MMDVM-TNC firmware is ready")
            print(f"  flash written: {'yes' if flash_written else 'no (verification only)'}")
            print(f"  force reflash requested: {'yes' if args.force_reflash else 'no'}")
            if backup_dir:
                print(f"  rollback: {backup_dir}")
            print(f"  log: {log_path}")
            print("  RF transmit during firmware operation: no")
            print("  option bytes written: no")
            return 0
    except (qf.FlashError, qf.FirmwareProfileError, OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=log)
        fail(str(exc), log_path)
        return 20
    finally:
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
