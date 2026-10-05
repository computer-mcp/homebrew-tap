import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


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


class DistributionRenderTests(unittest.TestCase):
    def setUp(self):
        self.metadata = {path.stem: json.loads(path.read_text()) for path in (updates.ROOT / "Metadata").glob("*.json")}

    def test_committed_distributions_match_their_templates(self):
        for name, candidate in self.metadata.items():
            with self.subTest(name=name):
                self.assertEqual(updates.render(candidate), updates.distribution_path(candidate).read_text())

    def test_new_cask_version_keeps_a_versioned_url(self):
        candidate = copy.deepcopy(self.metadata["computer-mcp"])
        old, new = candidate["version"], "99.0.0"
        candidate["version"], candidate["tag"] = new, "v" + new
        candidate["assets"] = {name.replace(old, new): {**record, "url": record["url"].replace(old, new)} for name, record in candidate["assets"].items()}
        text = updates.render(candidate)
        self.assertIn(f'version "{new}"', text)
        self.assertIn('url "https://github.com/computer-mcp/computer-mcp/releases/download/v#{version}/Computer-MCP-#{version}-universal.dmg"', text)

    def test_unreviewed_distribution_is_rejected(self):
        candidate = {**self.metadata["computer-mcp"], "kind": "formula"}
        with self.assertRaises(ValueError):
            updates.render(candidate)


class ProposalTests(unittest.TestCase):
    BRANCH = "automation/distribution-updates"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.root, self.remote = base / "tap", base / "remote.git"
        self.git(base, "init", "--quiet", "--bare", str(self.remote))
        self.git(base, "init", "--quiet", "-b", "master", str(self.root))
        self.git(self.root, "remote", "add", "origin", str(self.remote))
        self.write_metadata("1.0.0")
        (self.root / "Formula").mkdir()
        (self.root / "Formula/apple-cli.rb").write_text("1.0.0\n")
        (self.root / "Formula/retired.rb").write_text("retired\n")
        self.git(self.root, "add", ".")
        self.git(self.root, "commit", "--quiet", "-m", "Fixture")
        self.git(self.root, "push", "--quiet", "origin", "master")
        self.head = self.git(self.root, "rev-parse", "HEAD")
        self.signed, self.verified, self.calls = set(), True, []
        for patcher in (patch.object(updates, "api", self.read), patch.object(updates, "write", self.write),
                        patch.dict(os.environ, {"GITHUB_REPOSITORY": "computer-mcp/homebrew-tap", "GITHUB_OUTPUT": ""})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def git(self, root, *arguments, data=None, environment=None):
        command = ["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                   "-c", "commit.gpgsign=false", *arguments]
        return subprocess.run(command, input=data, check=True, capture_output=True, timeout=10, env=environment).stdout.decode().strip()

    def write_metadata(self, version):
        (self.root / "Metadata").mkdir(exist_ok=True)
        (self.root / "Metadata/apple-cli.json").write_text(json.dumps({"name": "apple-cli", "version": version}) + "\n")

    def stage_update(self):
        self.write_metadata("1.1.0")
        (self.root / "Formula/apple-cli.rb").write_text("1.1.0\n")
        (self.root / "Formula/retired.rb").unlink()
        self.git(self.root, "add", "-A")

    def proposal(self):
        return self.git(self.remote, "for-each-ref", "--format=%(objectname)", f"refs/heads/{self.BRANCH}")

    def commit(self, sha):
        tree, *parents = self.git(self.remote, "show", "-s", "--format=%T %P", sha).split()
        return {"sha": sha, "tree": {"sha": tree}, "parents": [{"sha": parent} for parent in parents],
                "verification": {"verified": sha in self.signed}}

    def read(self, endpoint):
        self.assertTrue(endpoint.startswith("repos/computer-mcp/homebrew-tap/git/commits/"))
        return self.commit(endpoint.rsplit("/", 1)[1])

    def write(self, method, endpoint, body):
        operation = (method, endpoint.removeprefix("repos/computer-mcp/homebrew-tap/"))
        self.calls.append(operation)
        if operation == ("POST", "git/blobs"):
            return {"sha": self.git(self.remote, "hash-object", "-w", "--stdin", data=base64.b64decode(body["content"]))}
        if operation == ("POST", "git/trees"):
            with tempfile.TemporaryDirectory() as directory:
                environment = {**os.environ, "GIT_INDEX_FILE": str(Path(directory) / "index")}
                self.git(self.remote, "read-tree", body["base_tree"], environment=environment)
                lines = [f"0 {'0' * 40}\t{entry['path']}" if entry["sha"] is None else f"{entry['mode']} {entry['sha']}\t{entry['path']}"
                         for entry in body["tree"]]
                self.git(self.remote, "update-index", "--index-info", data="".join(line + "\n" for line in lines).encode(), environment=environment)
                return {"sha": self.git(self.remote, "write-tree", environment=environment)}
        if operation == ("POST", "git/commits"):
            parents = [argument for parent in body["parents"] for argument in ("-p", parent)]
            sha = self.git(self.remote, "commit-tree", body["tree"], *parents, "-m", body["message"])
            if self.verified:
                self.signed.add(sha)
            return self.commit(sha)
        if operation == ("POST", "git/refs"):
            self.git(self.remote, "update-ref", body["ref"], body["sha"], "0" * 40)
            return {}
        if operation == ("PATCH", f"git/refs/heads/{self.BRANCH}") and body["force"] is True:
            self.git(self.remote, "update-ref", f"refs/heads/{self.BRANCH}", body["sha"])
            return {}
        raise AssertionError(f"Unexpected API request {operation}")

    def test_staged_distribution_becomes_a_signed_proposal(self):
        self.stage_update()
        tree = self.git(self.root, "write-tree")
        commit = updates.propose(self.BRANCH, self.root)
        self.assertEqual(self.proposal(), commit)
        self.assertIn(commit, self.signed)
        self.assertEqual(self.git(self.remote, "show", "-s", "--format=%T %P%n%s", commit), f"{tree} {self.head}\nUpdate apple-cli to 1.1.0")
        self.assertEqual(self.git(self.root, "rev-parse", "HEAD"), self.head)

    def test_unsigned_proposal_is_not_published(self):
        self.stage_update()
        self.verified = False
        with self.assertRaisesRegex(ValueError, "sign"):
            updates.propose(self.BRANCH, self.root)
        self.assertEqual(self.proposal(), "")

    def test_identical_signed_proposal_is_not_recreated(self):
        self.stage_update()
        commit = updates.propose(self.BRANCH, self.root)
        self.calls.clear()
        self.assertIsNone(updates.propose(self.BRANCH, self.root))
        self.assertEqual((self.proposal(), self.calls), (commit, []))

    def test_unsigned_proposal_with_the_same_files_is_replaced(self):
        self.stage_update()
        unsigned = self.git(self.root, "commit-tree", self.git(self.root, "write-tree"), "-p", self.head, "-m", "Unsigned")
        self.git(self.root, "push", "--quiet", "origin", f"{unsigned}:refs/heads/{self.BRANCH}")
        commit = updates.propose(self.BRANCH, self.root)
        self.assertNotEqual(commit, unsigned)
        self.assertEqual(self.proposal(), commit)
        self.assertIn(("PATCH", f"git/refs/heads/{self.BRANCH}"), self.calls)

    def test_only_distribution_files_are_proposed(self):
        self.stage_update()
        (self.root / "README.md").write_text("Unreviewed\n")
        self.git(self.root, "add", "README.md")
        with self.assertRaisesRegex(ValueError, "distribution files"):
            updates.propose(self.BRANCH, self.root)
        self.assertEqual((self.proposal(), self.calls), ("", []))

    def test_unchanged_distribution_makes_no_request(self):
        self.assertIsNone(updates.propose(self.BRANCH, self.root))
        self.assertEqual(self.calls, [])
