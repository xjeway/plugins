import json
import os
import unittest
from unittest import mock

from helpers import RepoTestCase

from steward import hooks, inbox, store


class HookTest(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.make_integration()
        self.wt = self.worktree("claude/a")

    def stop(self, cwd=None, active=False):
        return hooks.run("stop", json.dumps({"cwd": cwd or self.wt, "stop_hook_active": active, "session_id": "s"}))

    def prompt(self, cwd=None):
        return hooks.run("prompt", json.dumps({"cwd": cwd or self.wt, "session_id": "s"}))

    def notice(self):
        req = {"id": "r1", "branch": "claude/a"}
        inbox.write_notice(self.repo(), req, "conflict", "a.txt clashes with claude/b", "<<<<<<< ours")

    def test_passes_when_nothing_to_do(self):
        self.assertEqual(self.stop(), (0, "", ""))

    def test_passes_on_main_and_outside_repos(self):
        self.assertEqual(self.stop(cwd=self.root), (0, "", ""))
        self.assertEqual(self.stop(cwd=self.tmp), (0, "", ""))

    def test_passes_without_config(self):
        os.remove(os.path.join(self.root, ".claude", "steward.json"))
        os.remove(os.path.join(self.wt, ".claude", "steward.json"))
        self.commit("a.txt", "a\n", "a", cwd=self.wt)
        self.assertEqual(self.stop(), (0, "", ""))

    def test_asks_once_per_head(self):
        self.commit("a.txt", "a\n", "a", cwd=self.wt)
        code, _, err = self.stop()
        self.assertEqual(code, 2)
        self.assertIn("steward submit", err)
        self.assertEqual(self.stop(), (0, "", ""))
        self.commit("a.txt", "b\n", "b", cwd=self.wt)
        self.assertEqual(self.stop()[0], 2)

    def test_does_not_ask_when_active_or_submitted(self):
        self.commit("a.txt", "a\n", "a", cwd=self.wt)
        self.assertEqual(self.stop(active=True), (0, "", ""))
        self.ok("submit", cwd=self.wt)
        self.assertEqual(self.stop(), (0, "", ""))

    def test_delivers_notice_until_acked(self):
        self.notice()
        code, _, err = self.stop()
        self.assertEqual(code, 2)
        self.assertIn("a.txt clashes with claude/b", err)
        self.assertIn("<<<<<<< ours", err)
        self.assertEqual(self.stop(active=True), (0, "", ""))
        self.assertEqual(self.stop()[0], 2)
        self.ok("inbox", "--ack", cwd=self.wt)
        self.assertEqual(self.stop(), (0, "", ""))
        self.assertTrue(os.listdir(self.repo().path("inbox", "acked")))

    def test_prompt_hook_injects_notice(self):
        self.assertEqual(self.prompt(), (0, "", ""))
        self.notice()
        code, out, _ = self.prompt()
        self.assertEqual(code, 0)
        payload = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(payload["hookEventName"], "UserPromptSubmit")
        self.assertIn("a.txt clashes", payload["additionalContext"])

    def test_crash_is_logged_and_passes(self):
        self.write(".claude/steward.json", "{not json")
        self.assertEqual(self.stop(), (0, "", ""))
        with open(self.repo().path("hook.log")) as f:
            self.assertIn("ConfigError", f.read())

    def test_non_object_payload_passes(self):
        self.assertEqual(hooks.run("stop", "[]"), (0, "", ""))

    def test_inbox_command(self):
        self.assertIn("no pending notice", self.ok("inbox", cwd=self.wt))
        self.notice()
        self.assertIn("a.txt clashes", self.ok("inbox", cwd=self.wt))

    def test_successful_submit_acks_the_pending_notice(self):
        self.notice()
        self.commit("a.txt", "fixed\n", "fix", cwd=self.wt)
        out = self.ok("submit", cwd=self.wt)
        self.assertIn("acknowledged the steward's notice about r1", out)
        self.assertIsNone(inbox.pending(self.repo(), "claude/a"))
        self.assertEqual(os.listdir(self.repo().path("inbox", "acked")), ["claude__a-r1.json"])
        self.assertEqual(self.stop(), (0, "", ""))

    def test_failed_submit_keeps_the_notice(self):
        self.notice()
        self.cli("submit", cwd=self.wt)  # nothing to submit
        self.assertIsNotNone(inbox.pending(self.repo(), "claude/a"))

    def test_denied_notice_does_not_say_resubmit(self):
        req = {"id": "r2", "branch": "claude/a"}
        text = inbox.format_notice(inbox.write_notice(self.repo(), req, "denied", "not this quarter", ""))
        self.assertIn("not this quarter", text)
        self.assertIn("denied", text)
        self.assertNotIn("submit again", text)
        self.assertIn("user", text)
        conflict = inbox.format_notice(inbox.write_notice(self.repo(), req, "conflict", "clash", "<<<"))
        self.assertIn("submit again", conflict)

    def test_ack_racing_another_ack_returns_none(self):
        self.notice()
        notice = inbox.pending(self.repo(), "claude/a")
        os.remove(self.repo().path("inbox", "claude__a.json"))  # acknowledged elsewhere after we looked
        with mock.patch.object(inbox, "pending", return_value=notice):
            self.assertIsNone(inbox.ack(self.repo(), "claude/a"))


if __name__ == "__main__":
    unittest.main()
