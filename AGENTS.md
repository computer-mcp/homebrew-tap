# Tap Agent Guide

Read README.md before changing this repository.

Product repositories own release versions and artifacts. Formula and Cask files
own Homebrew installation. `release-sources.json` owns distribution scope and
reviewed platform floors. `Metadata/` contains derived accepted release inputs.

Before changing reusable files, name the behavior, cause, invariant, owner,
data flow and validation. Keep generated package metadata consistent with the
published release. Preserve release files and executable signatures during
installation. Never use drafts or local development archives as release inputs.

Use Homebrew's Formula/Cask conventions and its official test-bot workflow.
Validate updater behavior with Python standard-library tests, and validate real
packages on ephemeral GitHub Actions runners. Keep local execution evidence in
ignored `.agent/`. Keep machine paths, credentials and personal data out of
committed files. The default branch is master.

Change master only through squash-merged pull requests with passing `test-bot`
checks. Never push to master directly, merge by rebase, or bypass the ruleset.
Automated distribution updates follow the same path.
