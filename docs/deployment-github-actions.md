# Deploying from GitHub Actions

The `Deploy` workflow (`.github/workflows/deploy.yml`) updates the cPanel shared-hosting site over
SSH. The SSH key lives only in GitHub's encrypted secrets. Production settings stay in cPanel's
*Setup Python App* page and are read on the server, so no person or tool needs the cPanel password
and no settings file has to be maintained.

## One-time setup

1. **Deploy key.** In cPanel open *SSH Access → Manage SSH Keys → Generate a New Key*, then
   *Manage → Authorize*. Keep the private key.
2. **GitHub secrets** in *Settings → Secrets and variables → Actions → Repository secrets* (in this
   repository, not another one):
   - `SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY` (the whole key, including the `BEGIN`/`END` lines)
   - `SSH_KEY_PASSPHRASE` if the key has a passphrase, and `SSH_PORT` if it is not 22
   - `SSH_KNOWN_HOSTS` (recommended): the output of `ssh-keyscan -p PORT HOST` from a trusted
     computer, which pins the server's identity
3. **Optional variables** (*Variables* tab): `APP_ROOT` and `APP_VENV` are detected from cPanel's
   virtualenv layout; `APP_URL` defaults to `MARKETINGIQ_PUBLIC_URL` from the cPanel settings.

## Modes

Run *Actions → Deploy → Run workflow* with **Use workflow from: main**:

- **test**: read-only report. Shows the server user, app folder, deployed commit, cPanel setting
  *names*, database engine and schema state, health endpoints, and whether server-only files
  (`.git`, logs, zips, old `.htaccess` copies) are downloadable from the website.
- **bootstrap-deploy**: the first deploy of a folder that was uploaded by hand. It snapshots the
  folder to `~/.marketingiq/backups/app-*.tgz`, links it to GitHub, writes `main`'s files and then
  continues like a normal deploy. The git metadata is kept in `~/.marketingiq/app.git`, outside the
  web folder. Untracked server files (`.htaccess`, uploads, logs) are never touched.
- **deploy**: every later release. It refuses to run if tracked files were edited on the server
  since the last deploy, so nothing gets overwritten silently.

Each deploy then:

1. runs `pip install .` in the cPanel virtualenv
2. runs the production preflight with the cPanel settings
3. backs up the database to `~/.marketingiq/backups/db-*.sql.gz` (the last 10 are kept)
4. runs `alembic upgrade head` and verifies the schema
5. restarts Passenger (`tmp/restart.txt`) and waits for `/health/ready`

## Failure handling

- **Failure before the restart:** the previous code (or, on bootstrap, the snapshot) is restored
  and the running site is not restarted.
- **Unhealthy after the restart, no migration ran:** the previous code is restored and Passenger
  is restarted again.
- **Unhealthy after a migration ran:** the run fails and names the database backup to restore.
  Migrations are never reverted automatically.

Results appear as annotations on the run summary page. Pushing to an `ops/**` branch runs the
read-only check, so pipeline changes can be tested before they are merged.
