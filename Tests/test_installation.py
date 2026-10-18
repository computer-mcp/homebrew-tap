import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("installation", Path(__file__).resolve().parents[1] / "Scripts/verify_installation.py")
installation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installation)


class InstalledArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.prefix = Path(temporary.name)
        binaries = self.prefix / "libexec/bin"
        binaries.mkdir(parents=True)
        (self.prefix / "bin").mkdir()
        records = []
        for name in ("apple", "apple-cli-mcp", "libswiftCompatibilitySpan.dylib"):
            content = name.encode() * (2 * 1024 * 1024 // len(name) + 1)
            (binaries / name).write_bytes(content)
            records.append({"path": f"bin/{name}", "sha256": hashlib.sha256(content).hexdigest()})
            if not name.endswith(".dylib"):
                (self.prefix / "bin" / name).symlink_to(binaries / name)
        self.metadata = {"version": "1.0.0", "binaries": records}

    def verify(self):
        def output(arguments, **kwargs):
            return str(self.prefix) if "--prefix" in arguments else self.metadata["version"]
        with patch.object(installation.subprocess, "check_output", side_effect=output), patch.object(installation.subprocess, "run"):
            installation.verify_cli(self.metadata)

    def test_complete_binary_and_runtime_inventory_is_accepted(self):
        self.verify()

    def test_changed_executable_or_runtime_is_rejected(self):
        for record in self.metadata["binaries"]:
            file = self.prefix / "libexec" / record["path"]
            original = file.read_bytes()
            file.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
            with self.subTest(path=record["path"]), self.assertRaisesRegex(ValueError, "changed an accepted executable"):
                self.verify()
            file.write_bytes(original)

    def test_relocated_link_cannot_change_runtime_layout(self):
        link = self.prefix / "bin/apple"
        link.unlink()
        other = self.prefix / "bin/other-apple"
        other.write_bytes(b"other installation")
        link.symlink_to(other)
        with self.assertRaisesRegex(ValueError, "does not preserve the runtime layout"):
            self.verify()
