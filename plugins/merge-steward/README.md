# merge-steward

Run dozens of Claude Code sessions on one repository without them trampling each other's merges.

One **steward** session owns an `integration` branch. Worker sessions (each in its own worktree) submit finished branches; the steward reviews them, coordinates workers whose changes collide, trial-merges and runs the project's checks in a private worktree, merges what passes, bounces the rest back with the raw evidence, and asks you only about risky changes. `integration` reaches `main` through a pull request you review.

Requires `git` and `python3` (3.9+); `gh` for promoting to `main`.

## Enable it in a repository

Commit `.claude/steward.json`:

```json
{
  "verify": ["npm ci", "npm test"],
  "hotFiles": { "package-lock.json": "regenerate:npm install --package-lock-only" },
  "approval": {
    "riskyPaths": [".github/*"],
    "rules": ["removing an existing feature", "auth or permission changes"]
  },
  "promote": { "everyNMerges": 5 }
}
```

| Key | Default | Meaning |
|---|---|---|
| `verify` | required | Shell commands run, in order, on the trial merge. |
| `integrationBranch` / `mainBranch` | `integration` / `main` | |
| `verifyTimeoutSec` | `1800` | Total budget for the verify commands. |
| `hotFiles` | `{}` | glob → `union` (keep both sides' lines; UTF-16 files with a byte-order mark are handled) or `regenerate:<command>` (take integration's copy, rerun the command). Applied to conflicts in the steward's trial merge, and in `steward submit`'s rebase when every conflicted file has a rule. |
| `approval.mode` | `risky` | `risky`: only risky requests wait for you; `always`; `never`. |
| `approval.riskyPaths` | `[]` | globs that make a request risky. |
| `approval.riskyWhenDeletingFiles` | `true` | deleting any file makes a request risky. |
| `approval.rules` | `[]` | plain-language rules the steward checks while reviewing. |
| `promote.everyNMerges` | `5` | open the integration → main PR after this many merges. |

In `steward submit`'s rebase, auto-resolution continues with `git rebase --continue`, which can run the repository's commit hooks; if a hook fails, the rebase is aborted and the worker resolves the conflicts by hand as usual.

Repositories without this file are untouched: the hooks exit immediately.

## Use it

1. In the main checkout (on `main`), start a session and say: "be the merge steward" (skill `merge-steward:steward`).
2. Start worker sessions in worktrees as usual. When a worker stops with unsubmitted commits, its Stop hook asks it (once per commit) to submit via `merge-steward:submit`.
3. When the steward holds a risky branch, tell any session "批准 <branch>" / "approve <branch>" (or deny).
4. Merge the promote PR with a **merge commit**.

## CLI

`bin/steward <command>`: `submit`, `inbox`, `init`, `watch`, `status`, `next`, `review`, `verify`, `verify-base`, `merge`, `reject`, `hold`, `approve`, `deny`, `requeue`, `log-event`, `promote`, `metrics`. State lives in `$(git rev-parse --git-common-dir)/steward/`. The steward trial-merges and runs `verify` in its own worktree next to the main checkout, `<main checkout>.steward-verify` (for `/x/repo`, `/x/repo.steward-verify`), so tools that look upward for `node_modules` or config never see the main checkout's copies.

## Development

```bash
cd plugins/merge-steward
python3 -m unittest discover -s tests -v
```
