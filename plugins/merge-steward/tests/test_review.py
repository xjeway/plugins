import json
import unittest

from helpers import RepoTestCase

from steward import events, store


class NextTest(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.init_steward()

    def test_next_claims_fifo_and_refuses_while_busy(self):
        _, a = self.feature("claude/a", "a.txt", "a\n")
        self.feature("claude/b", "b.txt", "b\n")
        claimed = json.loads(self.ok("next"))
        self.assertEqual(claimed["id"], a["id"])
        code, _, err = self.cli("next")
        self.assertEqual(code, 1)
        self.assertIn("already processing", err)

    def test_approved_and_requeued_go_first(self):
        self.feature("claude/a", "a.txt", "a\n")
        _, b = self.feature("claude/b", "b.txt", "b\n")
        r = self.repo()
        store.save(r, "queue", dict(b, approved={"by": "user", "at": "x"}))
        self.assertEqual(json.loads(self.ok("next"))["id"], b["id"])

    def test_superseded_when_branch_moved(self):
        wt, a = self.feature("claude/a", "a.txt", "a\n")
        self.commit("a.txt", "more\n", "more", cwd=wt)
        out = self.ok("next")
        self.assertIn(f"superseded {a['id']}", out)
        self.assertIn("queue empty", out)
        [rej] = store.list_requests(self.repo(), "rejected")
        self.assertEqual(rej["rejectKind"], "superseded")
        self.assertIn("superseded", [e["type"] for e in events.load(self.repo())])

    def test_init_returns_processing_request_to_queue(self):
        _, req = self.feature("claude/a", "a.txt", "a\n")
        self.ok("next")
        out = self.init_steward()
        self.assertIn(f"returned {req['id']} to the queue", out)
        [queued] = store.list_requests(self.repo(), "queue")
        self.assertTrue(queued["requeued"])

    def test_next_on_empty_queue(self):
        self.assertIn("queue empty", self.ok("next"))


class ReviewTest(RepoTestCase):
    def test_review_reports_overlaps(self):
        self.init_steward()
        _, a = self.feature("claude/a", "shared.txt", "a\n")
        other = self.worktree("claude/b")
        self.commit("shared.txt", "b\n", "b edits shared", cwd=other)
        self.ok("submit", "--session-name", "S-B", cwd=other)
        self.ok("next", "--id", a["id"])
        data = json.loads(self.ok("review", a["id"]))
        self.assertIn("shared.txt", data["diffstat"])
        [active] = data["overlapWithActive"]
        self.assertEqual(active["branch"], "claude/b")
        self.assertEqual(active["sessionName"], "S-B")
        self.assertEqual(active["files"], ["shared.txt"])
        with open(data["diffPath"]) as f:
            self.assertIn("+a", f.read())

    def test_review_fails_when_head_is_gone(self):
        self.init_steward()
        _, a = self.feature("claude/a", "a.txt", "a\n")
        self.ok("next", "--id", a["id"])
        _, req = store.locate(self.repo(), a["id"], ["processing"])
        store.save(self.repo(), "processing", dict(req, head="0" * 40))
        code, _, err = self.cli("review", a["id"])
        self.assertEqual(code, 1)
        self.assertIn("git diff", err)

    def test_review_requires_processing(self):
        self.init_steward()
        _, a = self.feature("claude/a", "a.txt", "a\n")
        code, _, err = self.cli("review", a["id"])
        self.assertEqual(code, 1)
        self.assertIn("not being processed", err)


if __name__ == "__main__":
    unittest.main()
