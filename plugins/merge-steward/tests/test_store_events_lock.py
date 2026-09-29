import os
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

from helpers import RepoTestCase

from steward import events, lock, store
from steward.errors import StewardError


def req(rid, branch="claude/a", head="h1", submitted="2026-09-29T10:00:00+00:00"):
    return {"id": rid, "branch": branch, "head": head, "submittedAt": submitted, "issues": [3]}


class StoreTest(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.r = self.repo()
        self.r.ensure_dirs()

    def test_save_list_move_locate(self):
        store.save(self.r, "queue", req("b", submitted="2026-09-29T10:00:02+00:00"))
        store.save(self.r, "queue", req("a"))
        self.assertEqual([x["id"] for x in store.list_requests(self.r, "queue")], ["a", "b"])
        moved = store.move(self.r, "a", "queue", "processing", claimedAt="now")
        self.assertEqual(moved["claimedAt"], "now")
        self.assertEqual(store.locate(self.r, "a"), ("processing", moved))
        self.assertEqual(store.locate(self.r, "claude/a")[1]["id"], "b")
        self.assertIsNone(store.locate(self.r, "nope"))

    def test_move_missing_src_raises_steward_error(self):
        with self.assertRaisesRegex(StewardError, "not in queue"):
            store.move(self.r, "ghost", "queue", "processing")

    def test_move_applies_updates_and_removes_src(self):
        store.save(self.r, "queue", req("a"))
        moved = store.move(self.r, "a", "queue", "done", result="ok")
        self.assertEqual(moved["result"], "ok")
        self.assertFalse(os.path.exists(store.req_path(self.r, "queue", "a")))
        self.assertEqual(store.read_json(store.req_path(self.r, "done", "a"))["result"], "ok")

    def test_temp_files_are_ignored(self):
        open(self.r.path("queue", ".x.json.1.tmp"), "w").close()
        self.assertEqual(store.list_requests(self.r, "queue"), [])

    def test_list_requests_skips_corrupt_files(self):
        store.save(self.r, "queue", req("a"))
        with open(store.req_path(self.r, "queue", "broken"), "w") as f:
            f.write("{not json")
        with open(store.req_path(self.r, "queue", "list"), "w") as f:
            f.write("[1, 2]")
        self.assertEqual([x["id"] for x in store.list_requests(self.r, "queue")], ["a"])

    def test_list_requests_skips_a_file_that_vanishes_mid_scan(self):
        store.save(self.r, "queue", req("a"))
        store.save(self.r, "queue", req("b", submitted="2026-09-29T10:00:02+00:00"))
        real = store.read_json

        def racing(path):
            if path == store.req_path(self.r, "queue", "a"):  # another process moves it first
                os.replace(path, store.req_path(self.r, "processing", "a"))
            return real(path)

        with mock.patch.object(store, "read_json", side_effect=racing):
            self.assertEqual([x["id"] for x in store.list_requests(self.r, "queue")], ["b"])
            self.assertIsNone(store.locate(self.r, "ghost"))

    def test_head_known_ignores_rejected(self):
        store.save(self.r, "rejected", req("a", head="h9"))
        self.assertFalse(store.head_known(self.r, "h9"))
        store.save(self.r, "done", req("b", head="h9"))
        self.assertTrue(store.head_known(self.r, "h9"))

    def test_new_id_is_unique(self):
        first = store.new_id(self.r, "claude/a")
        store.save(self.r, "queue", req(first))
        self.assertNotEqual(store.new_id(self.r, "claude/a"), first)
        self.assertIn("claude__a", first)

    def test_state_defaults(self):
        self.assertEqual(store.read_state(self.r), {"mergesSincePromote": 0, "lastPromoteAt": None})
        store.write_state(self.r, {"mergesSincePromote": 2, "lastPromoteAt": None})
        self.assertEqual(store.read_state(self.r)["mergesSincePromote"], 2)


class MetricsTest(unittest.TestCase):
    def ev(self, type_, rid, branch="a", ts="2026-09-29T10:00:00+00:00", **detail):
        return {"ts": ts, "type": type_, "requestId": rid, "branch": branch, "issues": [], "detail": detail}

    def test_rates(self):
        evs = [
            self.ev("submitted", "r1"),
            self.ev("verified", "r1", result="CONFLICT"),
            self.ev("rejected", "r1", kind="conflict"),
            self.ev("submitted", "r2", ts="2026-09-29T10:01:00+00:00"),
            self.ev("verified", "r2", result="PASS"),
            self.ev("merged", "r2", ts="2026-09-29T10:03:00+00:00"),
            self.ev("submitted", "r3", branch="b"),
            self.ev("verified", "r3", branch="b", result="PASS"),
            self.ev("held", "r3", branch="b"),
            self.ev("merged", "r3", branch="b", ts="2026-09-29T10:01:00+00:00"),
        ]
        m = events.metrics(evs)
        self.assertEqual(m["verified"], 3)
        self.assertEqual(m["conflictRate"], 0.333)
        self.assertEqual(m["rejectRate"], 0.333)
        self.assertEqual(m["humanInterventionRate"], 0.333)
        self.assertEqual(m["firstPassRate"], 0.5)
        self.assertEqual(m["avgQueueSeconds"], 90)

    def test_empty(self):
        m = events.metrics([])
        self.assertIsNone(m["conflictRate"])
        self.assertIsNone(m["avgQueueSeconds"])


class EventsFileTest(RepoTestCase):
    def test_record_and_load(self):
        r = self.repo()
        events.record(r, "submitted", req("a"))
        events.record(r, "human-requested", reason="x")
        loaded = events.load(r)
        self.assertEqual([e["type"] for e in loaded], ["submitted", "human-requested"])
        self.assertEqual(loaded[0]["issues"], [3])
        self.assertEqual(loaded[1]["detail"], {"reason": "x"})

    def test_load_skips_malformed_lines(self):
        r = self.repo()
        events.record(r, "submitted", req("a"))
        with open(r.path("events.jsonl"), "a", encoding="utf-8") as f:
            f.write('{"ts": "trunc')
            f.write("\n")
        events.record(r, "held", req("a"))
        self.assertEqual([e["type"] for e in events.load(r)], ["submitted", "held"])


class LockTest(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.r = self.repo()
        self.r.ensure_dirs()

    def test_second_steward_refused_while_fresh(self):
        lock.acquire(self.r, "A")
        with self.assertRaises(lock.LockHeld):
            lock.acquire(self.r, "B")
        self.assertEqual(lock.acquire(self.r, "A")["sessionName"], "A")

    def test_stale_lock_taken_over(self):
        old = (datetime.now(timezone.utc) - timedelta(seconds=lock.STALE_AFTER_SEC + 5)).isoformat(timespec="seconds")
        store.write_json_atomic(self.r.path("steward.lock"), {"sessionName": "A", "startedAt": old, "heartbeatAt": old})
        prev = lock.acquire(self.r, "B")
        self.assertEqual(prev["sessionName"], "A")
        self.assertEqual(lock.read(self.r)["sessionName"], "B")

    def test_heartbeat_refreshes(self):
        old = "2020-01-01T00:00:00+00:00"
        store.write_json_atomic(self.r.path("steward.lock"), {"sessionName": "A", "startedAt": old, "heartbeatAt": old})
        lock.heartbeat(self.r)
        self.assertFalse(lock.is_stale(lock.read(self.r)))

    def test_heartbeat_without_lock_is_noop(self):
        lock.heartbeat(self.r)
        self.assertIsNone(lock.read(self.r))
        self.assertFalse(os.path.exists(self.r.path("steward.lock")))

    def test_corrupt_lock_is_stale_and_taken_over(self):
        for junk in ("{not json", '{"sessionName": "A"}'):
            with open(self.r.path("steward.lock"), "w") as f:
                f.write(junk)
            self.assertTrue(lock.is_stale(lock.read(self.r)))
            lock.acquire(self.r, "B")
            self.assertEqual(lock.read(self.r)["sessionName"], "B")
            os.unlink(self.r.path("steward.lock"))

    def test_fresh_lock_by_other_raises_and_is_untouched(self):
        lock.acquire(self.r, "A")
        with self.assertRaises(lock.LockHeld):
            lock.acquire(self.r, "B")
        self.assertEqual(lock.read(self.r)["sessionName"], "A")
        self.assertEqual(
            [n for n in os.listdir(self.r.state_dir) if n.startswith(".steward.lock")], [])

    def test_heartbeat_with_wrong_owner_raises(self):
        lock.acquire(self.r, "A")
        with self.assertRaises(lock.LockHeld):
            lock.heartbeat(self.r, session_name="B")


if __name__ == "__main__":
    unittest.main()
