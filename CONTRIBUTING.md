# Contributing

Product repositories own published releases and acceptance records. This tap
owns installation templates in `scripts/templates/` and accepted distribution
inputs in `Metadata/`. Formulae and casks belong in `Formula/` and `Casks/`.

Run the updater tests and Homebrew syntax checks before opening a pull request:

```sh
python3 -B -m unittest discover -s tests
brew test-bot --only-tap-syntax --tap=computer-mcp/tap
actionlint
```

CI runs the Homebrew checks and verifies published package installations on
macOS. Its `validate` aggregate requires every `test-bot` matrix result to pass.
Release candidate verification preserves active runs and does not use caches.
Source policy checks run separately, including after pull request title edits.

Use Conventional Commit subjects and signed commits. Contributions are provided
under this repository's Apache-2.0 license.
