import os
import unittest

from helpers import RepoTestCase

from steward import decide, events, inbox, store


class DecideTest(RepoTestCase):
    config = {"verify": ["true"], "approval": {"riskyPaths": ["secure/*"]}, "promote": {"everyNMerges": 2}}

    def setUp(self):
        super().setUp()
        self.init_steward()

    def claim(self, branch, rel, content):
        wt, req = self.feature(branch, rel, content)
        self.ok("next", "--id", req["id"])
        return wt, req

    def reason(self, text):
        path = os.path.join(self.tmp, "reason.md")
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_merge_after_pass(self):
        _, req = self.claim("claude/a", "a.txt", "a\n")
        self.ok("verify", req["id"])
        out = self.ok("merge", req["id"])
        self.assertIn("merged", out)
        self.assertNotIn("promote due", out)
        self.assertEqual(self.git("show", "integration:a.txt"), "a")
        self.assertIn(f"Steward-Request: {req['id']}", self.git("log", "-1", "--format=%B", "integration"))
        [done] = store.list_requests(self.repo(), "done")
        self.assertEqual(done["mergeCommit"], self.git("rev-parse", "integration"))
        _, req2 = self.claim("claude/b", "b.txt", "b\n")
        self.ok("verify", req2["id"])
        self.assertIn("promote due", self.ok("merge", req2["id"]))

    def test_merge_guards(self):
        wt, req = self.claim("claude/a", "a.txt", "a\n")
        code, _, err = self.cli("merge", req["id"])
        self.assertIn("did not PASS", err)
        self.ok("verify", req["id"])
        self.commit("a.txt", "moved\n", "moved", cwd=wt)
        code, _, err = self.cli("merge", req["id"])
        self.assertEqual(code, 1)
        self.assertIn("branch moved", err)

    def test_merge_refuses_when_integration_moved(self):
        _, req = self.claim("claude/a", "a.txt", "a\n")
        self.ok("verify", req["id"])
        other = self.worktree("x")
        self.commit("x.txt", "x\n", "x", cwd=other)
        self.git("update-ref", "refs/heads/integration", self.git("rev-parse", "x"))
        code, _, err = self.cli("merge", req["id"])
        self.assertEqual(code, 1)
        self.assertIn("moved since verify", err)

    def test_risky_needs_hold_and_approval(self):
        _, req = self.claim("claude/a", "secure/key.txt", "k\n")
        self.ok("verify", req["id"])
        code, _, err = self.cli("merge", req["id"])
        self.assertIn("needs human approval", err)
        self.ok("hold", req["id"], "--note", "touches secure/")
        [held] = store.list_requests(self.repo(), "awaiting")
        self.assertEqual(held["risk"]["notes"], ["touches secure/"])
        other = self.worktree("claude/other")
        self.ok("approve", "claude/a", cwd=other)
        self.ok("next")
        self.ok("verify", req["id"])
        self.ok("merge", req["id"])
        types = [e["type"] for e in events.load(self.repo())]
        self.assertIn("held", types)
        self.assertIn("approved", types)

    def test_deny(self):
        _, req = self.claim("claude/a", "secure/key.txt", "k\n")
        self.ok("hold", req["id"], "--note", "risky")
        self.ok("deny", req["id"], "--reason", "not now")
        self.assertEqual(inbox.pending(self.repo(), "claude/a")["kind"], "denied")
        self.assertEqual(store.list_requests(self.repo(), "rejected")[0]["rejectKind"], "denied")

    def test_reject_writes_notice_with_evidence(self):
        self.write_config(dict(self.config, verify=["echo boom; exit 3"]))
        _, req = self.claim("claude/a", "a.txt", "a\n")
        self.ok("verify", req["id"])
        self.ok("reject", req["id"], "--kind", "verify-failed", "--reason-file", self.reason("the build broke"))
        notice = inbox.pending(self.repo(), "claude/a")
        self.assertEqual(notice["reason"], "the build broke")
        self.assertIn("boom", notice["evidence"])
        self.assertEqual(self.git("status", "--porcelain", cwd=self.repo().verify_dir), "")

    def test_requeue(self):
        _, req = self.claim("claude/a", "a.txt", "a\n")
        self.ok("requeue", req["id"])
        [q] = store.list_requests(self.repo(), "queue")
        self.assertTrue(q["requeued"])

    def test_approval_mode_never_and_always(self):
        self.write_config(dict(self.config, approval={"mode": "never", "riskyPaths": ["secure/*"]}))
        _, req = self.claim("claude/a", "secure/k.txt", "k\n")
        self.ok("verify", req["id"])
        self.ok("merge", req["id"])
        self.write_config(dict(self.config, approval={"mode": "always"}))
        _, req2 = self.claim("claude/b", "b.txt", "b\n")
        self.ok("verify", req2["id"])
        code, _, err = self.cli("merge", req2["id"])
        self.assertIn("needs human approval", err)

    def test_log_event(self):
        _, req = self.claim("claude/a", "a.txt", "a\n")
        self.ok("log-event", "coordinated", "--id", req["id"], "--text", "told claude/b to keep both")
        ev = events.load(self.repo())[-1]
        self.assertEqual((ev["type"], ev["requestId"]), ("coordinated", req["id"]))

    def crash_after_commit(self, req):
        """Do what merge does up to and including the commit, then die before any bookkeeping."""
        self.ok("verify", req["id"])
        real = store.move

        def boom(*a, **k):
            raise RuntimeError("crash")

        store.move = boom
        try:
            with self.assertRaises(RuntimeError):
                decide.merge(self.repo(), self.cfg(), req["id"])
        finally:
            store.move = real

    def test_merge_finishes_idempotently_after_crash(self):
        _, req = self.claim("claude/a", "a.txt", "a\n")
        self.crash_after_commit(req)
        self.assertEqual(len(store.list_requests(self.repo(), "processing")), 1)
        tip = self.git("rev-parse", "integration")
        out = self.ok("merge", req["id"])
        self.assertIn("merged", out)
        self.assertEqual(self.git("rev-parse", "integration"), tip)
        [done] = store.list_requests(self.repo(), "done")
        self.assertEqual(done["mergeCommit"], tip)
        self.assertEqual(store.list_requests(self.repo(), "processing"), [])
        self.assertEqual([e["type"] for e in events.load(self.repo())].count("merged"), 1)
        self.assertEqual(store.read_state(self.repo())["mergesSincePromote"], 1)
        self.assertEqual(self.git("status", "--porcelain", cwd=self.repo().verify_dir), "")

    def test_init_finishes_a_merged_request_instead_of_requeueing(self):
        _, req = self.claim("claude/a", "a.txt", "a\n")
        self.crash_after_commit(req)
        tip = self.git("rev-parse", "integration")
        self.ok("init", "--session-name", "Merge steward")
        self.assertEqual(store.list_requests(self.repo(), "queue"), [])
        [done] = store.list_requests(self.repo(), "done")
        self.assertEqual(done["mergeCommit"], tip)
        self.assertEqual([e["type"] for e in events.load(self.repo())].count("merged"), 1)
        self.assertEqual(store.read_state(self.repo())["mergesSincePromote"], 1)

    def fake_integration_commit(self, message):
        d = self.repo().verify_dir
        self.commit("other.txt", "o\n", "other", cwd=d)
        self.git("commit", "--amend", "-q", "-m", message, cwd=d)

    def test_prefix_id_collision_is_not_a_finished_merge(self):
        _, req = self.claim("claude/a", "a.txt", "a\n")
        self.ok("verify", req["id"])
        # a different request whose id extends this one's, already merged
        self.fake_integration_commit(f"Merge claude/ab into integration\n\nx\n\nSteward-Request: {req['id']}b")
        self.assertIsNone(decide.merge_commit_for(self.repo(), self.cfg(), req))
        code, _, err = self.cli("merge", req["id"])
        self.assertEqual(code, 1)
        self.assertIn("moved since verify", err)
        self.ok("init", "--session-name", "Merge steward")
        self.assertEqual(store.list_requests(self.repo(), "done"), [])
        self.assertEqual(len(store.list_requests(self.repo(), "queue")), 1)

    def test_quoted_trailer_text_is_not_a_finished_merge(self):
        _, req = self.claim("claude/a", "a.txt", "a\n")
        self.ok("verify", req["id"])
        self.fake_integration_commit(f"Fix thing\n\nSteward-Request: {req['id']} is quoted here\n\nmore text")
        self.assertIsNone(decide.merge_commit_for(self.repo(), self.cfg(), req))
        self.ok("init", "--session-name", "Merge steward")
        self.assertEqual(store.list_requests(self.repo(), "done"), [])

    def test_reject_with_missing_reason_file(self):
        _, req = self.claim("claude/a", "a.txt", "a\n")
        code, _, err = self.cli("reject", req["id"], "--kind", "review", "--reason-file", os.path.join(self.tmp, "nope.md"))
        self.assertEqual(code, 1)
        self.assertIn("--reason-file", err)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
