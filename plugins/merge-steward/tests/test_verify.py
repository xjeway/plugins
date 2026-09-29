import json
import os
import unittest

from helpers import RepoTestCase

from steward import events, store


class VerifyTest(RepoTestCase):
    config = {
        "verify": ["test -f a.txt", "test ! -f fail.txt"],
        "verifyTimeoutSec": 2,
        "hotFiles": {"strings.txt": "union", "lock.txt": "regenerate:sort -u a.txt b.txt > lock.txt 2>/dev/null || cat a.txt > lock.txt"},
    }

    def setUp(self):
        super().setUp()
        self.init_steward()

    def claim(self, branch, rel, content):
        wt, req = self.feature(branch, rel, content)
        self.ok("next", "--id", req["id"])
        return wt, req

    def last(self, rid):
        return store.locate(self.repo(), rid, ("processing",))[1]["lastVerify"]

    def test_pass(self):
        _, req = self.claim("claude/a", "a.txt", "a\n")
        out = self.ok("verify", req["id"])
        self.assertTrue(out.startswith("PASS"))
        last = self.last(req["id"])
        self.assertEqual(last["integration"], self.git("rev-parse", "integration"))
        self.assertEqual(self.git("rev-parse", "MERGE_HEAD", cwd=self.repo().verify_dir), req["head"])
        self.assertEqual(events.load(self.repo())[-1]["detail"], {"result": "PASS"})

    def test_fail_reports_command_and_log(self):
        wt = self.worktree("claude/a")
        self.commit("a.txt", "a\n", "a", cwd=wt)
        self.commit("fail.txt", "x\n", "boom", cwd=wt)
        self.ok("submit", cwd=wt)
        self.ok("next")
        rid = store.list_requests(self.repo(), "processing")[0]["id"]
        out = self.ok("verify", rid)
        self.assertIn("FAIL test ! -f fail.txt", out)
        with open(self.last(rid)["log"]) as f:
            self.assertIn("exit code 1", f.read())

    def test_timeout(self):
        self.write_config(dict(self.config, verify=["sleep 5"], verifyTimeoutSec=1))
        _, req = self.claim("claude/a", "a.txt", "a\n")
        self.assertTrue(self.ok("verify", req["id"]).startswith("TIMEOUT sleep 5"))

    def _land_on_integration(self, rel, content):
        other = self.worktree("landed")
        self.commit(rel, content, "landed", cwd=other)
        self.git("merge", "-q", "--no-edit", "landed", cwd=self.repo().verify_dir)

    def test_conflict_keeps_raw_hunks(self):
        _, req = self.claim("claude/a", "a.txt", "mine\n")
        self._land_on_integration("a.txt", "theirs\n")
        out = self.ok("verify", req["id"])
        self.assertTrue(out.startswith("CONFLICT a.txt"))
        with open(self.last(req["id"])["evidence"]) as f:
            text = f.read()
        self.assertIn("mine", text)
        self.assertIn("theirs", text)
        self.assertEqual(self.git("status", "--porcelain", cwd=self.repo().verify_dir), "")

    def test_union_hot_file(self):
        wt = self.worktree("claude/a")
        self.commit("a.txt", "a\n", "a", cwd=wt)
        self.commit("strings.txt", "hello=Hello\n", "string a", cwd=wt)
        self.ok("submit", cwd=wt)
        self._land_on_integration("strings.txt", "bye=Bye\n")
        self.ok("next")
        rid = store.list_requests(self.repo(), "processing")[0]["id"]
        self.assertTrue(self.ok("verify", rid).startswith("PASS"))
        with open(os.path.join(self.repo().verify_dir, "strings.txt")) as f:
            merged = f.read()
        self.assertIn("hello=Hello", merged)
        self.assertIn("bye=Bye", merged)

    def test_regenerate_hot_file(self):
        wt = self.worktree("claude/a")
        self.commit("a.txt", "a\n", "a", cwd=wt)
        self.commit("lock.txt", "mine\n", "lock a", cwd=wt)
        self.ok("submit", cwd=wt)
        self._land_on_integration("lock.txt", "theirs\n")
        self.ok("next")
        rid = store.list_requests(self.repo(), "processing")[0]["id"]
        self.assertTrue(self.ok("verify", rid).startswith("PASS"))
        with open(os.path.join(self.repo().verify_dir, "lock.txt")) as f:
            self.assertEqual(f.read(), "a\n")

    def test_verify_base(self):
        out = self.ok("verify-base")
        self.assertTrue(out.startswith("FAIL test -f a.txt"))



class VerifyHotFileFailureTest(RepoTestCase):
    config = {
        "verify": ["true"],
        "verifyTimeoutSec": 2,
        "hotFiles": {"lock.txt": "regenerate:false", "gone.txt": "regenerate:true"},
    }

    def setUp(self):
        super().setUp()
        self.init_steward()

    def _land(self, rel, content):
        other = self.worktree("landed")
        self.commit(rel, content, "landed", cwd=other)
        self.git("merge", "-q", "--no-edit", "landed", cwd=self.repo().verify_dir)

    def test_failed_regenerate_keeps_evidence_and_clean_worktree(self):
        wt = self.worktree("claude/a")
        self.commit("lock.txt", "mine\n", "lock a", cwd=wt)
        self.ok("submit", cwd=wt)
        self._land("lock.txt", "theirs\n")
        self.ok("next")
        rid = store.list_requests(self.repo(), "processing")[0]["id"]
        out = self.ok("verify", rid)
        self.assertTrue(out.startswith("CONFLICT lock.txt"))
        last = store.locate(self.repo(), rid, ("processing",))[1]["lastVerify"]
        with open(last["evidence"]) as f:
            text = f.read()
        self.assertIn("<<<<<<<", text)
        self.assertIn("mine", text)
        self.assertIn("theirs", text)
        self.assertEqual(self.git("status", "--porcelain", cwd=self.repo().verify_dir), "")

    def test_delete_modify_conflict_on_hot_file_is_a_conflict(self):
        base = self.worktree("base")
        self.commit("gone.txt", "one\n", "add gone", cwd=base)
        self.git("merge", "-q", "--no-edit", "base", cwd=self.repo().verify_dir)
        wt = self.worktree("claude/a")
        self.git("merge", "-q", "--no-edit", "base", cwd=wt)
        self.commit("gone.txt", "two\n", "modify gone", cwd=wt)
        self.ok("submit", cwd=wt)
        other = self.worktree("remover")
        self.git("merge", "-q", "--no-edit", "base", cwd=other)
        self.git("rm", "-q", "gone.txt", cwd=other)
        self.git("commit", "-q", "-m", "remove gone", cwd=other)
        self.git("merge", "-q", "--no-edit", "remover", cwd=self.repo().verify_dir)
        self.ok("next")
        rid = store.list_requests(self.repo(), "processing")[0]["id"]
        out = self.ok("verify", rid)
        self.assertTrue(out.startswith("CONFLICT gone.txt"))
        self.assertEqual(self.git("status", "--porcelain", cwd=self.repo().verify_dir), "")


LATIN1 = "caf\u00e9"


class EncodingTest(RepoTestCase):
    """Content that is not UTF-8 (Latin-1, and UTF-16 as Xcode writes .strings files) must not crash anything."""
    config = {"verify": ["true"], "hotFiles": {"*.strings": "union"}}

    def setUp(self):
        super().setUp()
        self.init_steward()

    def commit_bytes(self, rel, data, msg, cwd):
        with open(os.path.join(cwd, rel), "wb") as f:
            f.write(data)
        self.git("add", "-A", cwd=cwd)
        self.git("commit", "-q", "-m", msg, cwd=cwd)

    def submit_bytes(self, branch, rel, data):
        wt = self.worktree(branch)
        self.commit_bytes(rel, data, f"change {rel}", wt)
        self.ok("submit", cwd=wt)
        rid = store.locate(self.repo(), branch, ("queue",))[1]["id"]
        self.ok("next", "--id", rid)
        return rid

    def land_bytes(self, rel, data):
        other = self.worktree("landed")
        self.commit_bytes(rel, data, "landed", other)
        self.git("merge", "-q", "--no-edit", "landed", cwd=self.repo().verify_dir)

    def verify_state(self, rid):
        self.assertEqual(self.git("status", "--porcelain", cwd=self.repo().verify_dir), "")
        return store.locate(self.repo(), rid, ("processing",))[1]["lastVerify"]

    def test_review_latin1_and_utf16(self):
        wt = self.worktree("claude/a")
        self.commit_bytes("menu.txt", f"{LATIN1}\n".encode("latin-1"), "latin1", wt)
        self.commit_bytes("Localizable.strings", '"k" = "v";\n'.encode("utf-16"), "utf16", wt)
        self.ok("submit", cwd=wt)
        rid = store.locate(self.repo(), "claude/a", ("queue",))[1]["id"]
        self.ok("next", "--id", rid)
        data = json.loads(self.ok("review", rid))
        self.assertTrue(os.path.exists(data["diffPath"]))
        with open(data["diffPath"], encoding="utf-8") as f:
            self.assertIn("menu.txt", f.read())

    def test_verify_conflict_in_latin1_file(self):
        rid = self.submit_bytes("claude/a", "menu.txt", f"{LATIN1} mine\n".encode("latin-1"))
        self.land_bytes("menu.txt", f"{LATIN1} theirs\n".encode("latin-1"))
        self.assertTrue(self.ok("verify", rid).startswith("CONFLICT menu.txt"))
        last = self.verify_state(rid)
        with open(last["evidence"], encoding="utf-8") as f:
            text = f.read()
        self.assertIn("mine", text)
        self.assertIn("theirs", text)

    def test_verify_conflict_in_utf16_file(self):
        rid = self.submit_bytes("claude/a", "menu.txt", "mine\n".encode("utf-16"))
        self.land_bytes("menu.txt", "theirs\n".encode("utf-16"))
        self.assertTrue(self.ok("verify", rid).startswith("CONFLICT menu.txt"))
        self.assertEqual(self.verify_state(rid)["result"], "CONFLICT")

    def test_union_latin1_hot_file(self):
        rid = self.submit_bytes("claude/a", "fr.strings", f"a={LATIN1}\n".encode("latin-1"))
        self.land_bytes("fr.strings", "b=bye\n".encode("latin-1"))
        self.assertTrue(self.ok("verify", rid).startswith("PASS"))
        with open(os.path.join(self.repo().verify_dir, "fr.strings"), "rb") as f:
            merged = f.read().decode("latin-1")
        self.assertIn(f"a={LATIN1}", merged)
        self.assertIn("b=bye", merged)

    def test_union_utf16_hot_file(self):
        rid = self.submit_bytes("claude/a", "Localizable.strings", f'"a" = "{LATIN1}";\n'.encode("utf-16"))
        self.land_bytes("Localizable.strings", '"b" = "bye";\n'.encode("utf-16"))
        self.assertTrue(self.ok("verify", rid).startswith("PASS"))
        with open(os.path.join(self.repo().verify_dir, "Localizable.strings"), "rb") as f:
            raw = f.read()
        self.assertEqual(raw[:2], "x".encode("utf-16")[:2])  # BOM kept
        merged = raw.decode("utf-16")
        self.assertIn(f'"a" = "{LATIN1}";', merged)
        self.assertIn('"b" = "bye";', merged)

    def test_undecodable_hot_file_is_left_as_a_conflict(self):
        rid = self.submit_bytes("claude/a", "Localizable.strings", '"a" = "1";\n'.encode("utf-16-le"))  # no BOM
        self.land_bytes("Localizable.strings", '"b" = "2";\n'.encode("utf-16"))
        self.assertTrue(self.ok("verify", rid).startswith("CONFLICT Localizable.strings"))
        self.assertEqual(self.verify_state(rid)["result"], "CONFLICT")


if __name__ == "__main__":
    unittest.main()
