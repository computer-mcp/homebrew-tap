import copy
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import call, patch


SPEC = importlib.util.spec_from_file_location("release_updates", Path(__file__).resolve().parents[1] / "Scripts/release_updates.py")
updates = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updates)


def release(version, *, draft=False):
    return {"tag_name": "v" + version, "draft": draft, "published_at": None if draft else "2026-01-01T00:00:00Z", "prerelease": "-" in version}


class EmptyDistributionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sources = json.loads((updates.ROOT / "release-sources.json").read_text())
        self.templates = updates.ROOT / "Scripts/templates"
        (self.root / "release-sources.json").write_text(json.dumps(self.sources))
        root = patch.object(updates, "ROOT", self.root)
        root.start()
        self.addCleanup(root.stop)

    def test_no_published_releases_report_no_changes(self):
        directory = self.root / ".candidate"
        github_output = self.root / "github-output"
        for pages in ([], [[]], [[release("0.1.0", draft=True)]]):
            with self.subTest(pages=pages):
                github_output.write_text("")
                directory.mkdir(exist_ok=True)
                (directory / "updates.json").write_text('[{"name": "stale-candidate"}]')
                output = io.StringIO()
                with patch.object(updates.subprocess, "check_output", return_value=json.dumps(pages)) as request, \
                     patch.object(updates.subprocess, "run") as download, \
                     patch.object(updates, "api") as api, \
                     patch.dict(os.environ, {"GITHUB_OUTPUT": str(github_output)}), redirect_stdout(output):
                    updates.prepare(directory)
                self.assertEqual(request.call_args_list, [
                    call(["gh", "api", "--paginate", "--slurp", f"repos/{source['repository']}/releases?per_page=100"], text=True, timeout=120)
                    for source in self.sources
                ])
                download.assert_not_called()
                api.assert_not_called()
                self.assertEqual(json.loads((directory / "updates.json").read_text()), [])
                self.assertEqual(github_output.read_text(), "changed=false\n")
                for source in self.sources:
                    self.assertIn(f"No eligible published release for {source['repository']}", output.getvalue())
                self.assertIn("Validated 0 distribution updates", output.getvalue())

    def test_discovery_failure_is_not_reported_as_no_changes(self):
        directory = self.root / ".candidate"
        github_output = self.root / "github-output"
        with patch.object(updates.subprocess, "check_output", side_effect=subprocess.CalledProcessError(1, "gh")), \
             patch.dict(os.environ, {"GITHUB_OUTPUT": str(github_output)}), \
             self.assertRaises(subprocess.CalledProcessError):
            updates.prepare(directory)
        self.assertFalse(directory.exists())
        self.assertFalse(github_output.exists())

    def test_empty_candidate_does_not_invoke_homebrew_or_create_packages(self):
        directory = self.root / ".candidate"
        directory.mkdir()
        (directory / "updates.json").write_text("[]\n")
        with patch.object(updates.subprocess, "check_output") as query, patch.object(updates.subprocess, "run") as command:
            updates.stage(directory)
        query.assert_not_called()
        command.assert_not_called()
        for name in ("Formula", "Casks", "Metadata"):
            self.assertFalse((self.root / name).exists())

    def test_no_distribution_metadata_requires_no_identity_requests(self):
        with patch.object(updates, "api") as request, redirect_stdout(io.StringIO()):
            updates.check()
        request.assert_not_called()

    def test_first_release_creates_package_and_metadata(self):
        directory = self.root / ".candidate"
        directory.mkdir()
        templates = self.root / "Scripts/templates"
        templates.mkdir(parents=True)
        for source in self.sources:
            with self.subTest(name=source["name"]):
                name = source["name"]
                (templates / f"{name}.rb.in").write_text((self.templates / f"{name}.rb.in").read_text())
                asset = updates.asset_names(name, "0.1.0")[0]
                url = f"https://github.com/{source['repository']}/releases/download/v0.1.0/{asset}"
                candidate = {**source, "version": "0.1.0", "assets": {asset: {"url": url, "sha256": "a" * 64}}}
                (directory / "updates.json").write_text(json.dumps([candidate]))
                with patch.object(updates.subprocess, "check_output", return_value=source["minimum_macos"]), \
                     patch.object(updates.subprocess, "run") as command:
                    updates.stage(directory)
                command.assert_not_called()
                package = self.root / ("Formula" if source["kind"] == "formula" else "Casks") / f"{name}.rb"
                self.assertTrue(package.is_file())
                self.assertNotIn("@", package.read_text())
                self.assertIn("0.1.0", package.read_text())
                self.assertEqual(json.loads((self.root / "Metadata" / f"{name}.json").read_text()), candidate)


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


class AcceptanceAttemptTests(unittest.TestCase):
    def setUp(self):
        self.repository = "computer-mcp/apple-cli"
        self.commit = "a" * 40
        self.ci = {"repository": self.repository, "run_id": "42", "run_attempt": "1",
                   "run_url": f"https://github.com/{self.repository}/actions/runs/42"}
        self.run = {"status": "completed", "conclusion": "success", "head_sha": self.commit,
                    "path": ".github/workflows/release.yml", "run_attempt": 1, "html_url": self.ci["run_url"]}
        self.attempt = copy.deepcopy(self.run)
        self.jobs = {"jobs": [{"name": "build", "status": "completed", "conclusion": "success"}]}

    def accepted(self):
        with patch.object(updates, "api", side_effect=[self.run, self.attempt, self.jobs]):
            return updates.accepted_release_run(self.repository, self.ci, self.commit)

    def test_initial_success_requires_the_receipt_build_attempt(self):
        self.assertEqual(self.accepted(), self.run)

    def test_successful_publication_retry_can_reuse_accepted_build(self):
        self.run["run_attempt"] = 2
        self.attempt["conclusion"] = "failure"
        self.assertEqual(self.accepted()["run_attempt"], 2)

    def test_failed_or_running_publication_is_not_accepted(self):
        for status, conclusion in (("completed", "failure"), ("in_progress", None)):
            self.run.update(status=status, conclusion=conclusion)
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.accepted()

    def test_failed_missing_or_duplicate_build_is_not_accepted(self):
        for jobs in ([], [{"name": "build", "status": "completed", "conclusion": "failure"}],
                     [self.jobs["jobs"][0], self.jobs["jobs"][0]]):
            with self.subTest(jobs=jobs), patch.object(updates, "api", side_effect=[self.run, self.attempt, {"jobs": jobs}]), self.assertRaises(ValueError):
                updates.accepted_release_run(self.repository, self.ci, self.commit)

    def test_publication_workflow_cannot_change_source_or_path(self):
        for key, value in (("head_sha", "b" * 40), ("path", ".github/workflows/ci.yml")):
            changed = {**self.run, key: value}
            with self.subTest(key=key), patch.object(updates, "api", return_value=changed), self.assertRaises(ValueError):
                updates.accepted_release_run(self.repository, self.ci, self.commit)

    def test_build_attempt_identity_cannot_change(self):
        for key, value in (("head_sha", "b" * 40), ("path", ".github/workflows/ci.yml"), ("run_attempt", 2)):
            changed = {**self.attempt, key: value}
            with self.subTest(key=key), patch.object(updates, "api", side_effect=[self.run, changed]), self.assertRaises(ValueError):
                updates.accepted_release_run(self.repository, self.ci, self.commit)

    def test_receipt_cannot_reference_an_attempt_after_publication(self):
        self.ci["run_attempt"] = "2"
        with self.assertRaises(ValueError):
            self.accepted()

    def test_malformed_receipt_is_rejected_before_network_access(self):
        for key, value in (("repository", "other/apple-cli"), ("run_id", "0"), ("run_id", "４２"), ("run_attempt", "01"), ("run_attempt", "1/jobs")):
            with self.subTest(key=key), patch.object(updates, "api") as request, self.assertRaises(ValueError):
                updates.accepted_release_run(self.repository, {**self.ci, key: value}, self.commit)
            request.assert_not_called()
