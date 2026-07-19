# DevTools Automation — Operations & Security

How the organization-wide maintenance scripts run on a schedule, how they
authenticate, and the security model that keeps the access token safe in a
**public** repository.

Workflow: [`.github/workflows/devtools-automation.yml`](../../.github/workflows/devtools-automation.yml)

---

## What runs

| Trigger | What | When |
|---------|------|------|
| `schedule` | `gh-prefix-labels --execute` and `gh-dependabot-labels --execute` | Weekly, Sunday 06:00 UTC (`0 6 * * 0`) |
| `workflow_dispatch` | Any `gh-*` script from the dropdown, with optional `extra-args` and a `dry-run` toggle (default on) | On demand (write access required) |

- **`gh-prefix-labels`** — assigns GitHub *topics* to repos based on their name
  prefix, driven by [`config/prefix-labels.json`](config/prefix-labels.json).
- **`gh-dependabot-labels`** — reads each repo's `dependabot.yml` and creates the
  labels it references.

Both run against the `bauer-group` organization.

---

## Authentication

The scripts call the GitHub CLI / REST API with a Personal Access Token exposed
to the jobs as `GH_TOKEN`.

- **Secret name:** `DEVELOPERTOOLS_PAT`
- **Stored in:** the `automation` **GitHub Environment** (not as a plain
  repo/org secret) — both jobs declare `environment: automation`, so the token
  only resolves inside that environment's context.

### Minimum PAT permissions

For the two **scheduled** scripts, a fine-grained PAT needs only:

| Permission | Why |
|------------|-----|
| **Administration:** Read and write | `PUT /repos/{org}/{repo}/topics` (prefix topics) |
| **Issues:** Read and write | create labels (`gh-dependabot-labels`) |
| **Metadata:** Read | always granted; lists/reads org repos |

> `Metadata: write` does not exist for fine-grained PATs — setting topics runs
> under **Administration: write**, and labels live under **Issues**, not
> Pull requests.

Classic PAT equivalent: `repo` is sufficient for the scheduled scripts.

Running the broader **manual** scripts (`gh-branch-protection`,
`gh-codeowners-sync`, `gh-webhook-manager`, `gh-secrets-audit`, …) requires the
additional scopes documented in the workflow file header. Scope the PAT to the
minimum you actually use.

### Known limitation

`gh-dependabot-labels` fetches `dependabot.yml` unauthenticated from
`raw.githubusercontent.com`, so it only reads configs from **public** repos.
Private repos are silently skipped regardless of PAT scope. To cover private
repos the fetch would need to move to `gh api .../contents/...` (adds
**Contents: Read**).

---

## Security model

The token is org-wide and powerful, and the repo is public. Three layers keep
it safe.

### 1. Triggers are not reachable from the outside

The workflow runs **only** on `schedule` and `workflow_dispatch`. It does **not**
use `pull_request` or — critically — `pull_request_target`.

| What an outsider can do to a public repo | Effect here |
|------------------------------------------|-------------|
| Fork + open a PR | Does not trigger this workflow at all |
| PR that edits the workflow | Fork PRs never receive secrets; read-only `GITHUB_TOKEN`; first-time contributors need approval |
| Run a workflow manually | `workflow_dispatch` requires **write access** |

A pull request from the public therefore **cannot** read `DEVELOPERTOOLS_PAT`
or run the automation. The real trust boundary is *who has write access*, not
public vs. private.

### 2. Environment + deployment-branch restriction

Binding the secret to the `automation` environment is only half the control —
the environment **restricts deployments to the `main` branch**
(Settings → Environments → automation → *Deployment branches and tags* →
*Selected branches and tags* → `main`).

This closes the one remaining vector: a user with write access pushing a
modified workflow to a feature branch and dispatching it from there. Such a run
is rejected at the environment gate before any step executes:

```
Branch "<feature-branch>" is not allowed to deploy to automation
due to environment protection rules.
```

> **No required reviewers.** Reviewers would block the weekly scheduled run
> (it would hang waiting for an approval that never comes at 06:00 UTC). The
> branch restriction protects without that side effect. The scheduled run is
> unaffected because it executes on `main`.

### 3. No shell injection

Every `${{ ... }}` expression is passed through an `env:` block and referenced
as a shell variable (`$SCRIPT`, `$EXTRA_ARGS`, `$RUNNER`, `$SCRIPT_PATH`,
`$SCRIPT_ARGS`, …) — never interpolated directly into a `run:` body.

`${{ }}` is substituted as raw text *before* the shell parses the script, so a
crafted `extra-args` value could otherwise inject commands. A shell variable is
expanded *after* parsing, so its contents are never re-interpreted as syntax.

> `$SCRIPT_ARGS` is intentionally left unquoted in the run line to preserve
> word-splitting of multi-argument input (e.g. `--repo X --verbose`). This
> permits glob expansion but **not** command execution, and is only reachable
> by write-access users.

The default `GITHUB_TOKEN` is also pinned to `permissions: contents: read`.

---

## Runbook

### Rotate / (re)create the PAT

1. Create a fine-grained PAT (resource owner `bauer-group`, repository access
   *All repositories*) with the permissions above.
2. Settings → Environments → `automation` → **Environment secrets** → update
   `DEVELOPERTOOLS_PAT` with the new value.
3. Delete the old token. No workflow change is needed.

Prefer a short expiry (≤ 90 days) and set a rotation reminder.

### Verify it works (non-destructive)

```bash
# Dry-run: authenticates, lists repos, computes changes, writes nothing
gh workflow run devtools-automation.yml -f script=gh-prefix-labels -f dry-run=true

# Inspect the run; success criterion is real data, e.g. "Found N repositories"
gh run list --workflow devtools-automation.yml --limit 1
gh run view <run-id> --log
```

A green check alone is **not** proof: if the token is missing, the guard skips
execution and still reports success. Confirm by looking for actual script
output (`Found N repositories`).

### Verify the branch restriction (optional)

Dispatching from any branch other than `main` must fail at the environment gate
with *"… is not allowed to deploy to automation …"* and run **zero** steps.

---

## GHCR pull credential vs. `DEVELOPERTOOLS_PAT`

These are two different credentials and they are **not** interchangeable.

`DEVELOPERTOOLS_PAT` is a **fine-grained** PAT. `ghcr.io` rejects fine-grained
PATs outright — GitHub Packages only supports classic PATs — so it cannot be
reused for pulling container images, no matter which permissions it is granted.
The fine-grained permission set contains no package permission at all.

The GHCR pull credential is therefore a **separate classic PAT**, provisioned by
[`scripts/ghcr-token.py`](../../scripts/ghcr-token.py) (see
[`scripts/ghcr-token.md`](../../scripts/ghcr-token.md)). It must **not** be stored
as an Actions secret in this public repository: workflows that need to pull from
`ghcr.io` use the built-in `GITHUB_TOKEN`, which the registry does accept, with
repository access granted under the package's *Manage Actions access*.

> The failure mode is silent. `docker login` prints `Login Succeeded` for a
> fine-grained PAT and only `docker pull` later fails with `denied` — which is
> why `ghcr-token` validates against the registry before logging in.

### One deliberate convention deviation

`ghcr-token.py` contains the **only non-`gh` HTTP calls in this repository**
(stdlib `urllib`, against `api.github.com` and `ghcr.io`). This is intentional
and should not be "cleaned up" back to `gh api` or `curl`:

- `gh api` cannot reach `ghcr.io` — it targets api.github.com, and no flag changes that.
- Handing the token *under test* to a `gh` child process risks `gh` silently
  falling back to its own keyring, which would report **PASS** for a token the
  user never pasted — the worst possible outcome for a validator.
- `curl -u user:PAT` would place the token in the process table.

Discovery (listing orgs and packages) does still shell out to `gh`, and must: a
pull-only PAT has no `read:org` and could never enumerate the organizations it is
being tested against.

---

## Residual risks & roadmap

- **Write access is the trust boundary.** Anyone with write can reach the token
  via a `main`-based dispatch. Keep the write/admin member set minimal and
  enforce 2FA.
- **PAT is user-tied.** Long-term, replace it with a **GitHub App** (installation
  tokens are auto-expiring, per-repo scoped, and not bound to a person).
- **Org-wide blast radius.** A leaked token reaches every repo within its scope —
  another reason to keep the PAT minimal and rotate it.
