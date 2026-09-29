---
name: submit
description: Submit a finished work branch to the merge steward, handle a rejection notice from the steward, or relay the user's approval/denial of a held branch. Use when a hook says the branch has unsubmitted commits, when the steward rejects your merge request, or when the user says 批准/approve or 否决/deny a branch.
---

# Working with the merge steward

This repository merges through a merge steward session. Never merge into `main` or the integration branch yourself.

## The CLI

Run `steward <command>`. If `steward` is not on PATH, run `python3 <this skill's base directory>/../../bin/steward <command>`.

## Submitting

Submit only when your task is finished:

1. Commit all of your work. The working tree must be clean (untracked scratch files are fine).
2. Run the project's tests yourself and make sure they pass.
3. Get this session's name from ListAgents (the first output line: `This session is <name> — ...`), or omit the flag if ListAgents is unavailable. Run `steward submit --session-name "<name>"` (use the name other sessions can message you by; omit the flag if you cannot tell). Give the Bash tool a long timeout (600000 ms): a `hotFiles` regenerate command may run during the rebase.
   - It rebases your branch onto the integration branch first. Conflicts only in the repository's `hotFiles` are resolved automatically by their rules, and the output lists them (`auto-resolved hot files during the rebase: ...`). The steward will re-verify the result; if your tests fail on it, fix, commit and submit again (the old request is superseded).
   - If `steward submit` was killed or interrupted anyway, run `git rebase --abort` (if git says a rebase is in progress) before retrying.
   - If it reports rebase conflicts, the rebase was aborted and your branch is unchanged: run `git rebase <integration branch>`, resolve them, re-run the tests, and submit again. If it reports that the rebase failed for another reason, fix what git's message says and submit again.
   - On success it prints the request id and the steward's session name.
4. If SendMessage is available, message the steward session: the request id and one line on what the branch does.

If the task is not finished, do not submit; a hook asks you at most once per commit.

## When the steward rejects your request

You will see the notice from a hook, from a steward message, or via `steward inbox`. It contains the reason, any resolution the steward decided with other sessions, and raw evidence (conflict hunks or the failing log tail).

1. Follow the steward's resolution exactly; it has coordinated it with the other sessions.
2. Fix the branch, run the tests, commit.
3. `steward inbox --ack`.
4. Submit again. (A successful `steward submit` also acknowledges the notice, so hooks stop repeating it.)

If the notice says the **user denied** the request, do not resubmit the change as it is: tell the user, ask what they want instead, and run `steward inbox --ack` once you have their answer.

## Relaying the user's approval

If the user tells you to approve (批准) or deny (否决) a branch that the steward is holding:

- `steward approve <branch>` or `steward deny <branch> --reason "<the user's words>"`.
- If SendMessage is available, tell the steward what you did.
