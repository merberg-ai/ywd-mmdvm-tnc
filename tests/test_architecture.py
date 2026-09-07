from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ArchitectureContractTests(unittest.TestCase):
    def test_product_layer_does_not_compose_upper_layer_personalities(self) -> None:
        product_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((ROOT / "src" / "ywdtnc").rglob("*.py"))
        ).lower()
        forbidden_import_fragments = (
            "persistent_bbs",
            "mailbox_console",
            "node_mailbox",
            "beacon_scheduler",
            "classic_console",
            "forwarding_config",
        )
        for fragment in forbidden_import_fragments:
            self.assertNotIn(fragment, product_text)

    def test_only_qualified_service_module_is_imported(self) -> None:
        engine = (ROOT / "src" / "ywdtnc" / "engine.py").read_text(encoding="utf-8")
        service_imports = [
            line.strip()
            for line in engine.splitlines()
            if line.strip().startswith("from ywd1278.service")
        ]
        self.assertEqual(
            service_imports,
            ["from ywd1278.service.tnc_runtime import SustainedTNCRuntime"],
        )


if __name__ == "__main__":
    unittest.main()
