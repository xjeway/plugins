import os
import subprocess
import sys
import unittest
from unittest import mock

from helpers import RepoTestCase

from steward import events, hotfiles, store
from steward.submit import check_submittable, extract_issues, parse_name_status_z


class SubmitTest(RepoTestCase):
    config = {"verify": ["true"], "approval": {"riskyPaths": [".github/*"]}}

    def setUp(self):
        super().setUp()
        self.make_integration()
        self.wt = self.worktree("feat/issue-12-search")

    def test_extract_issues(self):
        self.assertEqual(extract_issues(["feat/issue-12-x", "fix #7 and #12\ngh-30"]), [12, 7, 30])
        self.assertEqual(extract_issues(["claude/cool-bose-c0f742"]), [])

    def test_not_submittable_reasons(self):
        r, cfg = self.repo(self.wt), self.cfg()
        self.assertEqual(check_submittable(r, cfg), (False, "no commits ahead of integration"))
        self.commit("a.txt", "a\n", "add a", cwd=self.wt)
        self.write("a.txt", "dirty\n", cwd=self.wt)
        self.assertEqual(check_submittable(r, cfg), (False, "working tree has uncommitted changes"))
        self.assertFalse(check_submittable(self.repo(), cfg)[0])  # main checkout is on main

    def test_submit_queues_request_with_risk_and_issues(self):
        self.commit(".github/ci.yml", "x\n", "ci for #5", cwd=self.wt)
        self.git("rm", "-q", "README.md", cwd=self.wt)
        self.git("commit", "-qm", "drop readme", cwd=self.wt)
        out = self.ok("submit", "--session-name", "S1", cwd=self.wt)
        self.assertIn("submitted", out)
        [req] = store.list_requests(self.repo(), "queue")
        self.assertEqual(req["branch"], "feat/issue-12-search")
        self.assertEqual(req["sessionName"], "S1")
        self.assertEqual(req["issues"], [12, 5])
        self.assertEqual(sorted(req["files"]), [".github/ci.yml", "README.md"])
        self.assertEqual(req["deletedFiles"], ["README.md"])
        self.assertEqual(req["risk"], {"paths": [".github/ci.yml"], "deletions": True, "notes": []})
        self.assertEqual(req["head"], self.git("rev-parse", "HEAD", cwd=self.wt))
        self.assertEqual([e["type"] for e in events.load(self.repo())], ["submitted"])

    def test_resubmitting_same_head_is_refused(self):
        self.commit("a.txt", "a\n", "add a", cwd=self.wt)
        self.ok("submit", cwd=self.wt)
        code, _, err = self.cli("submit", cwd=self.wt)
        self.assertEqual(code, 1)
        self.assertIn("already submitted", err)

    def test_rebases_onto_integration(self):
        other = self.worktree("other")
        self.commit("b.txt", "b\n", "b", cwd=other)
        self.git("branch", "-f", "integration", "other")
        self.commit("a.txt", "a\n", "add a", cwd=self.wt)
        self.ok("submit", cwd=self.wt)
        self.assertEqual(self.git("merge-base", "HEAD", "integration", cwd=self.wt), self.git("rev-parse", "integration"))

    def test_rebase_conflict_aborts(self):
        other = self.worktree("other")
        self.commit("a.txt", "theirs\n", "a theirs", cwd=other)
        self.git("branch", "-f", "integration", "other")
        before = self.commit("a.txt", "mine\n", "a mine", cwd=self.wt)
        code, _, err = self.cli("submit", cwd=self.wt)
        self.assertEqual(code, 1)
        self.assertIn("a.txt", err)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=self.wt), before)
        self.assertEqual(self.git("status", "--porcelain", cwd=self.wt), "")
        self.assertEqual(store.list_requests(self.repo(), "queue"), [])

    def test_non_ascii_paths_are_matched_unquoted(self):
        self.write_config({"verify": ["true"], "approval": {"riskyPaths": ["文档/*"]}})
        self.commit("文档/说明.md", "x\n", "docs", cwd=self.wt)
        self.commit("café.txt", "x\n", "cafe", cwd=self.wt)
        self.ok("submit", cwd=self.wt)
        [req] = store.list_requests(self.repo(), "queue")
        self.assertEqual(sorted(req["files"]), ["café.txt", "文档/说明.md"])
        self.assertEqual(req["risk"]["paths"], ["文档/说明.md"])

    def test_parse_name_status_z_handles_renames_and_copies(self):
        out = "M\0a.txt\0D\0gone.txt\0R100\0old.txt\0new.txt\0C75\0src.txt\0copy.txt\0A\0文档/说明.md\0"
        files, deleted = parse_name_status_z(out)
        self.assertEqual(files, ["a.txt", "gone.txt", "old.txt", "new.txt", "copy.txt", "文档/说明.md"])
        self.assertEqual(deleted, ["gone.txt", "old.txt"])

    def test_submit_with_a_lock_missing_its_session_name(self):
        r = self.repo()
        r.ensure_dirs()
        store.write_json_atomic(r.path("steward.lock"), {"startedAt": "x"})
        self.commit("a.txt", "a\n", "add a", cwd=self.wt)
        out = self.ok("submit", cwd=self.wt)
        self.assertIn("submitted", out)
        self.assertIn("steward: (unknown)", out)

    def test_no_integration_branch(self):
        self.git("branch", "-D", "integration")
        self.commit("a.txt", "a\n", "add a", cwd=self.wt)
        code, _, err = self.cli("submit", cwd=self.wt)
        self.assertEqual(code, 1)
        self.assertIn("steward init", err)


class RebaseHotFileTest(RepoTestCase):
    """Conflicts that the submit rebase hits only in hot files are resolved by the same rules verify uses."""
    config = {
        "verify": ["true"],
        "hotFiles": {"*.strings": "union", "lock.txt": "regenerate:cat lock.txt x.txt y.txt > lock.tmp && mv lock.tmp lock.txt", "bad.lock": "regenerate:false"},
    }

    def setUp(self):
        super().setUp()
        self.commit("a.strings", "k0\n", "base strings")
        self.make_integration()
        self.wt = self.worktree("claude/w")
        self.other = self.worktree("other", base="integration")

    def land(self, rel, content):
        """Commit on `other` and fast-forward integration to it."""
        self.commit(rel, content, f"land {rel}", cwd=self.other)
        self.git("branch", "-f", "integration", "other")

    def read(self, rel):
        with open(os.path.join(self.wt, rel), encoding="utf-8") as f:
            return f.read()

    def test_union_conflict_is_resolved_and_submitted(self):
        self.land("a.strings", "k0\nintegration=1\n")
        self.commit("a.strings", "k0\nworker=1\n", "worker strings", cwd=self.wt)
        out = self.ok("submit", cwd=self.wt)
        self.assertIn("auto-resolved hot files during the rebase: a.strings", out)
        merged = self.read("a.strings")
        self.assertIn("integration=1", merged)
        self.assertIn("worker=1", merged)
        self.assertEqual(self.git("status", "--porcelain", cwd=self.wt), "")
        self.assertEqual(self.git("merge-base", "HEAD", "integration", cwd=self.wt), self.git("rev-parse", "integration"))
        [req] = store.list_requests(self.repo(), "queue")
        self.assertEqual(req["autoResolved"], ["a.strings"])
        self.assertEqual(req["head"], self.git("rev-parse", "HEAD", cwd=self.wt))

    def test_each_stopped_commit_is_resolved(self):
        self.commit("b.strings", "b0\n", "base b", cwd=self.other)
        self.git("branch", "-f", "integration", "other")
        self.git("merge", "-q", "--ff-only", "integration", cwd=self.wt)
        self.land("a.strings", "k0\nintegration=a\n")
        self.land("b.strings", "b0\nintegration=b\n")
        self.commit("a.strings", "k0\nworker=a\n", "worker a", cwd=self.wt)
        self.commit("b.strings", "b0\nworker=b\n", "worker b", cwd=self.wt)
        out = self.ok("submit", cwd=self.wt)
        self.assertIn("a.strings, b.strings", out)
        self.assertIn("worker=a", self.read("a.strings"))
        self.assertIn("integration=b", self.read("b.strings"))
        self.assertEqual(self.git("rev-list", "--count", "integration..HEAD", cwd=self.wt), "2")

    def test_regenerate_takes_integration_copy_and_reruns_the_command(self):
        self.commit("lock.txt", "base\n", "base lock", cwd=self.other)
        self.git("branch", "-f", "integration", "other")
        self.git("merge", "-q", "--ff-only", "integration", cwd=self.wt)
        self.commit("x.txt", "x\n", "integration dep", cwd=self.other)
        self.land("lock.txt", "integration\n")
        self.write("y.txt", "y\n", cwd=self.wt)
        self.commit("lock.txt", "worker\n", "worker dep", cwd=self.wt)
        out = self.ok("submit", cwd=self.wt)
        self.assertIn("auto-resolved hot files during the rebase: lock.txt", out)
        self.assertEqual(self.read("lock.txt"), "integration\nx\ny\n")  # integration's copy, then the command
        self.assertEqual(self.git("status", "--porcelain", cwd=self.wt), "")

    def test_conflict_outside_hot_files_aborts_untouched(self):
        self.commit("a.txt", "base\n", "base a", cwd=self.other)
        self.git("branch", "-f", "integration", "other")
        self.git("merge", "-q", "--ff-only", "integration", cwd=self.wt)
        self.commit("a.txt", "integration\n", "int a", cwd=self.other)
        self.land("a.strings", "k0\nintegration=1\n")
        self.write("a.txt", "worker\n", cwd=self.wt)
        before = self.commit("a.strings", "k0\nworker=1\n", "worker both", cwd=self.wt)
        code, _, err = self.cli("submit", cwd=self.wt)
        self.assertEqual(code, 1)
        self.assertIn("a.txt", err)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=self.wt), before)
        self.assertEqual(self.git("status", "--porcelain", cwd=self.wt), "")
        self.assertEqual(self.read("a.strings"), "k0\nworker=1\n")
        self.assertEqual(store.list_requests(self.repo(), "queue"), [])

    def test_failing_rule_aborts_untouched(self):
        self.commit("bad.lock", "base\n", "base lock", cwd=self.other)
        self.git("branch", "-f", "integration", "other")
        self.git("merge", "-q", "--ff-only", "integration", cwd=self.wt)
        self.land("bad.lock", "integration\n")
        before = self.commit("bad.lock", "worker\n", "worker lock", cwd=self.wt)
        code, _, err = self.cli("submit", cwd=self.wt)
        self.assertEqual(code, 1)
        self.assertIn("bad.lock", err)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=self.wt), before)
        self.assertEqual(self.git("status", "--porcelain", cwd=self.wt), "")

    def test_non_conflict_rebase_failure_reports_git_error(self):
        self.land("new.txt", "integration\n")
        hook = os.path.join(self.root, ".git", "hooks", "pre-rebase")
        self.write(hook, "#!/bin/sh\necho 'rebasing is frozen today' >&2\nexit 1\n")
        os.chmod(hook, 0o755)
        before = self.commit("w.txt", "w\n", "worker", cwd=self.wt)
        code, _, err = self.cli("submit", cwd=self.wt)
        self.assertEqual(code, 1)
        self.assertNotIn("(unknown)", err)
        self.assertIn("rebasing is frozen today", err)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=self.wt), before)

    def test_multi_commit_union_matches_the_trial_merge(self):
        # Each replayed commit is unioned against integration and the fork point, like verify's single merge,
        # so lines the worker rewrote in later commits do not come back.
        self.commit("L.strings", "k=0\n", "base L", cwd=self.other)
        self.git("branch", "-f", "integration", "other")
        self.git("merge", "-q", "--ff-only", "integration", cwd=self.wt)
        self.land("L.strings", "k=0\nk1=1\n")
        for n in (1, 2, 3):
            self.commit("L.strings", f"k=0\nk2_{n}=1\n", f"worker {n}", cwd=self.wt)
        self.ok("submit", cwd=self.wt)
        self.assertEqual(self.read("L.strings"), "k=0\nk1=1\nk2_3=1\n")

    def assert_untouched(self, before):
        self.assertFalse(os.path.exists(self.git("rev-parse", "--path-format=absolute", "--git-path", "rebase-merge",
                                                 cwd=self.wt)))
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD", cwd=self.wt), "claude/w")
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=self.wt), before)
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=no", cwd=self.wt), "")

    def conflict_on(self, rel):
        self.commit(rel, "base\n", "base", cwd=self.other)
        self.git("branch", "-f", "integration", "other")
        self.git("merge", "-q", "--ff-only", "integration", cwd=self.wt)
        self.land(rel, "integration\n")
        return self.commit(rel, "worker\n", "worker", cwd=self.wt)

    def test_exception_mid_resolution_aborts_the_rebase(self):
        before = self.conflict_on("a.strings")
        with mock.patch.object(hotfiles, "resolve", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.cli("submit", cwd=self.wt)
        self.assert_untouched(before)

    def test_sigterm_mid_regenerate_aborts_and_kills_the_command(self):
        pid_file = os.path.join(self.tmp, "regen.pid")
        self.write_config(dict(self.config, hotFiles={"slow.lock": f"regenerate:echo $$ > {pid_file}; kill -TERM $PPID; sleep 30"}))
        before = self.conflict_on("slow.lock")
        steward = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin", "steward")
        p = subprocess.run([sys.executable, steward, "submit"], cwd=self.wt, capture_output=True, text=True, timeout=20)
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertIn("interrupted", p.stderr)
        self.assert_untouched(before)
        with open(pid_file) as f:
            pid = int(f.read())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)


if __name__ == "__main__":
    unittest.main()
