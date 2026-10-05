#!/usr/bin/env python3
"""Validate product release inputs, stage Homebrew distribution updates and propose them."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parent.parent
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-(alpha|beta|rc)\.([1-9][0-9]*))?", re.ASCII)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def version_key(version):
    match = VERSION.fullmatch(version)
    require(match is not None, "Unsupported product version")
    major, minor, patch, stage, number = match.groups()
    return (int(major), int(minor), int(patch), {"alpha": 0, "beta": 1, "rc": 2, None: 3}[stage], int(number or 0))


def api(endpoint):
    return json.loads(subprocess.check_output(["gh", "api", endpoint], text=True, timeout=120))


def write(method, endpoint, body):
    return json.loads(subprocess.check_output(["gh", "api", "--method", method, endpoint, "--input", "-"],
                                              input=json.dumps(body), text=True, timeout=120))


def git(root, *arguments):
    return subprocess.check_output(["git", "-C", str(root), *arguments], timeout=60)


def digest(file):
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def select_release(releases, allow_prereleases):
    candidates = []
    for release in releases:
        tag = release["tag_name"]
        if release["draft"] or not release["published_at"] or not tag.startswith("v") or not VERSION.fullmatch(tag[1:]):
            continue
        require(release["prerelease"] == ("-" in tag), "Release preview status disagrees with its version")
        if not release["prerelease"] or allow_prereleases:
            candidates.append(release)
    stable = [item for item in candidates if not item["prerelease"]]
    return max(stable or candidates, key=lambda item: version_key(item["tag_name"][1:]), default=None)


def source_commit(repository, tag):
    value = api(f"repos/{repository}/git/ref/tags/{tag}")["object"]
    for _ in range(10):
        if value["type"] == "commit":
            require(re.fullmatch(r"[0-9a-f]{40}", value["sha"]), "Invalid source commit")
            return value["sha"]
        require(value["type"] == "tag", "Unsupported tag object")
        value = api(f"repos/{repository}/git/tags/{value['sha']}")["object"]
    raise ValueError("Tag chain exceeds its bound")


def asset_record(repository, release, name):
    assets = [item for item in release["assets"] if item["name"] == name]
    require(len(assets) == 1, f"Missing or duplicate release asset: {name}")
    asset = assets[0]
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", asset["digest"] or ""), "Release asset has no SHA-256 digest")
    expected = f"https://github.com/{repository}/releases/download/{release['tag_name']}/{name}"
    require(asset["browser_download_url"] == expected, "Unexpected release asset URL")
    require(0 < asset["size"] <= 512 * 1024 * 1024, "Unsupported release asset size")
    return {"url": expected, "sha256": asset["digest"][7:], "size": asset["size"]}


def verify_identity(current, candidate):
    if not current:
        return True
    require(version_key(candidate["version"]) >= version_key(current["version"]), "Release version would regress")
    if candidate["version"] != current["version"]:
        return True
    for key in ("repository", "release_id", "tag", "source_commit", "assets"):
        require(candidate[key] == current[key], "Published release identity changed at the same version")
    return False


def asset_names(name, version):
    if name == "apple-cli":
        package = f"apple-cli-{version}-macos-arm64"
        return [package + suffix for suffix in (".tar.gz", ".tar.gz.sha256", ".provenance.json", ".verification.json")]
    if name == "computer-mcp":
        return [f"Computer-MCP-{version}-universal.dmg", "SHA256SUMS", "release.json",
                f"Computer-MCP-{version}-universal-ArtifactProvenance.json",
                f"Computer-MCP-{version}-AppNotary.json", f"Computer-MCP-{version}-DMGNotary.json"]
    raise ValueError("No acceptance contract for this product")


def accepted_release_run(repository, ci, commit):
    require(ci["repository"] == repository, "Acceptance belongs to another repository")
    require(re.fullmatch(r"[1-9][0-9]*", str(ci["run_id"]), re.ASCII), "Invalid acceptance run")
    require(re.fullmatch(r"[1-9][0-9]*", str(ci["run_attempt"]), re.ASCII), "Invalid acceptance attempt")
    endpoint = f"repos/{repository}/actions/runs/{ci['run_id']}"
    run = api(endpoint)
    require(run["status"] == "completed" and run["conclusion"] == "success", "Release workflow is not accepted")
    require(run["head_sha"] == commit and run["path"] == ".github/workflows/release.yml", "Acceptance workflow identity mismatch")
    require(run["run_attempt"] >= int(ci["run_attempt"]), "Acceptance attempt mismatch")
    require(ci["run_url"] == run["html_url"], "Acceptance URL mismatch")
    attempt = api(f"{endpoint}/attempts/{ci['run_attempt']}")
    require(attempt["head_sha"] == commit and attempt["path"] == run["path"] and str(attempt["run_attempt"]) == str(ci["run_attempt"]), "Acceptance build attempt identity mismatch")
    jobs = api(f"{endpoint}/attempts/{ci['run_attempt']}/jobs?per_page=100")["jobs"]
    builds = [job for job in jobs if job["name"] == "build"]
    require(len(builds) == 1 and builds[0]["status"] == "completed" and builds[0]["conclusion"] == "success", "Archive build attempt is not accepted")
    return run


def validate_apple_cli(candidate, directory):
    repository, version, tag = candidate["repository"], candidate["version"], candidate["tag"]
    package = f"apple-cli-{version}-macos-arm64"
    manifest = json.loads((directory / (package + ".provenance.json")).read_text())
    receipt = json.loads((directory / (package + ".verification.json")).read_text())
    run = accepted_release_run(repository, receipt["ci"], candidate["source_commit"])
    require(receipt["tested_macos"].split(".")[0] == candidate["minimum_macos"], "Review the new tested platform before updating the formula")
    with tempfile.TemporaryDirectory(prefix="tap-release-source-") as temporary:
        source = Path(temporary) / "source"
        subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch", tag, f"https://github.com/{repository}.git", str(source)], check=True, timeout=180)
        require(subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip() == candidate["source_commit"], "Source tag changed")
        subprocess.run(["python3", "-B", str(source / "Scripts/verify-release"), "--directory", str(directory), "--source", str(source), "--tag", tag, "--require-verification"], check=True, timeout=180)
    candidate["binaries"] = [{"path": item["path"], "sha256": item["sha256"]} for item in manifest["binaries"] + manifest["swift_runtime_libraries"]]
    candidate["acceptance_run"] = run["html_url"]


def validate_computer_mcp(candidate, directory):
    version = candidate["version"]
    release = json.loads((directory / "release.json").read_text())
    require(release["schema_version"] == 1 and release["main_repository"] == f"https://github.com/{candidate['repository']}", "Release metadata identity mismatch")
    for key in ("version", "source_commit"):
        require(release[key] == candidate[key], "Release metadata mismatch")
    require(release["release_tag"] == candidate["tag"], "Release tag mismatch")
    artifact = f"Computer-MCP-{version}-universal.dmg"
    digest = candidate["assets"][artifact]["sha256"]
    require(f"{digest}  {artifact}" in (directory / "SHA256SUMS").read_text().splitlines(), "DMG checksum mismatch")
    provenance = json.loads((directory / f"Computer-MCP-{version}-universal-ArtifactProvenance.json").read_text())
    require(provenance["schema_version"] == 1, "Unsupported artifact provenance")
    require(provenance["artifact"]["sha256"] == digest and provenance["artifact"]["name"] == artifact, "DMG provenance mismatch")
    require(provenance["release"] == {"tag": candidate["tag"], "commit": candidate["source_commit"]}, "DMG source mismatch")
    for kind in ("app", "dmg"):
        notary = json.loads((directory / f"Computer-MCP-{version}-{kind.upper() if kind == 'dmg' else 'App'}Notary.json").read_text())
        require(notary["status"] == "Accepted" and notary["id"] == provenance["notarization"][kind]["submission_id"], "Notarization receipt mismatch")
        require(provenance["notarization"][kind]["state"] == "accepted" and provenance["stapling"][kind] == "validated", "DMG or app is not notarized and stapled")


def prepare(output):
    updates = []
    for source in json.loads((ROOT / "release-sources.json").read_text()):
        repository, name = source["repository"], source["name"]
        require(repository == f"computer-mcp/{name}", "Distribution source must match its reviewed product")
        pages = json.loads(subprocess.check_output(["gh", "api", "--paginate", "--slurp", f"repos/{repository}/releases?per_page=100"], text=True, timeout=120))
        release = select_release([item for page in pages for item in page], source["allow_prereleases"])
        if release is None:
            continue
        version, tag = release["tag_name"][1:], release["tag_name"]
        candidate = {**source, "version": version, "tag": tag, "release_id": release["id"], "source_commit": source_commit(repository, tag),
                     "assets": {asset: asset_record(repository, release, asset) for asset in asset_names(name, version)}}
        current_path = ROOT / "Metadata" / f"{name}.json"
        current = json.loads(current_path.read_text()) if current_path.exists() else None
        if not verify_identity(current, candidate):
            continue
        with tempfile.TemporaryDirectory(prefix="tap-release-assets-") as temporary:
            directory = Path(temporary)
            command = ["gh", "release", "download", tag, "--repo", repository, "--dir", str(directory)]
            for asset in candidate["assets"]:
                command.extend(["--pattern", asset])
            subprocess.run(command, check=True, timeout=600)
            for asset, record in candidate["assets"].items():
                file = directory / asset
                require(file.stat().st_size == record["size"] and digest(file) == record["sha256"], "Downloaded release asset differs from GitHub's digest")
            if name == "apple-cli":
                validate_apple_cli(candidate, directory)
            else:
                validate_computer_mcp(candidate, directory)
        updates.append(candidate)
    output.mkdir(parents=True, exist_ok=True)
    (output / "updates.json").write_text(json.dumps(updates, indent=2, sort_keys=True) + "\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
            stream.write(f"changed={'true' if updates else 'false'}\n")
    print(f"Validated {len(updates)} distribution updates")


def distribution_path(candidate):
    return ROOT / ("Formula" if candidate["kind"] == "formula" else "Casks") / f"{candidate['name']}.rb"


def render(candidate):
    name, kind = candidate["name"], candidate["kind"]
    version_key(candidate["version"])
    require((name, kind) in {("apple-cli", "formula"), ("computer-mcp", "cask")}, "Unreviewed distribution")
    require(re.fullmatch(r"[a-z][a-z0-9_]*", candidate["macos_symbol"]), "Invalid macOS symbol")
    record = candidate["assets"][asset_names(name, candidate["version"])[0]]
    text = (ROOT / "Scripts/templates" / f"{name}.rb.in").read_text()
    for key, value in {"URL": record["url"], "VERSION": candidate["version"], "SHA256": record["sha256"], "MACOS_SYMBOL": candidate["macos_symbol"]}.items():
        text = text.replace(f"@{key}@", value)
    require(re.search(r"@[A-Z0-9_]+@", text) is None, "Unrendered template field")
    return text


def stage(directory):
    for candidate in json.loads((directory / "updates.json").read_text()):
        text = render(candidate)
        floor = subprocess.check_output(["brew", "ruby", "-e", 'require "macos_version"; puts MacOSVersion::SYMBOLS.fetch(ARGV.fetch(0).to_sym)', "--", candidate["macos_symbol"]], text=True, timeout=30).strip()
        require(floor.split(".")[0] == candidate["minimum_macos"], "Homebrew platform floor does not match its reviewed policy")
        destination = distribution_path(candidate)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text)
        metadata = ROOT / "Metadata" / f"{candidate['name']}.json"
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n")


def check():
    for file in sorted((ROOT / "Metadata").glob("*.json")):
        current = json.loads(file.read_text())
        repository, tag = current["repository"], current["tag"]
        release = api(f"repos/{repository}/releases/tags/{tag}")
        require(not release["draft"] and release["published_at"], "Distribution release is no longer published")
        candidate = {**current, "release_id": release["id"], "source_commit": source_commit(repository, tag),
                     "assets": {name: asset_record(repository, release, name) for name in current["assets"]}}
        require(not verify_identity(current, candidate), "Distribution identity changed")
    print("Published distribution identities match")


def propose(branch, root=ROOT):
    # master accepts only signed commits. GitHub signs commits that the job token creates through
    # its API, so the staged files become GitHub objects instead of a local commit.
    repository = os.environ["GITHUB_REPOSITORY"]
    parent = git(root, "rev-parse", "HEAD").decode().strip()
    base, tree = git(root, "rev-parse", "HEAD^{tree}").decode().strip(), git(root, "write-tree").decode().strip()
    if tree == base:
        return None
    existing = git(root, "ls-remote", "origin", f"refs/heads/{branch}").decode().split()
    if existing:
        current = api(f"repos/{repository}/git/commits/{existing[0]}")
        if current["tree"]["sha"] == tree and current["verification"]["verified"]:
            print(f"These distribution files are already proposed on {branch}.")
            return None
    fields = git(root, "diff-index", "--cached", "--no-renames", "-z", "HEAD").decode().split("\0")
    changes = [(header[1:].split(), path) for header, path in zip(fields[0:-1:2], fields[1::2])]
    require(all(path.split("/")[0] in ("Formula", "Casks", "Metadata") for _, path in changes), "Only distribution files may be proposed")
    entries, updates = [], []
    for (old_mode, mode, _, blob, status), path in changes:
        if status == "D":
            entries.append({"path": path, "mode": old_mode, "type": "blob", "sha": None})
            continue
        data = git(root, "cat-file", "blob", blob)
        created = write("POST", f"repos/{repository}/git/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"})
        require(created["sha"] == blob, "GitHub stored different distribution bytes")
        entries.append({"path": path, "mode": mode, "type": "blob", "sha": blob})
        if path.startswith("Metadata/"):
            metadata = json.loads(data)
            updates.append(f"{metadata['name']} to {metadata['version']}")
    require(updates, "A distribution proposal must update its metadata")
    title = "Update " + ", ".join(updates)
    created = write("POST", f"repos/{repository}/git/trees", {"base_tree": base, "tree": entries})
    require(created["sha"] == tree, "GitHub built a different distribution tree")
    commit = write("POST", f"repos/{repository}/git/commits", {"message": title, "tree": tree, "parents": [parent]})
    require(commit["tree"]["sha"] == tree and [item["sha"] for item in commit["parents"]] == [parent], "GitHub created a different distribution commit")
    require(commit["verification"]["verified"] is True, "GitHub did not sign the distribution proposal")
    if existing:
        write("PATCH", f"repos/{repository}/git/refs/heads/{branch}", {"sha": commit["sha"], "force": True})
    else:
        write("POST", f"repos/{repository}/git/refs", {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
            stream.write(f"title={title}\n")
    print(f"Proposed {commit['sha']} on {branch}: {title}")
    return commit["sha"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "stage", "check", "propose"))
    parser.add_argument("--directory", type=Path, default=ROOT / ".candidate")
    parser.add_argument("--branch", default="automation/distribution-updates")
    options = parser.parse_args()
    if options.mode == "prepare":
        prepare(options.directory.resolve())
    elif options.mode == "stage":
        stage(options.directory.resolve())
    elif options.mode == "propose":
        propose(options.branch)
    else:
        check()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(f"release update: {error}") from None
