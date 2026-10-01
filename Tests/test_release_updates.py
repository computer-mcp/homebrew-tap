import copy
import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location("release_updates", Path(__file__).resolve().parents[1] / "Scripts/release_updates.py")
updates = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updates)


def release(version, *, draft=False):
    return {"tag_name": "v" + version, "draft": draft, "published_at": None if draft else "2026-01-01T00:00:00Z", "prerelease": "-" in version}


class ReleaseSelectionTests(unittest.TestCase):
    def test_draft_cannot_replace_published_version(self):
        self.assertEqual(updates.select_release([release("2.0.0", draft=True), release("1.0.0")], True)["tag_name"], "v1.0.0")

    def test_stable_takes_precedence_over_preview(self):
        self.assertEqual(updates.select_release([release("1.0.0-beta.2"), release("0.9.0")], True)["tag_name"], "v0.9.0")

    def test_preview_sequence_compares_numerically(self):
        self.assertEqual(updates.select_release([release("1.0.0-alpha.9"), release("1.0.0-alpha.10")], True)["tag_name"], "v1.0.0-alpha.10")

    def test_stable_channel_does_not_install_preview(self):
        self.assertIsNone(updates.select_release([release("1.0.0-rc.1")], False))

    def test_preview_status_must_match_tag(self):
        record = release("1.0.0-alpha.1")
        record["prerelease"] = False
        with self.assertRaises(ValueError):
            updates.select_release([record], True)

    def test_invalid_versions_do_not_enter_distribution(self):
        for version in ("01.0.0", "1.0.0-alpha.0", "1.0.0-alpha.01", "1.0.0+build", "1.0.0\n", "１.0.0"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                updates.version_key(version)


class ReleaseIdentityTests(unittest.TestCase):
    def setUp(self):
        self.current = {"repository": "computer-mcp/apple-cli", "version": "1.0.0", "release_id": 1, "tag": "v1.0.0", "source_commit": "a" * 40,
                        "assets": {"archive": {"sha256": "b" * 64}}}

    def test_matching_version_is_idempotent(self):
        self.assertFalse(updates.verify_identity(self.current, copy.deepcopy(self.current)))

    def test_same_version_cannot_change_release_or_bytes(self):
        for key, value in (("release_id", 2), ("source_commit", "c" * 40), ("tag", "v2.0.0"), ("assets", {})):
            candidate = copy.deepcopy(self.current)
            candidate[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                updates.verify_identity(self.current, candidate)

    def test_versions_cannot_regress(self):
        with self.assertRaises(ValueError):
            updates.verify_identity(self.current, {**self.current, "version": "0.9.0"})

    def test_new_version_requires_distribution_validation(self):
        self.assertTrue(updates.verify_identity(self.current, {**self.current, "version": "1.0.1"}))


class AssetBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.asset = {"name": "package.tar.gz", "digest": "sha256:" + "a" * 64, "size": 128,
                      "browser_download_url": "https://github.com/computer-mcp/apple-cli/releases/download/v1.0.0/package.tar.gz"}
        self.release = {**release("1.0.0"), "assets": [self.asset]}

    def test_only_exact_published_asset_url_is_accepted(self):
        self.assertEqual(updates.asset_record("computer-mcp/apple-cli", self.release, self.asset["name"])["sha256"], "a" * 64)
        self.asset["browser_download_url"] = "https://example.com/package.tar.gz"
        with self.assertRaises(ValueError):
            updates.asset_record("computer-mcp/apple-cli", self.release, self.asset["name"])

    def test_missing_and_duplicate_assets_are_rejected(self):
        for assets in ([], [self.asset, self.asset]):
            self.release["assets"] = assets
            with self.subTest(assets=len(assets)), self.assertRaises(ValueError):
                updates.asset_record("computer-mcp/apple-cli", self.release, self.asset["name"])

    def test_unverified_or_unbounded_assets_are_rejected(self):
        for key, value in (("digest", None), ("digest", "sha256:bad"), ("size", 0), ("size", 513 * 1024 * 1024)):
            self.release["assets"] = [{**self.asset, key: value}]
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                updates.asset_record("computer-mcp/apple-cli", self.release, self.asset["name"])
