#!/usr/bin/env python3
# @name: ghcr-token
# @description: Validate a classic ghcr.io pull PAT across all orgs and emit Docker login commands
# @category: local
# @usage: ghcr-token [setup|doctor|emit] [--orgs <a,b>] [-u <user>] [--target <t>] [--execute] [--yes]
"""
ghcr-token.py — guided ghcr.io pull-credential provisioner.

This tool does NOT create the token. GitHub has no API for that: the only
endpoint that ever minted one (POST /authorizations) was removed 2020-11-13
with no replacement. The browser step is permanently manual.

What it does instead is everything around that step, and one thing in
particular that nothing else does:

  ghcr.io accepts ONLY classic PATs. Fine-grained PATs and GitHub App
  installation tokens are rejected -- but `docker login` still prints
  "Login Succeeded" for them, and the failure only surfaces later as a
  `docker pull ... denied`. Login success is worthless as evidence.

So the credential is proven against ghcr.io, per organization, BEFORE any
`docker login` runs. Discovery uses the ambient `gh` credential (a pull-only
PAT has no read:org and could never enumerate the orgs it is being tested
against); validation uses the pasted token and nothing else.

Host-native by necessity: Docker Desktop stores credentials in the Windows
Credential Manager via credsStore "desktop", which does not exist inside the
bauer-devtools container.
"""

import argparse
import base64
import getpass
import json
import os
import re
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
NC = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

TOKEN_URL = "https://github.com/settings/tokens/new?description=ghcr-machine-token&scopes=read:packages"
TOKEN_LIST_URL = "https://github.com/settings/tokens"
REGISTRY = "ghcr.io"
HTTP_TIMEOUT = 15

# Probe verdicts.
VERIFIED = "VERIFIED"
DENIED = "DENIED"
UNVERIFIED = "UNVERIFIED"


# --------------------------------------------------------------------------
# gh / docker subprocess helpers
# --------------------------------------------------------------------------

def run_gh(args: List[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["gh"] + args, capture_output=True, text=True, check=check)


def gh_lines(path: str, jq: str) -> Tuple[List[str], Optional[str]]:
    """Run `gh api --paginate <path> --jq <jq>`. Returns (lines, error)."""
    try:
        result = run_gh(["api", path, "--paginate", "--jq", jq])
    except subprocess.CalledProcessError as exc:
        return [], (exc.stderr or "").strip()
    except FileNotFoundError:
        return [], "gh CLI not found"
    return [ln.strip() for ln in result.stdout.splitlines() if ln.strip()], None


def check_gh_auth() -> bool:
    try:
        subprocess.run(["gh", "auth", "status"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True, timeout=30)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def http_get(url: str, headers: Dict[str, str]) -> Tuple[int, Dict[str, str], bytes]:
    """GET returning (status, lowercased-headers, body). Never raises on 4xx/5xx.

    Deliberate deviation from this repo's shell-out-to-gh convention, documented
    in services/devtools/AUTOMATION.md. Two reasons it cannot be `gh api`:
    ghcr.io is not api.github.com and gh cannot reach it, and passing the token
    under test to a gh child risks gh silently falling back to its own keyring
    and PASSING a token the user never pasted. `curl -u user:PAT` is also out --
    it would put the token in the process table.
    """
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()
    except HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()


def basic_auth(user: str, token: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{token}".encode("utf-8")).decode("ascii")


# --------------------------------------------------------------------------
# Token handling
# --------------------------------------------------------------------------

def mask(token: str) -> str:
    import hashlib
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
    kind = "classic" if token.startswith("ghp_") or re.fullmatch(r"[0-9a-f]{40}", token) else "unknown"
    prefix = token[:4] if token.startswith(("ghp_", "gho_", "ghs_", "ghu_")) else token[:3]
    return f"{prefix}... ({kind}, {len(token)} ch, sha256:{digest})"


def normalize_token(raw: str) -> Tuple[Optional[str], List[str], Optional[str]]:
    """Return (token, notes, fatal_error). Reports what it changed rather than
    silently fixing it -- a silently-repaired paste hides the real problem."""
    notes: List[str] = []
    token = raw

    # \ufeff written as an escape on purpose: a literal BOM here is zero-width and
    # invisible in every editor, so it silently rots on the next copy-paste edit.
    if token.startswith("\ufeff"):
        token = token.lstrip("\ufeff")
        notes.append("stripped a leading UTF-8 BOM")

    stripped = token.strip()
    if stripped != token:
        notes.append(f"stripped {len(token) - len(stripped)} surrounding whitespace character(s)")
        token = stripped

    for q in ('"', "'"):
        if len(token) >= 2 and token.startswith(q) and token.endswith(q):
            token = token[1:-1]
            notes.append(f"stripped wrapping {q} quotes")
            break

    if not token:
        return None, notes, "no token entered"

    # Whitespace INSIDE the value means a soft-wrapped paste, not a token.
    match = re.search(r"\s", token)
    if match:
        return None, notes, (
            f"whitespace inside the token at position {match.start() + 1} -- "
            "this looks like a line-wrapped paste, not a token. Re-copy it as one line."
        )

    return token, notes, None


def triage_prefix(token: str) -> Tuple[str, str]:
    """Positive assertion, not a denylist. Returns (level, message)."""
    if token.startswith("ghp_"):
        return "ok", "classic personal access token"
    if re.fullmatch(r"[0-9a-f]{40}", token):
        return "ok", "pre-2021 classic personal access token"
    if token.startswith("github_pat_"):
        return "fatal", (
            "this is a FINE-GRAINED PAT. ghcr.io rejects these outright -- GitHub's docs:\n"
            '      "GitHub Packages only supports authentication using a personal access\n'
            '       token (classic)."\n'
            "      The two menu entries sit next to each other. You need \"Tokens (classic)\"."
        )
    if token.startswith("ghs_"):
        return "fatal", "this is a GitHub App installation token. ghcr.io rejects these."
    if token.startswith(("gho_", "ghu_")):
        return "fatal", (
            "this is a gh CLI OAuth token (e.g. from `gh auth token`). It is tied to an\n"
            "      interactive user session -- never deploy it to a server."
        )
    return "warn", "unrecognized token prefix -- continuing, verification is the authority"


# --------------------------------------------------------------------------
# Discovery (ambient gh credential)
# --------------------------------------------------------------------------

def discover_orgs() -> List[str]:
    orgs, err = gh_lines("/user/orgs", ".[].login")
    if err:
        print(f"{YELLOW}warn: could not list organizations ({err}){NC}", file=sys.stderr)
    return sorted(orgs)


def gh_login() -> Optional[str]:
    try:
        return run_gh(["api", "/user", "--jq", ".login"]).stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def list_packages(org: str, visibility: str) -> Tuple[List[str], Optional[str]]:
    return gh_lines(
        f"/orgs/{org}/packages?package_type=container&visibility={visibility}",
        ".[].name",
    )


def select_probes(orgs: List[str]) -> List[Dict[str, str]]:
    """One probe per (org, visibility). Never public -- a public package answers
    200 unauthenticated and would turn the matrix into a meaningless green."""
    probes: List[Dict[str, str]] = []
    for org in orgs:
        found = False
        for visibility in ("private", "internal"):
            names, err = list_packages(org, visibility)
            if err:
                probes.append({
                    "org": org, "visibility": "-", "package": "-",
                    "verdict": UNVERIFIED,
                    "detail": "403 listing packages -- run `devtools gh-auth refresh`",
                })
                found = True
                break
            if names:
                probes.append({
                    "org": org, "visibility": visibility,
                    "package": sorted(names)[0], "verdict": "", "detail": "",
                })
                found = True
        if not found:
            probes.append({
                "org": org, "visibility": "-", "package": "-",
                "verdict": UNVERIFIED, "detail": "no container packages",
            })
    return probes


# --------------------------------------------------------------------------
# Tier A -- identity and token type
# --------------------------------------------------------------------------

def tier_a(token: str) -> Tuple[bool, Dict[str, str]]:
    status, headers, body = http_get("https://api.github.com/user", {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ghcr-token",
    })

    if status == 401:
        print(f"{RED}FATAL{NC}  401 from api.github.com -- the token is revoked, expired or mistyped.")
        return False, {}
    if status != 200:
        print(f"{RED}FATAL{NC}  unexpected {status} from api.github.com.")
        return False, {}

    info: Dict[str, str] = {}
    try:
        info["login"] = json.loads(body).get("login", "")
    except (json.JSONDecodeError, UnicodeDecodeError):
        info["login"] = ""

    # The header's ABSENCE is the fastest positive diagnosis of the most common
    # failure: a fine-grained PAT authenticates fine here and dies at ghcr.io.
    if "x-oauth-scopes" not in headers:
        print(f"{RED}FATAL{NC}  api.github.com returned 200 but no x-oauth-scopes header.")
        print("       That means this is NOT a classic PAT, and ghcr.io will reject it.")
        print("       This is exactly why such tokens work with `gh` but not with `docker`.")
        print(f"       Create a classic token: {CYAN}{TOKEN_URL}{NC}")
        return False, info

    scopes = [s.strip() for s in headers["x-oauth-scopes"].split(",") if s.strip()]
    info["scopes"] = ", ".join(scopes) or "(none)"

    if "read:packages" not in scopes:
        print(f"{RED}FATAL{NC}  token lacks the read:packages scope (has: {info['scopes']}).")
        print(f"       Edit its scopes -- no need to recreate it: {CYAN}{TOKEN_LIST_URL}{NC}")
        return False, info

    info["repo_warn"] = "repo" in scopes
    expiry = headers.get("github-authentication-token-expiration", "").strip()
    info["expires"] = expiry or "never"
    info["sso"] = "signalled" if headers.get("x-github-sso") else "not signalled"
    return True, info


# --------------------------------------------------------------------------
# Tier B -- real ghcr.io pull authorization
# --------------------------------------------------------------------------

def decode_jwt_access(jwt: str) -> Optional[List[dict]]:
    """Decode the payload of our own request/response pair. No signature check."""
    parts = jwt.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload)).get("access", [])
    except Exception:
        return None


def probe_registry(user: str, token: str, org: str, package: str) -> Tuple[str, str]:
    """Two calls against ghcr.io. Returns (verdict, detail)."""
    # In the registry scope and /v2/ path a slash inside the package name is
    # MEANINGFUL and must not be encoded -- the opposite of the gh api route,
    # where it must be quote(name, safe='').
    repo = f"{org.lower()}/{package.lower()}"

    scope_url = f"https://{REGISTRY}/token?service={REGISTRY}&scope=repository:{repo}:pull"
    try:
        status, _, body = http_get(scope_url, {
            "Authorization": basic_auth(user, token),
            "User-Agent": "ghcr-token",
        })
    except URLError as exc:
        return UNVERIFIED, f"network error reaching {REGISTRY} ({exc.reason})"

    # ghcr.io answers a bad credential with 403 here, not 401 -- verified against
    # the live registry. Both are a definitive rejection: mapping 403 to
    # UNVERIFIED would render a revoked token as yellow "could not check" and
    # let it slip past the DENIED gate.
    if status in (401, 403):
        return DENIED, f"ghcr.io rejected the credential ({status} at token exchange)"
    if status == 429:
        return UNVERIFIED, "rate-limited by ghcr.io (429) -- retry shortly"
    if status != 200:
        return UNVERIFIED, f"unexpected {status} from the ghcr.io token endpoint"

    try:
        registry_token = json.loads(body).get("token", "")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return UNVERIFIED, "malformed response from the ghcr.io token endpoint"
    if not registry_token:
        return UNVERIFIED, "ghcr.io returned no token"

    # A 200 here proves nothing on its own: ghcr.io hands out anonymous-scope
    # tokens with an empty access claim far more often than it returns 401.
    access = decode_jwt_access(registry_token)
    if access is not None and not any(
        entry.get("name") == repo and "pull" in entry.get("actions", []) for entry in access
    ):
        return DENIED, "ghcr.io issued an anonymous token (empty access claim)"

    try:
        status, _, _ = http_get(f"https://{REGISTRY}/v2/{repo}/tags/list", {
            "Authorization": f"Bearer {registry_token}",
            "User-Agent": "ghcr-token",
        })
    except URLError as exc:
        return UNVERIFIED, f"network error reaching {REGISTRY} ({exc.reason})"

    if status == 200:
        return VERIFIED, "200"
    if status in (401, 403):
        return DENIED, f"authenticated but not authorized ({status})"
    if status == 404:
        # OCI registries return 404, not 403, for a private repo the bearer may
        # not see -- so existence is not disclosed. Resolve it with the healthy
        # ambient gh credential rather than guessing.
        names, err = list_packages(org, "private")
        internal, _ = list_packages(org, "internal")
        if not err and package in (names + internal):
            return DENIED, "404 -- gh still lists this package, so the registry is hiding it"
        return UNVERIFIED, "404 -- package no longer listed, probe is stale"
    return UNVERIFIED, f"unexpected {status} from the registry"


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def render_matrix(user: str, token: str, info: Dict[str, str], probes: List[Dict[str, str]]) -> None:
    print()
    print(f"{BOLD}GHCR credential matrix{NC}")
    print(f"  user     {user}")
    print(f"  token    {mask(token)}")
    repo_note = (f"{YELLOW}repo GRANTED (over-scoped for pull-only){NC}"
                 if info.get("repo_warn") else f"{GREEN}repo not granted{NC}")
    print(f"  scopes   {info.get('scopes', '?')}   {repo_note}")
    print(f"  expires  {info.get('expires', '?')}   SSO {info.get('sso', '?')}")
    print()
    print(f"  {'ORG':<20}{'VIS':<10}{'PACKAGE':<30}RESULT")

    for probe in probes:
        verdict = probe["verdict"]
        colour = {VERIFIED: GREEN, DENIED: RED, UNVERIFIED: YELLOW}.get(verdict, NC)
        package = probe["package"]
        if len(package) > 28:
            package = package[:27] + "~"
        print(f"  {probe['org']:<20}{probe['visibility']:<10}{package:<30}"
              f"{colour}{verdict:<11}{NC}{DIM}{probe['detail']}{NC}")

    verified = sum(1 for p in probes if p["verdict"] == VERIFIED)
    denied = sum(1 for p in probes if p["verdict"] == DENIED)
    unverified = sum(1 for p in probes if p["verdict"] == UNVERIFIED)
    print()
    print(f"  {verified} verified | {denied} denied | {unverified} unverified")

    orgs_with_packages = {p["org"] for p in probes if p["package"] != "-"}
    orgs_ok = {p["org"] for p in probes if p["verdict"] == VERIFIED}
    if denied == 0 and unverified == 0:
        print(f"  {GREEN}This token is a valid enterprise-wide ghcr.io pull credential.{NC}")
    elif denied == 0 and orgs_with_packages:
        print(f"  Verified for {len(orgs_ok)} of {len(orgs_with_packages)} organizations "
              "that publish container packages.")
        print("  One classic PAT covers all of them -- classic PATs are user-scoped, not org-scoped.")

    for probe in probes:
        if probe["verdict"] == UNVERIFIED and probe["detail"] == "no container packages":
            print()
            print(f"{DIM}  NOTE  {probe['org']} has no container packages, so nothing could be")
            print("        verified there. No second token is needed -- a classic PAT is")
            print("        user-scoped. But each new private package still needs this user")
            print("        (or a team they are in) granted read access on it.")
            print(f"        Re-run `devtools ghcr-token doctor` after the first push.{NC}")
        elif probe["verdict"] == DENIED:
            print()
            print(f"{YELLOW}  DENIED on {probe['org']} -- in order of likelihood:{NC}")
            print(f"    1. SSO not authorized for this token: {TOKEN_LIST_URL}")
            print("       -> click the token -> Configure SSO -> Authorize for this org")
            print("    2. The org restricts classic PATs "
                  f"(Settings -> Personal access tokens)")
            print(f"    3. This user has no read access on the package itself:")
            print(f"       https://github.com/orgs/{probe['org']}/packages")


# --------------------------------------------------------------------------
# Emitters
# --------------------------------------------------------------------------

def probe_image(probes: List[Dict[str, str]]) -> Optional[str]:
    for probe in probes:
        if probe["verdict"] in (VERIFIED, "") and probe["package"] != "-":
            return f"{REGISTRY}/{probe['org'].lower()}/{probe['package'].lower()}"
    return None


def emit_desktop(user: str, image: Optional[str]) -> None:
    print()
    print(f"{BOLD}(a) Docker Desktop -- Windows / macOS / Linux workstation{NC}")
    print(f"{DIM}    Docker prompts for the password itself and reads the terminal with no echo,")
    print("    so the token never enters a shell variable, a pipe, or the command history.")
    print(f"    Identical in PowerShell and cmd.exe.{NC}")
    print()
    print(f"      docker login {REGISTRY} -u {user}")
    print()
    if image:
        print(f"{DIM}    Then prove it -- \"Login Succeeded\" alone means nothing:{NC}")
        print()
        print(f"      docker manifest inspect {image}:latest")
        print()
    print(f"{DIM}    If you must pipe it from a script instead:{NC}")
    print()
    print("      & {")
    print("        $OutputEncoding = [System.Text.UTF8Encoding]::new($false)")
    print("        $sec = Read-Host 'ghcr.io PAT (ghp_...)' -AsSecureString")
    print("        [System.Net.NetworkCredential]::new('', $sec).Password |")
    print(f"          docker login {REGISTRY} -u {user} --password-stdin")
    print("      }")
    print()
    print(f"{DIM}    Both lines are load-bearing. UTF8Encoding($false) because a BOM-emitting")
    print("    encoder (which [System.Text.Encoding]::UTF8 is) silently prepends EF BB BF")
    print("    and docker fails with a bare 'unauthorized'. The opening & { because without")
    print("    it a line-by-line paste leaves the rest in the input queue and Read-Host")
    print(f"    swallows the next line as the password.{NC}")


def emit_server(user: str, image: Optional[str], persist: bool) -> None:
    print()
    label = "with persisted /etc/ghcr/token" if persist else "default"
    print(f"{BOLD}(b) Docker server -- root login ({label}){NC}")
    print(f"{DIM}    Heredoc, not a one-liner: a truncated paste of a chained one-liner runs a")
    print("    prefix -- the directory gets created and the chmod never does. A truncated")
    print(f"    heredoc simply never fires, because bash waits for the terminator.{NC}")
    print()
    terminator = "GHCR_LOGIN_FILE" if persist else "GHCR_LOGIN"
    print(f"bash <<'{terminator}'")
    print("set -euo pipefail")
    print('[ "$(id -u)" -eq 0 ] || { echo "FATAL: run as root (sudo -i)" >&2; exit 1; }')
    print('command -v docker >/dev/null 2>&1 || { echo "FATAL: docker not found" >&2; exit 1; }')
    print()
    print(f"GHCR_USER='{user}'")
    if image:
        print(f"GHCR_PROBE='{image}:latest'")
    print()
    if persist:
        print("umask 077")
        print("install -d -m 0700 /etc/ghcr")
        print()
    print("printf 'ghcr.io PAT (ghp_...): ' > /dev/tty")
    print("IFS= read -rs GHCR_PAT < /dev/tty")
    print("printf '\\n' > /dev/tty")
    print()
    print('case "$GHCR_PAT" in')
    print('  ghp_*)             echo "OK: classic personal access token" ;;')
    print('  github_pat_*)      echo "FATAL: fine-grained PAT - ghcr.io rejects these." >&2; exit 1 ;;')
    print('  ghs_*)             echo "FATAL: App installation token - ghcr.io rejects these." >&2; exit 1 ;;')
    print('  gho_*|ghu_*)       echo "FATAL: gh CLI OAuth token. It may well pull, but it is tied" >&2')
    print('                     echo "       to a human session and dies with that account." >&2; exit 1 ;;')
    print('  *)                 echo "WARN: unrecognized token prefix - continuing." >&2 ;;')
    print("esac")
    print()
    if persist:
        print("printf '%s' \"$GHCR_PAT\" > /etc/ghcr/token")
        print("unset GHCR_PAT")
        print("chmod 0400 /etc/ghcr/token")
        print(f"docker login {REGISTRY} -u \"$GHCR_USER\" --password-stdin < /etc/ghcr/token")
    else:
        print(f"printf '%s' \"$GHCR_PAT\" | docker login {REGISTRY} -u \"$GHCR_USER\" --password-stdin")
        print("unset GHCR_PAT")
    print()
    if image:
        print("# \"Login Succeeded\" proves nothing -- verify against a private package.")
        print('if docker manifest inspect "$GHCR_PROBE" >/dev/null 2>&1; then')
        print('  echo "OK: $GHCR_PROBE reachable - credential is live"')
        print("else")
        print('  echo "FAIL: login accepted but the private manifest is NOT authorized" >&2')
        print("  exit 1")
        print("fi")
        print()
    print("cat <<'NOTE'")
    print()
    print("The credential is now in /root/.docker/config.json as base64 - NOT encrypted.")
    print("Membership of the 'docker' group is equivalent to root here: getent group docker")
    print("Swarm: deploy with  docker stack deploy --with-registry-auth")
    print("docker logout does NOT revoke. Only deleting the token at GitHub revokes it.")
    if persist:
        print()
        print("/etc/ghcr/token is a PERMANENT PLAINTEXT copy (0400 root). Exclude it from")
        print("backups and snapshots, or remove it when done:  shred -u /etc/ghcr/token")
    print("NOTE")
    print(terminator)


def emit(targets: List[str], user: str, probes: List[Dict[str, str]]) -> None:
    image = probe_image(probes)
    if not image:
        print(f"{YELLOW}note: no private or internal package found to verify against, so the")
        print(f"      emitted blocks omit the post-login proof step.{NC}")
    if "desktop" in targets:
        emit_desktop(user, image)
    if "server" in targets:
        emit_server(user, image, persist=False)
    if "server-file" in targets:
        emit_server(user, image, persist=True)


# --------------------------------------------------------------------------
# Acquisition
# --------------------------------------------------------------------------

def acquire_token(open_browser: bool) -> Optional[str]:
    env_token = os.environ.get("GHCR_PAT", "").strip()
    if env_token:
        print(f"{DIM}using the token from $GHCR_PAT (browser step skipped){NC}")
        raw = env_token
    else:
        print()
        print(f"{BOLD}Create the token{NC}")
        print(f"  {CYAN}{TOKEN_URL}{NC}")
        if open_browser:
            try:
                if webbrowser.open(TOKEN_URL):
                    print(f"{DIM}  (opened in your browser){NC}")
            except Exception:
                pass
        print()
        print("  The prefilled parameters are undocumented, so follow this checklist -- it")
        print("  is authoritative, and it works just as well on the bare form:")
        print()
        print(f"   1. Token type must be {BOLD}Tokens (classic){NC}, NOT \"Fine-grained tokens\".")
        print("      They are adjacent menu entries and ghcr.io rejects the fine-grained one.")
        print(f"   2. Scope: {BOLD}read:packages{NC} and nothing else.")
        print(f"      {YELLOW}If `repo` is checked, untick it{NC} -- it grants full read access to all")
        print("      your source code in every org, and pulling images does not need it.")
        print("   3. Expiration: as decided for this credential.")
        print(f"   4. After \"Generate token\": click {BOLD}Configure SSO{NC} next to it and authorize")
        print("      it for every organization. This is the only genuinely per-org step, and")
        print("      the most common reason a correct read:packages token still fails.")
        print()
        try:
            raw = getpass.getpass("Paste the token (input hidden): ")
        except (EOFError, KeyboardInterrupt):
            print()
            return None

    token, notes, fatal = normalize_token(raw)
    for note in notes:
        print(f"{DIM}  note: {note}{NC}")
    if fatal:
        print(f"{RED}error: {fatal}{NC}", file=sys.stderr)
        return None

    level, message = triage_prefix(token)
    if level == "fatal":
        print(f"{RED}FATAL{NC}  {message}")
        return None
    if level == "warn":
        print(f"{YELLOW}warn:{NC}  {message}")
    else:
        print(f"{GREEN}  ok:{NC}   {message}")
    return token


def do_login(user: str, token: str) -> bool:
    if not docker_available():
        print(f"{RED}error: docker is not responding. Is Docker Desktop running?{NC}", file=sys.stderr)
        return False
    try:
        # The only subprocess call here without text=True: the token must reach
        # docker's stdin as exact bytes -- no shell, no encoding layer, no BOM.
        # Output is deliberately NOT captured or echoed; see the security note in
        # AUTOMATION.md. Every other gh-*.py in this repo prints subprocess output
        # on failure. This one must not.
        subprocess.run(
            ["docker", "login", REGISTRY, "-u", user, "--password-stdin"],
            input=token.encode("ascii"), timeout=60, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as exc:
        print(f"{RED}error: docker login failed (exit {exc.returncode}).{NC}", file=sys.stderr)
        return False
    except (subprocess.TimeoutExpired, UnicodeEncodeError) as exc:
        print(f"{RED}error: docker login failed ({type(exc).__name__}).{NC}", file=sys.stderr)
        return False
    print(f"{GREEN}  ok:{NC}   logged this host into {REGISTRY} as {user}")
    return True


def confirm(question: str, default_yes: bool = False) -> bool:
    suffix = "[Y/n]" if default_yes else "[y/N]"
    try:
        answer = input(f"{question} {suffix}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not answer:
        return default_yes
    return answer in ("y", "yes")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="ghcr-token",
        description="Validate a classic ghcr.io pull PAT across all orgs and emit Docker login commands.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  ghcr-token                            guided setup: create, validate, print login commands
  ghcr-token --execute                  same, and log THIS host into ghcr.io as well
  ghcr-token --target server -u bot     server block only, for a machine-user token
  ghcr-token doctor                     validate the token in $GHCR_PAT, change nothing
  ghcr-token emit --target server-file  print the persisted-token server block

note: this tool cannot create the token -- GitHub has no API for that. It opens the
      prefilled page, then proves the token works against every org before use.
      `docker logout` does NOT revoke. Only deleting the token at GitHub does.
""",
    )
    parser.add_argument("verb", nargs="?", default="setup", choices=["setup", "doctor", "emit"],
                        help="setup (default), doctor, or emit")
    parser.add_argument("--orgs", default="", help="Comma-separated orgs (default: all via gh)")
    parser.add_argument("-u", "--user", default="", help="GitHub username for the emitted commands")
    parser.add_argument("--target", default="desktop,server",
                        help="Comma-separated: desktop, server, server-file")
    parser.add_argument("--no-browser", action="store_true", help="Print the URL instead of opening it")
    parser.add_argument("--execute", action="store_true", help="Also run docker login on this host")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompts")
    args = parser.parse_args()

    if Path("/.dockerenv").exists():
        print(f"{RED}This tool must run on the HOST -- Docker Desktop credentials live in the "
              f"Windows Credential Manager, not in this container.{NC}", file=sys.stderr)
        print("Use: devtools ghcr-token   or: python scripts/ghcr-token.py", file=sys.stderr)
        return 1

    targets = [t.strip() for t in args.target.split(",") if t.strip()]
    unknown = [t for t in targets if t not in ("desktop", "server", "server-file")]
    if unknown:
        print(f"{RED}error: unknown --target value(s): {', '.join(unknown)}{NC}", file=sys.stderr)
        return 1

    if not check_gh_auth():
        print(f"{RED}error: gh CLI is not authenticated. Run `gh auth login` first.{NC}", file=sys.stderr)
        return 1

    orgs = [o.strip() for o in args.orgs.split(",") if o.strip()] or discover_orgs()
    if not orgs:
        print(f"{RED}error: no organizations found.{NC}", file=sys.stderr)
        return 1

    print(f"{DIM}discovering container packages in: {', '.join(orgs)}{NC}")
    probes = select_probes(orgs)

    if args.verb == "emit":
        user = args.user or gh_login() or "YOUR_GH_USERNAME"
        emit(targets, user, probes)
        return 0

    token = acquire_token(open_browser=not args.no_browser and args.verb == "setup")
    if not token:
        return 1

    ok, info = tier_a(token)
    if not ok:
        return 1

    user = args.user or info.get("login") or gh_login() or "YOUR_GH_USERNAME"
    if info.get("login") and args.user and args.user != info["login"]:
        print(f"{YELLOW}warn:{NC}  --user {args.user} differs from the token owner "
              f"({info['login']}); using --user as instructed.")

    for probe in probes:
        if probe["verdict"]:
            continue
        probe["verdict"], probe["detail"] = probe_registry(user, token, probe["org"], probe["package"])

    render_matrix(user, token, info, probes)

    denied = sum(1 for p in probes if p["verdict"] == DENIED)
    if denied and not any(p["verdict"] == VERIFIED for p in probes):
        print()
        print(f"{RED}error: the credential was denied everywhere. Not emitting anything.{NC}",
              file=sys.stderr)
        return 1
    if denied:
        print()
        # --yes deliberately does NOT bypass this one: rolling a partially working
        # credential onto a server fleet is worse than a five-minute delay.
        if not confirm("Emit the install commands anyway?"):
            return 1

    if args.verb == "doctor":
        return 0

    emit(targets, user, probes)

    should_login = args.execute
    if not should_login and sys.stdin.isatty() and "desktop" in targets:
        print()
        should_login = confirm(f"Log this host into {REGISTRY} as {user} now?")
    if should_login:
        if not do_login(user, token):
            return 1

    print()
    print(f"{BOLD}Before you close this window{NC}")
    print(f"  Store the token in your password manager now -- GitHub will not show it")
    print(f"  again, and this tool saves it nowhere.")
    print(f"  Revoke it at {CYAN}{TOKEN_LIST_URL}{NC} -- `docker logout` does NOT revoke.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
