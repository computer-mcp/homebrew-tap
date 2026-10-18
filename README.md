# Computer MCP Homebrew Tap

The organization tap for Computer MCP formulae and casks.

## Install

There are no installable formulae or casks until the 0.1.0 product releases
are published and accepted by the release update workflow. The workflow
generates packages from those verified releases.

Plugins and Swift SDKs use their own distribution channels.

## Maintenance

This repository was created with `brew tap-new`. `Formula/` owns CLI packages;
`Casks/` owns desktop application packages. Product repositories own their
versions, release artifacts and compatibility contracts.

The release update workflow checks published releases hourly and can also run
manually. It verifies source tags, artifact checksums and product acceptance
records, uses `brew bump-formula-pr --write-only` or
`brew bump-cask-pr --write-only` for existing packages, and runs the official
`brew test-bot` checks plus installation verification before pushing an update.
Stable releases take precedence; Apple CLI prereleases are eligible when
there is no stable release. Sources with no eligible published release produce
no updates. `Metadata/` records the accepted distribution inputs.

The packages install original release files. CI verifies executable signatures
and bytes for Apple CLI, and the app version, signature and notarization for
Computer MCP. Formula and Cask pull requests run the same installation checks.

Dependabot opens weekly GitHub Actions update pull requests. Review changes and
their CI results before merging.

To add a product, review its release acceptance contract, add its source to
`release-sources.json`, and implement the corresponding validation and package
template in `Scripts/`. Validate with:

```sh
python3 -B -m unittest discover -s Tests
brew test-bot --only-tap-syntax --tap=computer-mcp/tap
```
