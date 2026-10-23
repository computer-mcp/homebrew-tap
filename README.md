<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/assets/logo/logo-dark.png">
    <img src=".github/assets/logo/logo-light.png" width="128" height="128" alt="">
  </picture>
</p>

<h1 align="center">Homebrew Tap</h1>

<p align="center">Homebrew formulae and casks for Computer MCP and Apple CLI.</p>

<p align="center">
  <a href="https://github.com/computer-mcp/homebrew-tap/actions/workflows/tests.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/computer-mcp/homebrew-tap/tests.yml?branch=master&label=tests&style=flat"></a>
  <a href="LICENSE"><img alt="License: Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue?style=flat"></a>
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#how-packages-are-updated">Updates</a> ·
  <a href="#add-a-product">Add a product</a> ·
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

## Overview

The organization's Homebrew tap, created with `brew tap-new`. It installs the original release files of Computer MCP and Apple
CLI, generated from each product's verified releases. Product repositories own their versions,
release artifacts and compatibility contracts; plugins and Swift SDKs use their own distribution
channels.

## Quick look

| Package | Kind | Source | Minimum macOS |
| --- | --- | --- | --- |
| `computer-mcp` | Cask | [computer-mcp/computer-mcp](https://github.com/computer-mcp/computer-mcp) | 14 |
| `apple-cli` | Formula | [computer-mcp/apple-cli](https://github.com/computer-mcp/apple-cli) | 27 |

## Install

Nothing is installable yet. The packages appear once the 0.1.0 product releases are published
and accepted by the release update workflow.

## How packages are updated

The release update workflow checks published releases every hour and can also run manually. It
verifies source tags, artifact checksums and product acceptance records, updates existing
packages with `brew bump-formula-pr --write-only` or `brew bump-cask-pr --write-only`, and runs
the official `brew test-bot` checks and installation verification before it pushes an update.
Stable releases take precedence; Apple CLI prereleases are eligible while it has no stable
release. A source with no eligible published release produces no update. `Metadata/` records the
accepted distribution inputs.

CI verifies the Apple CLI executable's signature and bytes, and the Computer MCP app's version,
signature and notarization. Formula and cask pull requests run the same installation checks.
Dependabot opens weekly GitHub Actions update pull requests; review them and their CI results
before merging.

## Add a product

Review the product's release acceptance contract, add its source to `release-sources.json`, and
implement its validation and package template in `scripts/`. `Formula/` holds CLI packages and
`Casks/` holds desktop application packages. Validate with:

```sh
python3 -B -m unittest discover -s tests
brew test-bot --only-tap-syntax --tap=computer-mcp/tap
```

## Contributing

See the [contributor guide](CONTRIBUTING.md).

## License

[Apache License 2.0](LICENSE).
