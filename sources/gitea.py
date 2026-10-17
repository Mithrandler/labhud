# Gitea Actions: the latest CI runs of your repositories, which failed, which run or wait for a runner.
#
#   LABHUD_GITEA_URL     https://git.example.com
#   LABHUD_GITEA_TOKEN   a token with read:repository (User settings > Applications)
#   LABHUD_GITEA_REPOS   optional: "owner/repo,owner/other"; else the 10 repositories updated last
#
# Data: gitea.{failed, running, queued, runs}; `runs` is a list for a card, failed and running
# first: list = "gitea.runs". `failed` counts the repositories whose latest run failed.

from ._common import ago, env, request, source

RUNS = 5
REPOS = 10


def _get(path):
    return request(env("GITEA_URL").rstrip("/") + "/api/v1" + path,
                   {"Authorization": "token " + env("GITEA_TOKEN"), "Accept": "application/json"},
                   timeout=15)


def _repos():
    if env("GITEA_REPOS").strip():
        return [r.strip() for r in env("GITEA_REPOS").split(",") if r.strip()]
    data = _get(f"/repos/search?sort=updated&order=desc&limit={REPOS}")
    return [r["full_name"] for r in (data.get("data") or []) if r.get("full_name")][:REPOS]


@source("gitea", every=120, env=("GITEA_URL", "GITEA_TOKEN"),
         title="Gitea Actions",
         about="The latest CI runs of your repositories: failed, running, waiting for a runner.",
         hints={"GITEA_URL": "https://git.example.com",
                "GITEA_TOKEN": "User settings > Applications: read:repository",
                "GITEA_REPOS": "optional: owner/repo,owner/other"})
def gitea():
    failed = running = queued = 0
    rows = []
    for full in _repos():
        data = _get(f"/repos/{full}/actions/runs?limit={RUNS}")
        runs = data.get("workflow_runs") or []
        if not runs:
            continue
        latest = runs[0]
        for r in runs:
            st = r.get("status") or ""
            if st in ("running", "in_progress"):
                running += 1
            if st in ("queued", "waiting", "blocked"):
                queued += 1
        concl = latest.get("conclusion") or ""
        status = latest.get("status") or ""
        if (status == "completed" or concl) and concl == "failure":
            failed += 1
        if status in ("running", "in_progress"):
            value = "running"
        elif status in ("queued", "waiting", "blocked"):
            value = "waiting for a runner"
        else:
            value = concl or status or "?"
        when = ago(latest.get("updated_at") or latest.get("started_at") or "")
        title = str(latest.get("display_title") or latest.get("head_branch") or "")[:40]
        name = full.split("/")[-1] + " · " + title
        rows.append({"name": name, "value": value + (" · " + when if when else ""),
                     "bad": concl == "failure" and value == "failure",
                     "_rank": 0 if concl == "failure" else 1 if value != concl else 2})
    rows.sort(key=lambda r: (r["_rank"], r["name"].lower()))
    for r in rows:
        del r["_rank"]
    return {"failed": failed, "running": running, "queued": queued,
            "runs": rows or [{"name": "no runs", "value": "—"}]}
