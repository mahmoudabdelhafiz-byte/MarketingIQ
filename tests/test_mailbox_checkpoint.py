from __future__ import annotations

from sqlalchemy import select

from marketingiq.domain.engagement import MailboxEngagementCheckpoint
from marketingiq.infrastructure.mailbox_checkpoint import CheckpointedIMAPMailboxReader


class FakeIMAPClient:
    def __init__(self, messages: dict[int, bytes], uid_validity: str = "100") -> None:
        self.messages = messages
        self.uid_validity = uid_validity
        self.fail_uid: int | None = None

    def login(self, username, password):
        return "OK", [b""]

    def select(self, mailbox, readonly=True):
        return "OK", [str(len(self.messages)).encode()]

    def response(self, name):
        assert name == "UIDVALIDITY"
        return "UIDVALIDITY", [self.uid_validity.encode()]

    def uid(self, command, *args):
        if command == "search":
            payload = b" ".join(str(uid).encode() for uid in sorted(self.messages))
            return "OK", [payload]
        if command == "fetch":
            uid = int(args[0])
            if uid == self.fail_uid:
                return "NO", []
            raw = self.messages[uid]
            return "OK", [(b"RFC822", raw)]
        raise AssertionError(f"unexpected command: {command}")

    def logout(self):
        return "BYE", [b""]


def _reader(session, client: FakeIMAPClient) -> CheckpointedIMAPMailboxReader:
    reader = CheckpointedIMAPMailboxReader(
        session,
        host="imap.example.test",
        username="marketing@example.test",
        password="secret",
        mailbox="INBOX",
    )

    def connect():
        return client

    reader._connect = connect
    return reader


def test_checkpoint_bootstraps_latest_then_drains_new_messages_without_gaps(session):
    client = FakeIMAPClient({uid: f"message-{uid}".encode() for uid in range(1, 6)})
    reader = _reader(session, client)

    first = reader.fetch_messages(2)
    assert [item.uid for item in first] == ["4", "5"]

    checkpoint = session.scalar(select(MailboxEngagementCheckpoint))
    assert checkpoint is not None
    assert checkpoint.uid_validity == "100"
    assert checkpoint.last_processed_uid == 5

    client.messages.update({6: b"message-6", 7: b"message-7", 8: b"message-8"})
    second = reader.fetch_messages(2)
    assert [item.uid for item in second] == ["6", "7"]
    assert checkpoint.last_processed_uid == 7

    third = reader.fetch_messages(2)
    assert [item.uid for item in third] == ["8"]
    assert checkpoint.last_processed_uid == 8


def test_uidvalidity_change_resets_cursor_and_bootstraps_new_mailbox_generation(session):
    client = FakeIMAPClient({10: b"old-10", 11: b"old-11"}, uid_validity="100")
    reader = _reader(session, client)
    assert [item.uid for item in reader.fetch_messages(10)] == ["10", "11"]

    client.uid_validity = "200"
    client.messages = {1: b"new-1", 2: b"new-2", 3: b"new-3"}
    reset = reader.fetch_messages(2)

    assert [item.uid for item in reset] == ["2", "3"]
    checkpoint = session.scalar(select(MailboxEngagementCheckpoint))
    assert checkpoint is not None
    assert checkpoint.uid_validity == "200"
    assert checkpoint.last_processed_uid == 3


def test_fetch_failure_does_not_advance_checkpoint_past_failed_uid(session):
    client = FakeIMAPClient({1: b"one", 2: b"two"})
    reader = _reader(session, client)
    assert [item.uid for item in reader.fetch_messages(10)] == ["1", "2"]

    client.messages.update({3: b"three", 4: b"four"})
    client.fail_uid = 3
    assert reader.fetch_messages(10) == []

    checkpoint = session.scalar(select(MailboxEngagementCheckpoint))
    assert checkpoint is not None
    assert checkpoint.last_processed_uid == 2

    client.fail_uid = None
    assert [item.uid for item in reader.fetch_messages(10)] == ["3", "4"]
    assert checkpoint.last_processed_uid == 4


def test_checkpoint_persists_only_hashed_mailbox_identity(session):
    client = FakeIMAPClient({1: b"one"})
    reader = _reader(session, client)
    reader.fetch_messages(1)

    checkpoint = session.scalar(select(MailboxEngagementCheckpoint))
    assert checkpoint is not None
    assert checkpoint.mailbox_identity == reader.identity
    assert checkpoint.mailbox_identity != "marketing@example.test"
    assert "imap.example.test" not in checkpoint.mailbox_identity
    assert len(checkpoint.mailbox_identity) == 24
