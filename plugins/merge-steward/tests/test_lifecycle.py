import io
import json
import os
import unittest
from unittest import mock

from helpers import RepoTestCase, sh

from steward import lifecycle, lock, store


class InitTest(RepoTestCase):
    def test_init_creates_integration_verify_worktree_and_lock(self):
        out = self.init_steward()
        self.assertIn("created integration from main", out)
        r = self.repo()
        self.assertEqual(self.git("rev-parse", "integration"), self.git("rev-parse", "main"))
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD", cwd=r.verify_dir), "integration")
        self.assertEqual(lock.read(r)["sessionName"], "Merge steward")

    def test_init_is_idempotent_and_syncs_main(self):
        self.init_steward()
        self.commit("m.txt", "m\n", "main moves")
        self.init_steward()
        self.assertEqual(self.git("rev-parse", "integration"), self.git("rev-parse", "main"))

    def test_init_refuses_second_live_steward(self):
        self.init_steward()
        code, _, err = self.cli("init", "--session-name", "Other")
        self.assertEqual(code, 1)
        self.assertIn("another steward is active", err)

    def test_init_reports_main_conflict(self):
        self.init_steward()
        self.commit("c.txt", "integration side\n", "c", cwd=self.repo().verify_dir)
        self.commit("c.txt", "main side\n", "c main")
        out = self.init_steward()
        self.assertIn("WARNING", out)


UPWARD_SEARCH = 'd="$PWD"; while [ "$d" != / ]; do [ -e "$d/upward-marker" ] && exit 1; d=$(dirname "$d"); done'


class VerifyWorktreeLocationTest(RepoTestCase):
    """Tools that resolve upward (node require, eslint, tsconfig) must not see the main checkout from verify."""
    config = {"verify": [UPWARD_SEARCH]}

    def test_verify_worktree_lives_outside_the_main_checkout(self):
        self.write("upward-marker", "only in the main checkout\n")
        self.init_steward()
        r = self.repo()
        self.assertEqual(r.verify_dir, self.root + ".steward-verify")
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD", cwd=r.verify_dir), "integration")
        v = self.repo(r.verify_dir)
        self.assertTrue(v.is_verify_worktree())
        self.assertFalse(r.is_verify_worktree())
        self.assertEqual(v.main_checkout, self.root)
        self.assertEqual(v.state_dir, r.state_dir)
        _, req = self.feature("claude/a", "a.txt", "a\n")
        self.ok("next", "--id", req["id"])
        self.assertTrue(self.ok("verify", req["id"]).startswith("PASS"))
        self.assertTrue(self.ok("verify", req["id"], cwd=r.verify_dir).startswith("PASS"))
        self.assertTrue(self.ok("merge", req["id"]).startswith("merged"))


class VerifyWorktreeTrustTest(RepoTestCase):
    """A directory already at <repo>.steward-verify is only used if it is this repository's worktree on integration."""

    def test_unrelated_repository_is_refused_and_untouched(self):
        d = self.root + ".steward-verify"
        os.makedirs(d)
        sh(["git", "init", "-q", "-b", "main"], d)
        with open(os.path.join(d, "precious.txt"), "w") as f:
            f.write("keep me\n")
        code, _, err = self.cli("init", "--session-name", "Merge steward")
        self.assertEqual(code, 1)
        self.assertIn("not a worktree of this repository", err)
        with open(os.path.join(d, "precious.txt")) as f:
            self.assertEqual(f.read(), "keep me\n")

    def test_worktree_on_another_branch_is_refused(self):
        self.make_integration()
        self.git("worktree", "add", "-q", "-b", "elsewhere", self.root + ".steward-verify", "main")
        code, _, err = self.cli("init", "--session-name", "Merge steward")
        self.assertEqual(code, 1)
        self.assertIn("elsewhere", err)
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD", cwd=self.root + ".steward-verify"), "elsewhere")


class WatchStatusTest(RepoTestCase):
    def test_watch_prints_new_and_approved(self):
        self.init_steward()
        _, req = self.feature("claude/a", "a.txt", "a\n")
        r = self.repo()
        buf = io.StringIO()
        lifecycle.watch(r, 0, once=True, out=buf)
        self.assertEqual(buf.getvalue().strip(), f"NEW {req['id']} claude/a")
        store.save(r, "queue", dict(req, approved={"by": "user", "at": "x"}))
        buf2 = io.StringIO()
        lifecycle.watch(r, 0, once=True, out=buf2)
        self.assertEqual(buf2.getvalue().strip(), f"APPROVED {req['id']} claude/a")

    def test_watch_survives_a_failing_pass_and_exits_cleanly_on_interrupt(self):
        self.init_steward()
        _, req = self.feature("claude/a", "a.txt", "a\n")
        r = self.repo()
        real = store.list_requests
        calls = {"list": 0, "sleep": 0}

        def flaky(repo, state):
            calls["list"] += 1
            if calls["list"] == 1:
                raise FileNotFoundError("queue entry moved mid-scan")
            return real(repo, state)

        def sleep(_):
            calls["sleep"] += 1
            if calls["sleep"] == 2:
                raise KeyboardInterrupt

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(store, "list_requests", side_effect=flaky), \
                mock.patch.object(lifecycle.time, "sleep", side_effect=sleep):
            lifecycle.watch(r, 0, out=out, err=err)
        self.assertEqual(out.getvalue().strip(), f"NEW {req['id']} claude/a")
        self.assertIn("queue entry moved mid-scan", err.getvalue())

    def test_status(self):
        self.init_steward()
        self.feature("claude/a", "a.txt", "a\n")
        text = self.ok("status")
        self.assertIn("queue (1):", text)
        self.assertIn("claude/a", text)
        data = json.loads(self.ok("status", "--json"))
        self.assertEqual(len(data["queue"]), 1)
        self.assertEqual(data["steward"]["sessionName"], "Merge steward")


if __name__ == "__main__":
    unittest.main()
