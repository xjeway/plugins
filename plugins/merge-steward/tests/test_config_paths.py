import os
import unittest

from helpers import RepoTestCase

from steward.config import ConfigError, parse_config
from steward.paths import find_repo, safe_name


class PathsTest(RepoTestCase):
    def test_find_repo_from_worktree_shares_common_dir(self):
        wt = self.worktree("claude/a")
        main_repo, wt_repo = self.repo(), self.repo(wt)
        self.assertEqual(main_repo.common_dir, wt_repo.common_dir)
        self.assertEqual(wt_repo.toplevel, os.path.realpath(wt))
        self.assertEqual(wt_repo.main_checkout, self.root)
        self.assertEqual(wt_repo.branch(), "claude/a")

    def test_verify_dir_is_a_sibling_of_the_main_checkout(self):
        wt = self.worktree("claude/a")
        for r in (self.repo(), self.repo(wt)):
            self.assertEqual(r.verify_dir, self.root + ".steward-verify")
            self.assertFalse(r.verify_dir.startswith(self.root + os.sep))

    def test_find_repo_outside_git_is_none(self):
        self.assertIsNone(find_repo(self.tmp))

    def test_safe_name(self):
        self.assertEqual(safe_name("claude/x/y"), "claude__x__y")


class ConfigTest(RepoTestCase):
    def test_defaults(self):
        cfg = parse_config({"verify": ["make test"]})
        self.assertEqual((cfg.integration, cfg.main, cfg.approval_mode, cfg.every_n_merges), ("integration", "main", "risky", 5))
        self.assertTrue(cfg.risky_when_deleting)

    def test_invalid(self):
        for raw in ({}, {"verify": []}, {"verify": ["x"], "approval": {"mode": "sometimes"}},
                    {"verify": ["x"], "hotFiles": {"a": "regenerate:"}}, {"verify": ["x"], "hotFiles": {"a": "ours"}},
                    [], {"verify": ["x"], "hotFiles": []}, {"verify": ["x"], "approval": "risky"},
                    {"verify": ["x"], "verifyTimeoutSec": "abc"}, {"verify": ["x"], "verifyTimeoutSec": None},
                    {"verify": ["x"], "approval": {"riskyPaths": ".github/*"}}, {"verify": ["x"], "approval": {"rules": "x"}},
                    {"verify": ["x"], "promote": {"everyNMerges": 0}}):
            with self.subTest(raw=raw), self.assertRaises(ConfigError):
                parse_config(raw)

    def test_main_checkout_config_wins_over_worktree_copy(self):
        wt = self.worktree("claude/a")
        self.write_config({"verify": ["from-main"]})
        from steward.config import load_config
        self.assertEqual(load_config(self.repo(wt)).verify, ["from-main"])

    def test_missing_config_is_none(self):
        os.remove(os.path.join(self.root, ".claude", "steward.json"))
        self.git("commit", "-qam", "drop config")
        from steward.config import load_config
        self.assertIsNone(load_config(self.repo()))


if __name__ == "__main__":
    unittest.main()
