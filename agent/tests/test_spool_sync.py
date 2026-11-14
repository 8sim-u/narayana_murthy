"""Synthetic spool/sync tests for offline durability; not run in this task."""

import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from activity_agent import Spool, Syncer  # noqa: E402


class _Response:
    def raise_for_status(self):
        return None


class _Session:
    def __init__(self, fail=False):
        self.fail = fail
        self.batches = []
        self.headers = []

    def post(self, _url, json, timeout, headers, allow_redirects=True):
        self.batches.append(list(json["records"]))
        self.headers.append(headers)
        if self.fail:
            raise OSError("synthetic offline network")
        return _Response()


def _record(index):
    return {"record_id": str(uuid.uuid4()), "sequence": index}


def test_sync_failure_keeps_spool_and_recovery_preserves_batch_order(tmp_path, monkeypatch):
    import activity_agent
    import consent

    # Syncer refuses to upload without workstation consent, so record it
    # for the configured employee before exercising the spool.
    marker = tmp_path / "consent.json"
    monkeypatch.setenv("WAMSS_CONSENT_FILE", str(marker))
    consent.record_consent(
        employee_id=activity_agent.EMPLOYEE_ID,
        os_user=consent.current_os_user(),
    )
    spool = Spool(str(tmp_path))
    records = [_record(i) for i in range(3)]
    for record in records:
        spool.append(record)

    # Loopback URL: sync_once only allows HTTPS or loopback HTTP.
    syncer = Syncer(spool, "http://127.0.0.1:8000", employee_token="sync-test-token")
    offline_session = _Session(fail=True)
    syncer.session = offline_session
    assert syncer.sync_once() is False
    assert spool.pending_count() == 3

    recovery_session = _Session()
    syncer.session = recovery_session
    assert syncer.sync_once() is True
    assert [row["record_id"] for row in recovery_session.batches[0]] == [
        row["record_id"] for row in records
    ]
    assert recovery_session.headers[0]["X-Employee-Token"] == "sync-test-token"
    assert spool.pending_count() == 0
    with open(spool.sent_path, encoding="utf-8") as sent_file:
        archived = [json.loads(line) for line in sent_file if line.strip()]
    assert [row["record_id"] for row in archived] == [row["record_id"] for row in records]


def test_stale_concurrent_acknowledgement_keeps_newer_unsent_rows(tmp_path):
    spool = Spool(str(tmp_path))
    first, second, later = _record(1), _record(2), _record(3)
    for record in (first, second, later):
        spool.append(record)

    first_batch = spool.read_batch(2)
    overlapping_batch = spool.read_batch(2)
    spool.mark_sent(first_batch)
    newest = _record(4)
    spool.append(newest)
    # The second uploader's acknowledgement refers to the original first two
    # IDs, not the next two lines now at the head of pending.jsonl.
    spool.mark_sent(overlapping_batch)

    pending = spool.read_batch(10)
    assert [row["record_id"] for row in pending] == [later["record_id"], newest["record_id"]]
