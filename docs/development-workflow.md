# Development workflow

- `main` is production-ready.
- `develop` is staging/integration.
- `feature/*` branches contain focused work.

Normal flow is `feature/*` → pull request to `develop` → review and tests → merge to `develop`.
A later, separate pull request promotes `develop` to `main`. Deployment is always a separate,
explicit action; merging does not imply deployment.

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
