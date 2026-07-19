# ghcr-token — ghcr.io pull credentials

Guided provisioner for the credential that lets Docker pull private and internal
images from `ghcr.io` across every organization you belong to.

```powershell
devtools ghcr-token                 # guided: create, validate, emit login commands
devtools ghcr-token --execute       # same, and log this host in as well
devtools ghcr-token doctor          # validate $GHCR_PAT, change nothing
devtools ghcr-token emit --target server
```

---

## Why a classic PAT, and only a classic PAT

This is the constraint everything else follows from.

> "GitHub Packages only supports authentication using a personal access token (classic)."
> — [Working with the Container registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)

| Token type | Spans all orgs? | Works with ghcr.io? |
|---|---|---|
| **Classic PAT** (`ghp_…`) | **yes** — account-wide scopes | **yes** |
| Fine-grained PAT (`github_pat_…`) | no — one owner per token | **no** |
| GitHub App installation token (`ghs_…`) | no — per installation | **no** |
| Actions `GITHUB_TOKEN` | workflow repo only | yes, inside Actions only |

Fine-grained PATs are not merely un-granted for packages — the fine-grained
permission set contains **no package permission at all**. There is nothing to
tick. This is structural, not a policy switch, so "use a fine-grained token with
`read:packages`" (seen in many third-party guides) is impossible: that scope does
not exist on fine-grained tokens.

Because a classic PAT's scopes are account-wide rather than owner-scoped, **one
token covers every organization you are a member of**. That is what makes a
single "enterprise-wide" credential achievable.

### The tool cannot create the token

There is no REST or GraphQL endpoint that mints a PAT. `POST /authorizations` was
removed on 2020-11-13 with no replacement. The browser step is permanently manual,
and so is rotation. Anything promising full automation of this is not buildable.

---

## The failure this tool exists to catch

`docker login` prints **`Login Succeeded`** for tokens `ghcr.io` will never
accept. The failure only appears later, as `docker pull … denied`, usually on a
different machine and often weeks later.

So login success is not evidence, and the tool refuses to treat it as such: it
proves the credential against the registry, per organization, **before** any
`docker login` runs.

Two things make the proof trustworthy:

- **It probes private/internal packages only.** A public package answers `200` to
  anonymous callers, so verifying against one would be a green light that proves
  nothing.
- **It checks the token's `access` claim, not just the status code.** `ghcr.io`
  hands out anonymous-scope tokens with an empty `access` array far more often
  than it returns `401`.

---

## Creating the token

The tool opens a prefilled page. Those query parameters are undocumented, so the
printed checklist is authoritative and works equally well on the bare form:

<https://github.com/settings/tokens/new?description=ghcr-machine-token&scopes=read:packages>

1. **Tokens (classic)** — *not* "Fine-grained tokens". Adjacent menu entries.
2. Scope **`read:packages`** and nothing else.
3. **If `repo` is checked, untick it.** GitHub auto-selects `repo` alongside
   `write:packages`; it grants full read access to all your source in every org,
   and pulling images does not need it.
4. After *Generate token*: **Configure SSO → Authorize** for each organization.
   The only genuinely per-org step, and the most common reason a correct
   `read:packages` token still fails.

`read:packages` alone is sufficient to pull. Only Apache Maven and Gradle
packages are repository-scoped; the container registry uses granular
per-package permissions.

---

## Two credentials, deliberately

| Purpose | Owner | Where it lands |
|---|---|---|
| Server deployments | a dedicated **machine user**, member of every org | `/root/.docker/config.json` |
| Local development | your own account | Windows Credential Manager |

A machine user is [explicitly permitted](https://docs.github.com/en/get-started/learning-about-github/types-of-github-accounts):
one free account per human plus one machine account. Keeping them separate means
a server credential does not carry a person's full org rights and does not die
when that person leaves.

```powershell
devtools ghcr-token --execute                       # your account, logs in here
devtools ghcr-token --target server -u <machine-user>   # machine user, server block only
```

---

## Where the credential actually lives

**Docker Desktop** → Windows Credential Manager via `credsStore: "desktop"`
(DPAPI, per-user). Reasonable at-rest protection.

**Linux server** → `/root/.docker/config.json` as **base64 — encoding, not
encryption**. Anyone who can read that file has the token. Membership of the
`docker` group is equivalent to root on that host:

```bash
getent group docker
```

**`docker logout` does not revoke anything.** It deletes the local copy. The only
real revocation is deleting the token at <https://github.com/settings/tokens>.

---

## Deployment targets beyond a plain `docker login`

### Docker Swarm

Worker nodes have no credential of their own. Without this flag, tasks sit in
`Pending`/`Rejected`:

```bash
docker stack deploy -c stack.yml --with-registry-auth mystack
docker service update --with-registry-auth --image ghcr.io/<org>/<image>:<tag> mystack_app
```

### Kubernetes

Do **not** hand `/root/.docker/config.json` to `--from-file`. With a credential
helper that file has no `auth` value at all (silent in-cluster 401), and without
one it exports *every* registry the host has logged into into a namespaced
Secret. Build a minimal single-registry file instead:

```bash
umask 077
AUTH=$(printf '%s' "<user>:<token>" | base64 -w0)
cat > /tmp/ghcr.json <<JSON
{"auths":{"ghcr.io":{"auth":"$AUTH"}}}
JSON

kubectl create secret generic ghcr-creds \
  --from-file=.dockerconfigjson=/tmp/ghcr.json \
  --type=kubernetes.io/dockerconfigjson \
  --namespace=production

shred -u /tmp/ghcr.json
```

Then reference it — and remember the ServiceAccount, the step most often missed:

```yaml
spec:
  imagePullSecrets:
    - name: ghcr-creds
```

```bash
kubectl patch serviceaccount default -n production \
  -p '{"imagePullSecrets":[{"name":"ghcr-creds"}]}'
```

Avoid `--docker-password="$(cat …)"`: it puts the token in process argv, readable
via `/proc/<pid>/cmdline` by any local user for the life of the command.

### Podman

Rootless Podman defaults to `$XDG_RUNTIME_DIR/containers/auth.json`, which lives
on tmpfs — the login evaporates on reboot. Pin the path:

```bash
# rootless
podman login --authfile "$HOME/.config/containers/auth.json" ghcr.io -u <user>
podman pull  --authfile "$HOME/.config/containers/auth.json" ghcr.io/<org>/<image>:<tag>
```

A rootless user can neither write `/etc/ghcr/auth.json` nor read a `0400 root`
token file, so the server recipe does not transfer.

### GitHub Actions

Never use a PAT here. `GITHUB_TOKEN` works with `ghcr.io` directly:

```yaml
- run: echo "${{ secrets.GITHUB_TOKEN }}" | docker login ghcr.io -u ${{ github.actor }} --password-stdin
```

Grant repository access under the package's **Manage Actions access**.

---

## Rotation

Rotation cannot be automated — same dead end as creation. Budget a recurring
manual task.

1. `devtools ghcr-token --target server -u <machine-user>` and create the new token.
2. Roll it out with the emitted block.
3. Confirm green, then **delete the old token** at
   <https://github.com/settings/tokens>. This is the only step that revokes.

The tool has no `revoke` verb on purpose: a command that cannot actually revoke
would be a trap during the incident it exists for.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Login Succeeded`, then `pull` → `denied` | fine-grained PAT or App token | create a **classic** PAT. Diagnose instantly: `x-oauth-scopes` absent on `GET /user` means not classic |
| Denied in one org, fine in others | SSO not authorized for that org | token → **Configure SSO** → Authorize |
| Denied, SSO fine | no package-level read grant (membership ≠ package access) | package → Manage access → add the user or a team |
| Denied, package access list empty | pushed before repo linking, or inheritance off | enable "Inherit access from repository", or grant directly |
| `403` on everything in one org | org/enterprise restricts classic PATs | Org Settings → Personal access tokens → Allow |
| `unauthorized` after piping on Windows | `$OutputEncoding` emits a BOM | use `[System.Text.UTF8Encoding]::new($false)`, or just `docker login ghcr.io -u <user>` |
| `unauthorized` from a `.bat` file | trailing space before `\|` | remove it. Docker CLI ≥ 29.3.0 preserves it as part of the password; older CLIs trimmed it |
| `unauthorized`, token was base64'd first | Docker encodes `user:token` itself | pass the raw `ghp_…` |
| Swarm tasks `Pending`, manager pulls fine | missing `--with-registry-auth` | redeploy with the flag |
| Token expired unexpectedly | org/enterprise max-lifetime policy overrode your choice | check the policy; rotate on the enforced cadence |

Verify a credential without pulling an image:

```bash
devtools ghcr-token doctor
```

---

## Notes for maintainers

**This tool contains the only non-`gh` HTTP calls in the repository**, via stdlib
`urllib`. That is deliberate and should not be "fixed" back to `gh api` or
`curl`:

- `gh api` cannot reach `ghcr.io` — it targets api.github.com and no flag changes that.
- Passing the token under test to a `gh` child risks `gh` silently falling back to
  its own keyring, which would **pass** a token the user never pasted — the worst
  possible outcome for a validator.
- `curl -u user:PAT` would put the token in the process table.

Discovery (`/user/orgs`, package listing) *does* use the ambient `gh` credential,
and must: a pull-only PAT has no `read:org` and could never enumerate the
organizations it is being tested against.

**A slash inside a package name is treated two opposite ways**, which is easy to
get backwards:

- registry scope string and `/v2/…` path → **meaningful, do not encode**
- `gh api /orgs/{org}/packages/container/{name}/versions` → **must** be
  `quote(name, safe='')`

**The tool never prints `docker login`'s stdout or stderr.** Every other
`gh-*.py` here prints subprocess output on failure; this one must not, because
the token is on that stdin.
