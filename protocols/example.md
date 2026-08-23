# Protocol: Example (the annotated template)

Copy this file, rename it, replace the body. It is both a runnable protocol and
the template every other protocol in this bundle follows.

## What a protocol is

A **protocol** is a reusable playbook this bundle ships under `protocols/`. It
encodes "the way we do this here" once, so an interactive chat and a scheduled
task run the same procedure the same way. A good protocol reads like a checklist
a careful teammate would follow: it names the exact tools to call, the order to
call them in, what counts as done, and what to do when a step cannot be
completed.

A protocol is **not** a skill. A skill (`skills/<name>/SKILL.md`) is a folder
that can bundle reference documents and runnable scripts, and it opens with YAML
frontmatter. A protocol is one file of prose. If your playbook needs to ship
code beside it, write a skill; if it is a procedure, write a protocol.

## When the agent reads one

- When the user names it ("run the incident-timeline protocol").
- When a task template's prompt names it — that is how the Operations Center's
  scheduled tasks reach these files.
- When it is plainly relevant to the request and the agent goes looking.

**Read once per run.** The system prompts tell the agent to read an applicable
protocol once and then follow it step by step — not to re-read it between steps
of the same conversation or task. Write for a reader who has the whole file in
context from here on: put the constraint next to the step it constrains rather
than in a preamble the agent will not look back at.

## The file format

Plain Markdown with **no frontmatter**. Nothing parses a protocol; fleet reads
the directory and the agent reads the file, so the only structure that matters
is the structure a reader can follow. (A `.yaml` protocol is also accepted, and
is worth it only for a genuinely tabular playbook.) Keep the filename to the
lowercase, hyphenated name the task prompt will use.

Two Kubernetes-path notes, because this bundle is the cluster one:

- `protocols/` is already baked into the sandbox image by
  `deploy/kubernetes/Containerfile.sandbox`, so a **new file in this directory
  needs no Containerfile change** — but it does need **both images rebuilt** to
  reach a sandbox, because the sandbox image carries a snapshot rather than a
  mount. `make images` builds the pair.
- A protocol that tells the agent to read a file from a directory *not* in that
  Containerfile will work on the single-box podman install and fail only on a
  cluster. Add the `COPY` line in the same PR, and say so in the PR body.

## The skeleton

Everything below is the shape to copy. Five sections; drop one only when it
genuinely does not apply.

### Goal

One or two sentences: what this protocol produces and for whom. Lead the file
with it, before any steps — the agent uses it to decide whether this is the
right playbook at all.

### Inputs

What the run needs and where each thing comes from. A short table works well:

| Input | Source | Notes |
| --- | --- | --- |
| `goal` | the task prompt | what this run should accomplish, in one line |
| evidence | the workspace | files the run reads; name the directory explicitly |

If the task template that invokes this protocol passes a `{placeholder}`, name
it here and say how to interpret a vague value.

### Steps

Numbered, in order, each one a coherent unit of work that names the tool it
uses (`bash`, `run_python`, `view_file`, `rb_search`, …). Two habits that make
the difference between a protocol that is followed and one that is
approximated:

1. **End every step with something observable** — a printed value, a row count,
   a written file and its path. A silent step gives the run nothing to verify.
2. **Put the rule beside the step.** "Guard the denominator" belongs in the step
   that divides, not in a principles section at the bottom.

### Output shape

Exactly what the run delivers: the sections of the final message, any artifact
written to the workspace and the naming convention for it, and the citation
lines. Be specific — `Source: <title> (<id>)` is a shape; "cite your sources" is
a wish.

### When you can't

The most important section, and the one most often skipped. Say what to do when
an input is missing, a tool is dark, or a read fails:

- Name the failure in the output rather than working around it silently.
- Produce the smaller correct result instead of the complete invented one.
- Never substitute a plausible-sounding version of something you could not read
  or compute.

Include the deployment-shape fallback wherever the protocol reads a bundle file:
if a read of a path under `protocols/` refuses or cannot find it, try `bash`
(`cat protocols/…`) before concluding it is missing, and if both fail, say which
path you tried. That one line is what makes a mis-declared or older deployment
degrade instead of stall.

## Worked example: running this protocol

There is a real, runnable procedure in here — the template is not a dead file.

1. **Restate the goal** in one sentence so a misunderstanding surfaces before
   any work happens.
2. **Gather the inputs.** Read the attached files, list the workspace, and pull
   from the runbook library or a connector when the task calls for it. Print
   what you loaded.
3. **Do the work with the tools.** Compute in the sandbox, draft the artifact,
   search the runbooks — do the task rather than describing how it could be
   done.
4. **Validate.** Re-read the goal and confirm the output answers it, and that
   every claim traces to a tool result, a cited source, or a stated assumption.
5. **Deliver** in the output shape above: answer first, detail below, gaps
   named, artifact paths given.

## The other protocols in this bundle

Read one of these before writing your own — each shows a different shape:

- [`ask-the-runbooks.md`](ask-the-runbooks.md) — grounded Q&A with a citation
  and a hard refusal to invent policy.
- [`incident-timeline.md`](incident-timeline.md) — evidence gathering, a table,
  and inference marked as inference.
- [`weekly-platform-report.md`](weekly-platform-report.md) — a scheduled
  gather → compute → write run that leaves what it cannot compute blank.
