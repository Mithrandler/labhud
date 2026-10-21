"""A stack's compose file from a git repository: `{repo, branch, path}` instead of the text.

The server fetches the one file over HTTP(S) from the forge's raw-file address (the agent needs no
git, and no new version): GitHub (raw.githubusercontent.com), GitLab (/-/raw/) and Gitea / Forgejo
(/raw/branch/), which is the guess for any other host. A token for a private repository is used
for that one request: it is never stored, logged, audited or sent back.
"""

import re
import urllib.error
import urllib.parse
import urllib.request

REPO_RE = re.compile(r"^https?://([A-Za-z0-9.-]+(:[0-9]{1,5})?)/([A-Za-z0-9._~-]+(/[A-Za-z0-9._~-]+){1,5}?)(\.git)?/?$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,100}$")
PATH_RE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+){0,8}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{8,300}$")
TIMEOUT = 15


def raw_url(repo, branch, path):
    """The address of `path` on `branch` in `repo`. Raises ValueError for anything that is not plain."""
    m = REPO_RE.match(repo or "")
    if not m:
        raise ValueError("repo: an http(s) address like https://git.example/owner/name")
    if not BRANCH_RE.match(branch or "") or ".." in branch:
        raise ValueError("branch: a plain branch or tag name")
    if not PATH_RE.match(path or "") or any(p in ("", ".", "..") for p in path.split("/")):
        raise ValueError("path: a relative path such as stacks/web/compose.yaml")
    host, project = m.group(1), re.sub(r"\.git$", "", m.group(3))
    scheme = repo.split(":", 1)[0]
    q = urllib.parse.quote
    if host.lower() in ("github.com", "www.github.com"):
        return f"https://raw.githubusercontent.com/{project}/{q(branch)}/{path}"
    if "gitlab" in host.lower():
        return f"{scheme}://{host}/{project}/-/raw/{q(branch)}/{path}"
    return f"{scheme}://{host}/{project}/raw/branch/{q(branch)}/{path}"


def fetch(repo, branch, path, token=None, limit=48 << 10, opener=urllib.request.urlopen):
    """The text of the file. Raises ValueError (with a message that never holds the token)."""
    url = raw_url(repo, branch, path)
    headers = {"User-Agent": "labhud"}
    if token:
        if not isinstance(token, str) or not TOKEN_RE.match(token):
            raise ValueError("token: not a token")
        headers["Authorization"] = "Bearer " + token
    try:
        with opener(urllib.request.Request(url, headers=headers), timeout=TIMEOUT) as r:
            data = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        raise ValueError(f"the repository answered {e.code}" + (" (a private one needs a token)" if e.code in (401, 403, 404) else "")) from None
    except (urllib.error.URLError, OSError):
        raise ValueError("the repository could not be reached") from None
    if len(data) > limit:
        raise ValueError(f"the file is longer than {limit >> 10} KiB")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("the file is not text") from None
