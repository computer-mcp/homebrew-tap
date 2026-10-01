#!/usr/bin/env python3
"""Validate product release inputs and stage Homebrew distribution updates."""

import argparse
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


def validate_apple_cli(candidate, directory):
    repository, version, tag = candidate["repository"], candidate["version"], candidate["tag"]
    package = f"apple-cli-{version}-macos-arm64"
    manifest = json.loads((directory / (package + ".provenance.json")).read_text())
    receipt = json.loads((directory / (package + ".verification.json")).read_text())
    require(receipt["ci"]["repository"] == repository, "Acceptance belongs to another repository")
    require(str(receipt["ci"]["run_id"]).isdigit(), "Invalid acceptance run")
    require(str(receipt["ci"]["run_attempt"]).isdigit(), "Invalid acceptance attempt")
    run = api(f"repos/{repository}/actions/runs/{receipt['ci']['run_id']}/attempts/{receipt['ci']['run_attempt']}")
    require(run["status"] == "completed" and run["conclusion"] == "success", "Release workflow is not accepted")
    require(run["head_sha"] == candidate["source_commit"] and run["path"] == ".github/workflows/release.yml", "Acceptance workflow identity mismatch")
    require(str(run["run_attempt"]) == receipt["ci"]["run_attempt"], "Acceptance attempt mismatch")
    require(receipt["ci"]["run_url"] == run["html_url"], "Acceptance URL mismatch")
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


def stage(directory):
    for candidate in json.loads((directory / "updates.json").read_text()):
        name, kind = candidate["name"], candidate["kind"]
        version_key(candidate["version"])
        require((name, kind) in {( "apple-cli", "formula"), ("computer-mcp", "cask")}, "Unreviewed distribution")
        asset = asset_names(name, candidate["version"])[0]
        record = candidate["assets"][asset]
        destination = ROOT / ("Formula" if kind == "formula" else "Casks") / f"{name}.rb"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            subprocess.run(["brew", "trust", f"--{kind}", f"computer-mcp/tap/{name}"], check=True, timeout=30)
            subprocess.run(["brew", f"bump-{kind}-pr", "--write-only", "--no-audit", f"--version={candidate['version']}", f"--url={record['url']}", f"--sha256={record['sha256']}", f"computer-mcp/tap/{name}"], check=True, timeout=600)
        else:
            template = (ROOT / "Scripts/templates" / f"{name}.rb.in").read_text()
            for key, value in {"URL": record["url"], "VERSION": candidate["version"], "SHA256": record["sha256"], "MACOS_SYMBOL": candidate["macos_symbol"]}.items():
                template = template.replace(f"@{key}@", value)
            destination.write_text(template)
        metadata = ROOT / "Metadata" / f"{name}.json"
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "stage", "check"))
    parser.add_argument("--directory", type=Path, default=ROOT / ".candidate")
    options = parser.parse_args()
    if options.mode == "prepare":
        prepare(options.directory.resolve())
    elif options.mode == "stage":
        stage(options.directory.resolve())
    else:
        check()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(f"release update: {error}") from None
