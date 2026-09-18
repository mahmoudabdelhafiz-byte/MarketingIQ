# Development workflow

- `main` is production-ready.
- `develop` is staging/integration.
- `feature/*` branches contain focused work.

Normal flow is `feature/*` → pull request to `develop` → review and tests → merge to `develop`.
A later, separate pull request promotes `develop` to `main`. The same hosted CI suite is required
for pull requests targeting either `develop` or `main`, and it also reruns after pushes to both
protected branches. Deployment is always a separate, explicit action; merging does not imply
deployment.

For this initially minimal repository, create and publish the integration branch once:

```bash
git switch main
git pull --ff-only
git switch -c develop
git push -u origin develop
```

Then branch with `git switch -c feature/<short-name> develop`, commit, push, and open a PR whose
base is `develop`. Protect `main` and `develop`, require passing checks and review, disallow force
pushes, and limit direct pushes. Never open routine feature work directly against `main`.

## Reproducible validation

Runtime and development dependencies have compatible upper/lower bounds in `pyproject.toml`.
`constraints/dev.txt` records the reviewed direct versions used by CI without replacing the
standard Python packaging workflow. Set `PIP_CONSTRAINT=constraints/dev.txt` before the normal
editable install to reproduce those direct selections. Upgrade the project metadata and
constraint together, then run the full suite.

GitHub Actions runs Python 3.12 against MySQL 8, followed by Ruff, byte compilation, pytest, the
frozen baseline guard, a full Alembic `upgrade head`, and schema-readiness verification against a
disposable MySQL database. The workflow runs on feature pushes, `develop` and `main` pushes, and
pull requests targeting either protected branch. `TEST_DATABASE_URL` enables tests marked
`mysql`; without it those retained integration tests are explicitly skipped rather than silently
using SQLite.

The Codex workspace's install failure is environmental: pip has no custom project index
configuration, but its HTTPS traffic is routed through the workspace proxy, which returns HTTP
403 while pip's isolated build environment requests Hatchling. No untrusted mirror or bypass is
approved; CI uses the default package infrastructure supplied by GitHub-hosted runners.
