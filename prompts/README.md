# Prompt library

Files in this directory appear as read-only, Git-backed entries in fleet's
Prompt Library in both Chat and the Operations Center. fleet accepts `.yaml`,
`.yml`, `.md`, and `.txt` and inserts the exact file body into the draft.

For YAML, use a top-level `name` plus optional `description` or `goal` so the
catalog is easy to browse. For Markdown, use a level-one heading followed by a
short introductory paragraph. Keep credentials and customer specifics out of
prompt files; this directory is committed and reviewed.

People who do not work in Git can create private or workspace-shared prompts in
the same picker and export a JSON backup.

**On the Kubernetes path this directory is baked into the control-plane image**
(`deploy/kubernetes/Containerfile.control-plane`), so adding a prompt means
rebuilding and rolling that image. It is deliberately NOT baked into the sandbox
image: prompts are read host-side and inserted into a draft, never read by the
agent from inside a sandbox.
