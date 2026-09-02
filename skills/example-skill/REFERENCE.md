# Reference: frontmatter and loading rules

Long-form detail that does not belong in `SKILL.md`. This file is **Level 2**:
it is not in the model's context until the skill is used and the agent opens it.

## Frontmatter fields

| Field | Required | Notes |
| --- | --- | --- |
| `name` | yes | Must equal the folder name exactly. A mismatch is a load-time validation error. |
| `description` | yes | The trigger. Always in context. Say what it does *and* when to use it. |
| `allowed-tools` | no | Parsed and surfaced for review. **Not enforced** as an authorization boundary — it structurally cannot be one, since skills are read on demand mid-turn and a skill can never exceed the turn's existing sandbox, MCP, and approval limits. |

## How fleet finds skills

`clientconfig.ReadSkills` walks the bundle's skills directory at boot and reads
each `<name>/SKILL.md`. That directory is a **merged tree** fleet materializes
from three sources, lowest precedence first: fleet's built-in pack (unless
`skills_builtin: false`; minus `skills_hidden`), each Agent Plugin's `skills/`
(`plugins/`), and this bundle's own `skills/`. A later source overwrites an
earlier one, so a bundle skill with a built-in's name wins.

Where that tree lives depends on the sandbox backend. Under podman it sits
under the control plane's data dir and is bind-mounted into every sandbox.
Under kubernetes a pod mounts only the workspace claim, so fleet re-stages the
same tree at `<workspace root>/skills` inside the claim and every pod mounts
it read-only (fleet ADR-0055). Either way the agent's path is
`skills/<name>/…`, and this bundle inherits the pack.

## Validating a skill

```sh
fleet validate-config          # reports skill load errors alongside everything else
```

A skill with a `name` that does not match its folder, or with no frontmatter,
is reported there rather than silently ignored.
