# Releasing foliot

Publishing is intentionally automated through GitHub Actions and PyPI Trusted
Publishing. No long-lived PyPI token belongs in the repository.

## One-time PyPI setup

Before the first release, create a pending trusted publisher for the future
`foliot` project with these values:

- PyPI project name: `foliot`
- GitHub owner: `wordedword75049`
- GitHub repository: `foliot`
- Workflow filename: `release.yml`
- Environment name: `pypi`

Also create the `pypi` environment in the GitHub repository settings. See
[PyPI's Trusted Publishers guide](https://docs.pypi.org/trusted-publishers/).

## Prepare a release

1. Choose the version and update `project.version` in `pyproject.toml`.
2. Move the release notes out of `Unreleased` in `CHANGELOG.md` and add the
   release date.
3. Run the complete checks:

   ```console
   uv sync --locked
   uv run ruff format --check src tests examples
   uv run ruff check src tests examples
   uv run basedpyright
   uv run pytest
   uv build --no-sources
   ```

4. Inspect both files under `dist/` and install the wheel in a fresh virtual
   environment as a smoke test.
5. Commit the release preparation and push it to `main`.

## Publish

Create and publish a GitHub release whose tag exactly matches the package
version with a `v` prefix, such as `v0.1.0`. Publishing the GitHub release
triggers `.github/workflows/release.yml`, which repeats all checks, builds the
artifacts, and sends them to PyPI through the trusted publisher.

PyPI versions are immutable. If publishing fails after a file was accepted,
fix the problem under a new version rather than trying to replace the file.

