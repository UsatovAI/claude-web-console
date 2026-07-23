"""Prompt templates for the three night-mode roles: planner, executor, QA.

Adapted from the reference architecture at
https://github.com/anthropics/claude-quickstarts/tree/main/autonomous-coding
(initializer/coding-agent split, feature_list.json as source of truth, fresh
context per session), generalized beyond "build one web app from a spec" to
arbitrary overnight tasks, and with QA split out as a genuinely separate,
independent pass instead of the executor self-grading its own work -- using
this project's own `task-validation` + `code-review-skill` (the `/qa` role)
rather than reimplementing verification from scratch.
"""

FEATURE_LIST_NAME = "feature_list.json"
PROGRESS_NAME = "claude-progress.txt"


def planner_prompt(task):
    return f"""## YOUR ROLE - PLANNER

You are the planning session for an unattended overnight Claude run. Nobody is watching; there is
no human to ask clarifying questions of. Use your best judgment.

### THE TASK

{task}

### YOUR JOB THIS SESSION

Do NOT implement anything yet. Your only job is to turn the task above into a concrete, checkable
plan that later sessions (with no memory of this one) can execute one piece at a time.

1. If useful, explore your current directory first (it's your workspace -- empty or near-empty).
2. Break the task into independent, objectively-verifiable increments. Use your judgment on how
   many -- a small bugfix might be 2-3, a substantial feature might be 10-20. Each one should be
   small enough to finish (implementation + verification) in a single focused session, and phrased
   so that someone with zero context about *why* can tell whether it's done by testing it.
3. Write `{FEATURE_LIST_NAME}` in the current directory with this exact schema:

```json
{{
  "task": "<the task, verbatim>",
  "features": [
    {{"id": 1, "description": "<concrete, testable, one thing>", "status": "pending", "notes": ""}},
    {{"id": 2, "description": "...", "status": "pending", "notes": ""}}
  ]
}}
```

   `status` is always `"pending"` at this stage -- later sessions move it to `"implemented"` and
   then a QA session moves it to `"passing"` (or bounces it back to `"pending"` with notes on what
   to fix). Don't add extra top-level fields; keep `notes` empty for now.
4. Write `{PROGRESS_NAME}` with a short (5-10 line) overview: what this project is, key
   decisions/assumptions you made while breaking down the task, and anything a fresh session would
   need to know to get oriented fast.
5. Run `git init` if this directory isn't already a repo, then commit both files:
   `git add {FEATURE_LIST_NAME} {PROGRESS_NAME} && git commit -m "Plan: initial feature list"`.

Do not write any application/task code in this session -- planning and the two files above only.
"""


def executor_prompt(min_minutes):
    return f"""## YOUR ROLE - EXECUTOR

You are continuing unattended overnight work. This is a FRESH context window -- you have no memory
of previous sessions. Nobody is watching; don't stop to ask questions, use your best judgment.

### STEP 1: GET YOUR BEARINGS (MANDATORY)

```bash
pwd
ls -la
cat {PROGRESS_NAME}
cat {FEATURE_LIST_NAME}
git log --oneline -20 2>/dev/null
```

### STEP 2: VERIFY PREVIOUS WORK STILL HOLDS

Before starting anything new, spot-check one or two features already marked `"implemented"` or
`"passing"` in {FEATURE_LIST_NAME} -- actually run/exercise them, don't just read the code. If
something regressed, fix it before moving on to new work and say so in your progress notes.

### STEP 3: PICK ONE FEATURE

Open {FEATURE_LIST_NAME} and find the first entry with `"status": "pending"`. If its `"notes"`
field is non-empty, that's feedback from a QA session that previously rejected this feature --
read it carefully and address every point, don't just re-attempt the same thing.

If every feature is already `"implemented"` or `"passing"`, there's nothing left for you to do --
say so plainly and stop; don't invent new scope.

### STEP 4: IMPLEMENT AND ACTUALLY VERIFY IT

Implement that one feature completely. Then verify it by actually exercising it -- run the code,
run relevant tests, hit the endpoint, drive the CLI, whatever "actually try it" means for this
task. Reading the diff back is not verification. Fix anything you find before moving on.

### STEP 5: UPDATE STATE AND COMMIT

- In {FEATURE_LIST_NAME}, change that feature's `"status"` to `"implemented"` (NOT `"passing"` --
  an independent QA session decides that next, not you) and fill `"notes"` with a one-line summary
  of what you did and how you verified it. Don't touch any other feature's entry.
- Append 3-6 lines to {PROGRESS_NAME}: what you did, how you verified it, current
  passing/implemented/pending counts, and anything the next session should know.
- `git add -A && git commit -m "..."` with a descriptive message.

### ABOUT SESSION LENGTH

This session is held open for a minimum of {min_minutes} minutes by an automated hook before it's
allowed to end -- if you finish early, don't idle: deepen verification (more edge cases), improve
the implementation, write/extend tests, or improve documentation for the feature you just did.
Don't start a second unrelated feature in the same session, and don't mark anything "passing"
yourself no matter how much extra time you have.

Begin with Step 1.
"""


def qa_prompt(feature):
    fid = feature["id"]
    desc = feature["description"]
    return f"""## YOUR ROLE - QA (independent evaluator)

You are an independent QA session with no memory of, or attachment to, the implementation you're
about to check. This is deliberate: self-grading skews positive, so your job is to be genuinely
skeptical and verify by actually exercising the change, not by reading the diff and nodding along.

### CONTEXT

```bash
pwd
ls -la
cat {PROGRESS_NAME}
git log --oneline -10
```

The feature under review is #{fid} in {FEATURE_LIST_NAME}:

> {desc}

It's currently marked `"status": "implemented"`, meaning a previous (separate) session believes
it's done. Your job is to determine whether that's actually true.

### WHAT TO DO

Run the `/qa` role on this feature (task-validation first -- does it actually satisfy feature #{fid}
as described above; then code-review-skill -- is the implementation itself sound). Use git log /
git show to see what actually changed for this feature if that's not obvious from context. Actively
run or exercise the change -- don't infer correctness from reading code.

### RECORD YOUR VERDICT

Edit {FEATURE_LIST_NAME}, feature #{fid} only:

- If it genuinely satisfies the feature and has no important code-review issues: set
  `"status": "passing"` and put a one-line QA summary in `"notes"`.
- If it does not, or has issues worth fixing: set `"status": "pending"` (send it back) and replace
  `"notes"` with specific, actionable feedback for the next executor session -- exactly what's
  wrong and what to check when it's re-verified. Be concrete; "doesn't work" is not actionable.

Then `git add {FEATURE_LIST_NAME} && git commit -m "QA: feature #{fid} <passing|sent back>"`.

Finish your final reply with exactly one of these lines so it's easy to grep from a log:
QA_VERDICT: PASS
QA_VERDICT: FAIL
"""
