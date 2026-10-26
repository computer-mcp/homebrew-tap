---
description: Keep the computer-mcp tap's formulae, casks and metadata tied to published releases.
---
# Tap Agent Guide

Read `README.md` before changing this repository.

- Product repositories own release versions and artifacts. Formula and Cask
  files own Homebrew installation, `release-sources.json` owns distribution
  scope and reviewed platform floors, and `Metadata/` holds derived accepted
  release inputs.
- Keep generated package metadata consistent with the published release.
  Preserve release files and executable signatures during installation. Never
  use drafts or local development archives as release inputs.
- Use Homebrew's Formula and Cask conventions and its official test-bot
  workflow. Validate updater behavior with Python standard-library tests, and
  real packages on ephemeral GitHub Actions runners.
