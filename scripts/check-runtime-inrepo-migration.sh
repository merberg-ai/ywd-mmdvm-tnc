#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python3 - "$ROOT" <<'PY'
from pathlib import Path
import hashlib,json,subprocess,sys
root=Path(sys.argv[1]); core=root/"vendor/ywd-1278"
data=json.loads((root/"qualification/fwm3-runtime-vendor-manifest.json").read_text(encoding="utf-8"))
expected="c28c46c3478d7931af611923c92cd8f692a00858"
assert subprocess.check_output(["git","-C",str(core),"rev-parse","HEAD"],text=True).strip() == expected
assert data["source_commit"] == expected and data["phase"] == "FWM3"
for key in ("hardware_accessed","gpio_accessed","uart_opened","flash_written","rf_transmitted"):
    assert data[key] is False
runtime=data["runtime_files"]; support=data["support_files"]
assert runtime
assert {x["path"] for x in support} == {"firmware/hat_control.py","firmware/probe_hat.py","firmware/targets.json"}
for item in runtime + support:
    dst=root/item["path"]; src=core/item["source_path"]
    assert dst.read_bytes() == src.read_bytes(), item["path"]
    assert subprocess.check_output(["git","hash-object",str(dst)],text=True).strip() == item["git_blob_sha"]
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == item["sha256"]
    assert dst.stat().st_size == item["bytes"]
forbidden={"node","bbs","mailbox","beacon","console","forwarding"}
for path in (root/"src/ywd1278").rglob("*.py"):
    assert not (set(part.lower() for part in path.parts) & forbidden), path
assert {p.name for p in (root/"src/ywd1278/service").glob("*.py")} <= {"__init__.py","live_channel_access.py","rx_runtime.py","tnc_runtime.py"}
pyproject=(root/"pyproject.toml").read_text(encoding="utf-8")
import tomllib
parsed_pyproject=tomllib.loads(pyproject)
project_version=parsed_pyproject["project"]["version"]
package_include=parsed_pyproject["tool"]["setuptools"]["packages"]["find"]["include"]
assert "ywdtnc*" in package_include
assert "ywd1278*" in package_include
# Additional product-side packages are permitted as long as the frozen qualified
# ywd1278 closure above remains byte-identical. ywdweb is a passive observer.
assert set(package_include) <= {"ywdtnc*", "ywd1278*", "ywdweb*"}
assert f'__version__ = "{project_version}"' in (root/"src/ywdtnc/__init__.py").read_text(encoding="utf-8")
installer=(root/"installer/install.sh").read_text(encoding="utf-8")
assert '$2/vendor/ywd-1278' not in installer
assert 'PYTHONPATH="$ROOT/src:$ROOT/vendor/ywd-1278/src"' not in installer
assert 'PYTHONPATH="$ROOT/src"' in installer
probe=(root/"firmware/probe.sh").read_text(encoding="utf-8")
assert 'vendor/ywd-1278/firmware' not in probe
assert 'firmware/probe_hat.py' in probe
flash=(root/"firmware/qualified_flash.py").read_text(encoding="utf-8")
assert 'ROOT / "vendor" / "ywd-1278"' not in flash
assert 'RUNTIME_VENDOR_MANIFEST' in flash
print(f"FWM3_RUNTIME_FILES={len(runtime)}")
print("FWM3_RUNTIME_BLOBS_EXACT=PASS")
print("FWM3_HAT_SUPPORT_BLOBS_EXACT=PASS")
print("FWM3_FORBIDDEN_PRODUCT_LAYERS_ABSENT=PASS")
print("FWM3_PRODUCT_PACKAGE_DISCOVERY=PASS")
print("FWM3_PASSIVE_WEBUI_PACKAGE_ALLOWED=PASS")
print("FWM3_INSTALLER_VENDOR_PIP_DEPENDENCY=ABSENT")
print("FWM3_PROBE_SUPPORT_SOURCE=IN_REPO")
print("FWM3_FLASH_SUPPORT_SOURCE=IN_REPO")
print("HARDWARE_ACCESSED=NO GPIO_ACCESSED=NO UART_OPENED=NO FLASH_WRITTEN=NO RF_TRANSMITTED=NO")
PY
python3 -m compileall -q "$ROOT/src/ywd1278" "$ROOT/firmware/probe_hat.py" "$ROOT/firmware/hat_control.py" "$ROOT/firmware/qualified_flash.py"
