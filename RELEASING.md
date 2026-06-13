# Releasing

Each package publishes to PyPI independently via **Trusted Publishing** (OIDC) —
no API tokens are stored anywhere. Publishing is driven by the
[`Publish`](.github/workflows/publish.yml) workflow, triggered by a git tag.

## 1. One-time PyPI setup (per package)

Trusted Publishing must be authorized on PyPI **once per project**. Because these
projects don't exist on PyPI yet, use **pending publishers**.

Sign in to PyPI → **Account settings → Publishing → Add a new pending publisher**
(<https://pypi.org/manage/account/publishing/>) and create **one entry per package**
with these exact values:

| Field | Value |
|-------|-------|
| PyPI Project Name | _the distribution name_ (see list below) |
| Owner | `vigilancetrent` |
| Repository name | `truststack` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

Distribution names (one pending publisher each):

- `truststack-core`
- `truststack-agent-clock`
- `truststack-shipped-or-not`
- `truststack-task-dedupe`
- `truststack-entity-canon`
- `truststack-meta-token-vault`

> After a project's first successful publish, its pending publisher becomes a
> normal trusted publisher automatically — no further PyPI changes are needed for
> subsequent releases.

## 2. Cut a release

> Publish **`truststack-core` first** — every other package depends on it, so it
> must exist on PyPI before a leaf package can be installed.

1. Bump the version in the package's `pyproject.toml`
   (e.g. `packages/truststack-core/pyproject.toml` → `version = "0.1.0"`).
2. Commit it to `main`.
3. Tag and push — the tag is **`<dist-name>-v<version>`**:

   ```bash
   git tag truststack-core-v0.1.0
   git push origin truststack-core-v0.1.0
   ```

The `Publish` workflow then:

- resolves the package directory from the tag,
- **verifies the tag version matches the package's declared version** (fails otherwise),
- builds the sdist + wheel with `uv build --package <dist-name>`,
- uploads to PyPI via `pypa/gh-action-pypi-publish` using the OIDC trusted publisher.

Repeat per package, e.g. `truststack-agent-clock-v0.1.0`,
`truststack-shipped-or-not-v0.1.0`, etc.

## Notes

- A tag whose version doesn't match the package's `pyproject.toml` version is
  rejected before any upload, preventing accidental mismatched releases.
- The `pypi` GitHub Environment guards the publish job; add required reviewers in
  **Settings → Environments → pypi** if you want a manual approval gate before each release.
- Versions are immutable on PyPI — to fix a bad release, bump the version and tag again.
