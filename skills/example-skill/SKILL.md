---
name: example-skill
description: Annotated template that teaches how to write an Agent Skill for this bundle — the frontmatter rules, the three levels of progressive disclosure, and what changes when the deployment is Kubernetes. Use when authoring a new skill, or when you want a known-good skeleton to copy.
---

# Example Skill

A **skill** is a reusable capability this bundle ships under `skills/`. It is
the packaged sibling of a [protocol](../../protocols/ask-the-runbooks.md): where
a protocol is a single Markdown playbook, a skill is a *folder* that can also
bundle reference documents and runnable scripts alongside its instructions.

This file is both a working example and the template. Copy the folder, rename
it, replace the body.

## Anatomy

```
skills/
  example-skill/
    SKILL.md         # this file — frontmatter + instructions
    REFERENCE.md     # long-form detail, loaded on demand (Level 2)
    scripts/
      greet.py       # a deterministic stdlib script, run on demand (Level 3)
```

`SKILL.md` opens with YAML frontmatter fenced by `---`, and the frontmatter must
be the very first thing in the file:

```
---
name: example-skill        # MUST equal the folder name
description: <what it does AND when to use it — one or two sentences>
---
```

`name` must match the directory name exactly. `description` is the trigger: it
is the only part of a skill that is always in the model's context, so it must
say both what the skill does and when to reach for it.

## Progressive disclosure — the three levels

1. **The `description`** sits in the system-prompt roster, always. Cheap.
2. **This body and any sibling `.md`** load only when the skill is used.
3. **Scripts are RUN, not read into context** — deterministic logic executes
   instead of being re-derived, and costs no tokens.

The optional `allowed-tools` frontmatter field is **advisory metadata only**.
fleet parses it and surfaces it for review; it does **not** enforce it as an
authorization boundary. Govern consequential tools through `agent_policy` in
`manifest.yaml` instead.

## What Kubernetes changes about skills

Levels 2 and 3 read files **from inside the sandbox**, and a sandbox pod mounts
only the workspace claim. Two things must both be true for a skill to work on
the cluster path:

1. `deploy/kubernetes/Containerfile.sandbox` bakes `skills/` into the sandbox
   image at the same absolute path the control plane uses, and the deployment
   declares it (`bundle_docs_in_image`). It does; keep it that way.
2. `manifest.yaml` sets `skills_builtin: false`. With fleet's built-in skills
   pack inherited, the skills directory is a *merged* tree under the control
   plane's data PVC — a path no image can carry — and every skill collapses to
   Level 1: the agent sees this description and can open nothing.

If you are writing a skill and its `REFERENCE.md` cannot be read in a sandbox,
that is the cause. See the README's `skills_builtin` section.

## Running the script

```sh
python3 skills/example-skill/scripts/greet.py "Rowan"
```

Scripts are invoked, not sourced, so no exec bit is needed — the sandbox image
makes `skills/` read-only and non-executable on purpose.

## Writing a good one

- Make it a capability a careful teammate would hand off, not a script bolted
  to one dataset.
- Put the *when* in the description, not just the *what*.
- Push determinism into `scripts/` and judgment into the body.
- Standard library only, unless the package is in `sandbox/Containerfile`.
