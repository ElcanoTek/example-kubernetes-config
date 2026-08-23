# Protocol: Weekly Platform Report

Produce the week's platform report as a written artifact in the workspace:
gather the inputs, compute the numbers in Python, leave what cannot be computed
blank, and say where the file landed.

Use this for the recurring weekly run (the "Weekly Platform Report" task
template schedules it) and for any ad-hoc "give me this week's numbers" ask.
**This is a scheduled playbook: assume nobody is watching.** No one can answer a
question mid-run, so decide, act, verify, and report — and when something is
missing, record the gap in the artifact instead of waiting on a human.

## Steps

1. **Read the reporting rules** with `rb_get_runbook` for `rb-004` ("Reading
   the weekly platform report"). It says which numbers are exact, which are only
   as good as their source, and what to do with the rest. It governs this
   protocol; where the two disagree, the runbook wins.
2. **Fix the reporting window** before you read anything. State it explicitly in
   the artifact — start date, end date, timezone — and use the same window for
   every figure. A report whose numbers cover different windows is worse than no
   report.
3. **Gather the inputs** from the `inputs/` directory of your working directory
   — the workspace fleet gave this run, the same filesystem the manifest maps
   into connectors as `${FLEET_WORKSPACE}`. List it with `bash` first, then read
   what is there:

   ```sh
   ls -la inputs/
   ```

   Print the file names and sizes you found. An empty or missing `inputs/`
   directory is a finding, not a crash: say so in the artifact and continue with
   whatever other sources are available.
4. **Pull anything the connectors can give you.** Per rb-004, deployment count
   and change-failure rate come from the release tracker and are exact — use
   `rt_list_releases` when that connector is live. If it is dark (no
   `DEPLOY_API_TOKEN` on this deployment), those cells are uncomputable; see
   step 6. Time-to-restore is computed from incident timelines — the ones
   [`incident-timeline.md`](incident-timeline.md) writes — and is only as good
   as they are. Say so in the caveats.
5. **Compute in Python, printing every intermediate.** Load the real files with
   `run_python`; do not eyeball a total off a listing. Every cell ends with a
   printed value, and each print names what it is:

   ```python
   print("window:", start, "→", end)
   print("rows loaded:", len(rows), "from", path)
   print("deployments:", deployments)
   print("change-failure rate:", f"{100.0 * failed / total:.1f}%" if total else "N/A")
   ```

   Guard every denominator. A ratio with no denominator prints `N/A`, never `0`.
6. **Leave uncomputable cells blank.** This is the rule the report exists to
   uphold, and rb-004 states it directly: anything the report cannot compute
   from a source is left blank, never estimated — a blank cell prompts a
   question, and an invented number prevents one. A blank cell gets a one-line
   reason in the "Gaps" section ("release tracker not connected on this
   deployment", "no incident timelines in `inputs/` for this window"). Do not
   carry last week's figure forward, do not interpolate, and do not write
   "~40".
7. **Compare against last week only if last week's artifact is actually
   present.** If it is in the workspace, read it and show the delta. If it is
   not, say the comparison was unavailable — do not reconstruct it.
8. **Write the artifact** to the workspace as
   `weekly-platform-report-<ISO date>.md`, where `<ISO date>` is the **last day
   of the reporting window** in `YYYY-MM-DD` form (so consecutive weeks sort).
   Write it with the file tools or `bash`; do not print the whole report as your
   only output — an unattended run's message is not an artifact.
9. **Report the path** in your final message, along with the window, the
   headline numbers, the count of blank cells, and anything that needs a human
   decision. Assume the reader has only your final message and the file.

## What goes in the artifact

- **Window** — start, end, timezone.
- **Headline numbers** — a table, one row per metric, with a `basis` column
  naming the source and the formula behind each figure.
- **What changed** — the deltas against last week, when last week's artifact was
  available.
- **Gaps** — every blank cell and the one-line reason it is blank.
- **Caveats** — which figures are exact and which inherit the quality of their
  source (rb-004 draws that line for you).
- **Needs a human** — anything a person has to decide, with what you would need
  to resolve it.

## When an input is unreadable

Name the file, say what you tried, and continue with the rest. One corrupt
export blanks the cells it feeds; it does not abort the report. Surface every
skipped input in "Gaps" so the coverage hole is visible in the artifact rather
than only in the run log.

If a read of a file under `protocols/` refuses or cannot find it, try `bash`
(`cat protocols/…`) before concluding it is missing — depending on how this
deployment is built, the file tools may not reach that directory even though the
file is there. If both fail, say which path you tried, and never improvise the
playbook you could not read.

## Output shape

- The written artifact at `weekly-platform-report-<ISO date>.md`, in the
  workspace.
- A final message with: the path, the window, the headline figures, the number
  of blank cells and why, and anything needing a human decision.
- `Source: Reading the weekly platform report (rb-004)`, plus every input file
  you read, by name.
