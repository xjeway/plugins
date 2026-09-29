import json
import os
import stat
import unittest

from helpers import RepoTestCase, sh

from steward import events, store

FAKE_GH = """#!/bin/sh
echo "$@" >> "{log}"
case "$2" in
  list) cat "{existing}" 2>/dev/null || true ;;
  create) echo "https://github.com/x/y/pull/7" ;;
esac
"""


class PromoteTest(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.remote = os.path.join(self.tmp, "remote.git")
        sh(["git", "init", "-q", "--bare", self.remote], self.tmp)
        self.git("remote", "add", "origin", self.remote)
        self.git("push", "-q", "origin", "main")
        self.gh_log = os.path.join(self.tmp, "gh.log")
        self.existing = os.path.join(self.tmp, "existing")
        gh = os.path.join(self.tmp, "gh")
        with open(gh, "w") as f:
            f.write(FAKE_GH.format(log=self.gh_log, existing=self.existing))
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IEXEC)
        os.environ["STEWARD_GH"] = gh
        self.addCleanup(os.environ.pop, "STEWARD_GH", None)
        self.init_steward()

    def merge_one(self, branch, rel):
        _, req = self.feature(branch, rel, "x\n")
        self.ok("next", "--id", req["id"])
        self.ok("verify", req["id"])
        self.ok("merge", req["id"])
        return req

    def test_promote_pushes_and_opens_pr(self):
        self.merge_one("feat/issue-4-a", "a.txt")
        out = self.ok("promote")
        self.assertIn("https://github.com/x/y/pull/7", out)
        self.assertEqual(sh(["git", "rev-parse", "integration"], self.remote), self.git("rev-parse", "integration"))
        with open(self.gh_log) as f:
            log = f.read()
        self.assertIn("pr create --base main --head integration", log)
        self.assertIn("#4", log)
        self.assertIn("merge commit", log)
        self.assertEqual(store.read_state(self.repo())["mergesSincePromote"], 0)
        self.assertEqual(events.load(self.repo())[-1]["type"], "promoted")

    def test_promote_reuses_open_pr(self):
        with open(self.existing, "w") as f:
            f.write("https://github.com/x/y/pull/3\n")
        self.merge_one("claude/a", "a.txt")
        self.assertIn("pull/3", self.ok("promote"))
        with open(self.gh_log) as f:
            self.assertNotIn("pr create", f.read())

    def test_promote_merges_origin_main_first(self):
        self.commit("m.txt", "m\n", "main moved")
        self.git("push", "-q", "origin", "main")
        self.ok("promote")
        self.assertEqual(self.git("show", "integration:m.txt"), "m")

    def test_promote_without_remote_asks_human(self):
        self.git("remote", "remove", "origin")
        code, _, err = self.cli("promote")
        self.assertEqual(code, 1)
        self.assertIn("no 'origin' remote", err)
        self.assertEqual(events.load(self.repo())[-1]["type"], "human-requested")

    def assert_promote_fails(self, message):
        before = store.read_state(self.repo()).get("mergesSincePromote")
        code, _, err = self.cli("promote")
        self.assertEqual(code, 1)
        self.assertIn(message, err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(events.load(self.repo())[-1]["type"], "human-requested")
        self.assertEqual(store.read_state(self.repo()).get("mergesSincePromote"), before)

    def test_promote_without_gh_asks_human(self):
        self.merge_one("claude/a", "a.txt")
        os.environ["STEWARD_GH"] = os.path.join(self.tmp, "no-such-gh")
        self.assert_promote_fails("cannot run gh")

    def test_promote_gh_failure_asks_human(self):
        self.merge_one("claude/a", "a.txt")
        failing = os.path.join(self.tmp, "gh-fail")
        with open(failing, "w") as f:
            f.write("#!/bin/sh\necho 'not logged in' >&2\nexit 1\n")
        os.chmod(failing, os.stat(failing).st_mode | stat.S_IEXEC)
        os.environ["STEWARD_GH"] = failing
        self.assert_promote_fails("gh pr list failed: not logged in")

    def test_metrics_command(self):
        self.merge_one("claude/a", "a.txt")
        data = json.loads(self.ok("metrics", "--json"))
        self.assertEqual(data["merged"], 1)
        self.assertEqual(data["firstPassRate"], 1.0)
        self.assertIn("conflictRate: 0.0", self.ok("metrics"))


if __name__ == "__main__":
    unittest.main()
