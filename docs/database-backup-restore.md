# Database backup and restore readiness

MarketingIQ must have a verified database recovery path before a production deployment or schema
migration. The application does not silently create backups because shared-hosting providers differ
in shell access, snapshot/export tooling, retention, and restore capabilities.

## Deployment gate

Production preflight requires these non-secret settings:

```text
DATABASE_BACKUP_STRATEGY=provider_managed
DATABASE_BACKUP_RETENTION_DAYS=7
DATABASE_RESTORE_TEST_DATE=2026-09-01
```

`DATABASE_BACKUP_STRATEGY` must be either:

- `provider_managed`: the hosting provider performs scheduled database backups/snapshots and
  exposes a documented restore path.
- `operator_managed`: the operator owns database export, secure storage, retention, and restore
  execution.

The retention value must be between 1 and 3650 days. `DATABASE_RESTORE_TEST_DATE` must use
`YYYY-MM-DD` and cannot be in the future. These values record an operational decision; the
preflight cannot prove that a provider backup actually exists or that an operator archive is valid.

## Required evidence before first production deployment

Record outside the repository:

1. the selected backup strategy;
2. where backups are stored or where the hosting-provider backup control is located;
3. the effective retention period;
4. who can initiate a restore;
5. the date of the latest restore rehearsal;
6. the database name and Alembic revision captured by the rehearsal;
7. the release or commit used to validate the restored copy.

Do not store database passwords, backup encryption keys, provider tokens, or backup archives in Git.

## Provider-managed strategy

Confirm in the hosting control panel or provider documentation that:

- the MarketingIQ database is included;
- the schedule is appropriate for the expected recovery-point objective;
- retention meets the declared `DATABASE_BACKUP_RETENTION_DAYS`;
- a restore can target a separate database or otherwise be tested without overwriting production;
- access to backup/restore controls is restricted to authorized operators.

A provider statement that backups are enabled is not sufficient by itself. Perform at least one
restore rehearsal to a disposable database before first go-live and record the successful date in
`DATABASE_RESTORE_TEST_DATE`.

## Operator-managed strategy

If the host provides shell/database-client access, an operator-managed logical backup may use
`mysqldump` or an equivalent MySQL 8-compatible export mechanism. Never place the database
password directly on the command line or commit it to a script. Prefer a provider credential
facility, protected MySQL option file, or other secret mechanism supported by the host.

A representative logical export shape is:

```bash
mysqldump --single-transaction --routines --triggers --events --hex-blob \
  --default-character-set=utf8mb4 DATABASE_NAME > marketingiq-backup.sql
```

This is a runbook example, not an automated production command. Adjust it for the hosting provider
and verify that the account has only the permissions required for backup/restore.

## Restore rehearsal

Restore into a separate disposable database whenever possible. Do not rehearse by overwriting the
production database.

For a logical dump, a representative restore shape is:

```bash
mysql RESTORE_DATABASE_NAME < marketingiq-backup.sql
```

Before the backup, record the current Alembic revision:

```sql
SELECT version_num FROM alembic_version;
```

After restoring, confirm that the restored database contains the same recorded revision and expected
application data. Validate it with the matching application release. Do not automatically run newer
migrations merely to make a restore rehearsal look current; the purpose is to prove that the backup
can reproduce the captured state.

After a successful rehearsal, update `DATABASE_RESTORE_TEST_DATE` in the deployment environment
metadata. Do not commit environment secrets or the backup itself.

## Pre-migration backup

Immediately before an explicitly approved production `alembic upgrade head`:

1. stop or otherwise control writes if the selected backup method requires it;
2. create or confirm a fresh backup/snapshot;
3. record the backup timestamp and pre-migration Alembic revision;
4. confirm the backup is retained independently of the deployment checkout;
5. only then execute the migration.

## Rollback principles

Application-code rollback and database rollback are separate decisions. A newer schema may not be
backward-compatible with older code, and blindly running Alembic downgrade is not an assumed
recovery strategy.

If database restoration is required:

- stop application and scheduled-job writes;
- restore the chosen backup to a separate database first when practical;
- validate the restored revision and data;
- point the matching application release at the validated restored database during the controlled
  recovery;
- preserve the failed database until the incident is understood, unless storage/security policy
  requires otherwise.

Production restore execution is always an explicit operational action. Nothing in the application,
CI, deployment preflight, or migration command automatically restores data.
