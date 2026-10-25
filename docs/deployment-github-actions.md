# Deploying from GitHub Actions

The `Deploy` workflow (`.github/workflows/deploy.yml`) updates the cPanel shared-hosting site over
SSH. The SSH key lives only in GitHub's encrypted secrets and the database credentials only on the
server, so no person or tool needs the cPanel password.

## One-time setup

1. **Dedicated deploy key.** In cPanel open *SSH Access → Manage SSH Keys → Generate a New Key*
   (name it `github-deploy`, no passphrase), then *Manage → Authorize*. Download the **private**
   key. A separate key can be revoked without touching your personal one.
2. **GitHub secrets.** Open *Settings → Secrets and variables → Actions* and add:
   - `SSH_HOST`: the server host name (from cPanel's sidebar or your hosting welcome email)
   - `SSH_USER`: the cPanel user name
   - `SSH_PORT`: only if it is not 22
   - `SSH_PRIVATE_KEY`: the whole private key, including the `BEGIN` and `END` lines
   - `SSH_KNOWN_HOSTS` (recommended): the output of `ssh-keyscan -p PORT HOST`, run once from a
     computer you trust. This pins the server's identity.
3. **GitHub variables** (same page, *Variables* tab, all optional):
   - `APP_ROOT`: defaults to `~/marketingiq`
   - `APP_VENV`: the cPanel virtualenv `bin/activate`; auto-detected when blank
   - `APP_URL`: `https://marketingiq.barmageyat.net`, which enables health checks
4. **Server env file.** Migrations run from SSH, which does not see the variables set in cPanel's
   Python App page. Create `~/.marketingiq/env` with *File Manager* (show hidden files), containing
   the same production values as the Python App, for example:

   ```text
   APP_ENV=production
   DATABASE_URL=mysql+pymysql://USER:PASSWORD@localhost:3306/DATABASE?charset=utf8mb4
   AUTH_SECRET=...
   DATABASE_BACKUP_STRATEGY=provider_managed
   DATABASE_BACKUP_RETENTION_DAYS=7
   DATABASE_RESTORE_TEST_DATE=YYYY-MM-DD
   ```

   Then set its permissions to `600`. The check step lists only the variable **names** it finds,
   never their values.
5. **Git checkout.** The app root must be a git clone of this repository (the check reports whether
   it is). If the files were uploaded manually, clone the repository once and move cPanel's app
   folder to it.

## Running it

- *Actions → Deploy → Run workflow*, mode **test**: connects and reports the server state
  read-only (user, tools, app root, git commit, virtualenv, installed version, env file names),
  then runs health checks.
- Mode **deploy**, from `main`:
  1. fast-forwards the server checkout to `main`
  2. runs `pip install .`
  3. runs the deployment preflight
  4. backs up the database to `~/.marketingiq/backups` (keeps 10)
  5. runs `alembic upgrade head` and verifies the schema
  6. restarts Passenger (`tmp/restart.txt`) and runs health checks

  If any step before the restart fails, the previous commit and package are restored and the site
  keeps serving the old version. Results appear as annotations on the run summary.

A database migration that already ran is **not** reverted automatically; restore from the backup
file if a release must be rolled back past a migration.
