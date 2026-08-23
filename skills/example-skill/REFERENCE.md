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
each `<name>/SKILL.md`. Which directory that *is* depends on `skills_builtin`:

- `skills_builtin: false` (this bundle) — the bundle's own `skills/`.
- `skills_builtin: true` (fleet's default) — a merged tree of the built-in pack
  plus the bundle's, materialized under the control plane's data dir. A bundle
  skill with the same name as a built-in one wins.

`skills_hidden: [name, …]` drops individual entries either way.

The merged tree is why this bundle turns the pack off: it lives on the control
plane's data PVC, and no sandbox image can carry a path derived from a runtime
hash. On the single-box podman install both work, because the sandbox
bind-mounts whatever directory fleet resolved.

## Validating a skill

```sh
fleet validate-config          # reports skill load errors alongside everything else
```

A skill with a `name` that does not match its folder, or with no frontmatter,
is reported there rather than silently ignored.
