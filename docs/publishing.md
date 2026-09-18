# Publish your copy on GitHub

From the extracted project directory, run this as your normal desktop user:

```bash
bash tools/publish-github.sh
```

This creates **a public repository named `spatial-workbench` under your signed-in
GitHub account** and pushes the source. Running the command requests publication;
there is no separate confirmation after authentication. This document does not
mean a repository has already been created.

The script installs missing `git` and `github-cli` packages through normal
Arch/CachyOS `sudo pacman` prompts. It uses the official GitHub CLI browser login
when needed, including permission to publish the included GitHub Actions
workflows. Credentials are handled by `gh`; the script never requests, reads,
or prints an authentication token. It configures GitHub's Git credential helper
using `gh auth setup-git`.

To require a particular signed-in account or choose another repository name:

```bash
bash tools/publish-github.sh --owner Lich-King-Ethan --name spatial-workbench
```

`--owner` verifies the authenticated personal account. It cannot publish into
another person's account or an organization. Existing Git commit names and
emails are preserved. If either is missing, the script sets it for this checkout
using the authenticated login and GitHub's numeric-ID noreply address.

You can inspect the publication file list without signing in or changing files:

```bash
bash tools/publish-github.sh --dry-run
```

Only the exact source paths listed in the script are staged, including tests,
documentation, workflows, package recipes, license texts, and source patches.
Build outputs, diagnostic reports, test logs, credentials, and runtime state are
excluded. Every listed source file must exist and must be a regular file without
symlinked parents. Review any personal edits before publishing; a filename list
does not detect secrets pasted into a source file. If you add source files later,
review them and update the explicit list before using this first-publication tool.

The script refuses existing Git remotes, pre-staged changes, detached HEAD, and
history containing files outside that list. A fresh extracted source archive is
the easiest starting point. It stages only the approved files and creates a
commit only when their contents have changed. It does not force-push, replace an
existing repository, or rewrite history.

If creation succeeds but the push fails, the GitHub repository may already exist.
Review it and the local `origin`, resolve the reported authentication/network
error, then finish with a normal push:

```bash
git remote -v
git log -1 --oneline
git push -u origin HEAD
```

If an older GitHub CLI login lacks the `workflow` permission and GitHub rejects
the workflow files, run `gh auth refresh --hostname github.com --scopes workflow`
and retry the push. This uses GitHub's normal authorization flow.

If there is no `origin`, add the exact HTTPS URL of your newly created repository
with `git remote add origin URL` first. Do not rerun creation against an existing
repository. After successful publication, use normal Git commits and pushes for
later updates.

The command follows the official [repository creation](https://cli.github.com/manual/gh_repo_create),
[browser authentication](https://cli.github.com/manual/gh_auth_login), and
[Git credential helper](https://cli.github.com/manual/gh_auth_setup-git) interfaces.
