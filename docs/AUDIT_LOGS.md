# Activity audit log verification

Workstation activity records are stored in `activity_logs`. Each new record
stores the SHA-256 hash of its normalized event data and the hash of the
previous retained record. The database head is updated in the same transaction
as each batch, and concurrent batches are serialized at that head.

## Verify a database

Run from `backend/` with the same `DATABASE_URL` used by the application:

```bash
python scripts/verify_audit_chain.py
```

The command exits with status 0 for a valid chain, status 1 when it finds an
integrity failure, and status 2 when it cannot access or prepare the database.
On failure, it reports the first affected activity event ID and a short reason;
it does not print employee names, window titles, or event contents.

## Existing records and retention

On the first application startup after this feature is installed, existing
activity records with no chain hashes are linked in database ID order. This
creates a baseline from the records present at upgrade time; it cannot prove
that those older records were never changed before the upgrade. A partially
populated chain is not silently repaired.

The existing retention process can remove old activity rows. It normally
removes an older prefix, so verification accepts the first retained record as
the starting anchor. A retained record's content hash and each link after
that first record are still checked. Any additional gap is reported as a
broken link rather than being assumed to be an authorized purge.

## Security and privacy

This is tamper-evident, not immutable or cryptographically signed. A person who
can rewrite every activity row and the database head can recompute the chain.
It does not encrypt event data or expand what the application collects. The
existing employee notice, consent, privacy mode, and retention requirements
remain in force; the workstation agent continues to store input counts rather
than keystroke content.
