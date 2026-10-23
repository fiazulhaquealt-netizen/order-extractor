#!/usr/bin/env python3
"""Local CI/CD loop: fetch -> fast-forward main -> pytest -> Render deploy (API) -> healthz -> restart box worker.
Never forces, never prints secrets. Log: worker/cicd.log (via supervisor). State: worker/.cicd_state.json."""
import json, os, subprocess, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "worker/.cicd_state.json"
SERVICE = os.getenv("RENDER_SERVICE_ID", "srv-db75t1mk1f9s73cb19qg")
SITE = os.getenv("SITE_URL", "https://order-extractor-k55c.onrender.com")
KEY_FILE = Path(os.getenv("RENDER_KEY_FILE", "/workspace/.render_key"))
INTERVAL = int(os.getenv("CICD_INTERVAL_S", "60"))
SELF = {"worker/cicd.py", "worker/cicd_forever.sh"}

def log(m): print(time.strftime("%F %T"), m, flush=True)
def git(*a, check=True): return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, check=check).stdout.strip()
def state(): return json.loads(STATE.read_text()) if STATE.exists() else {}
def save(s): STATE.write_text(json.dumps(s, indent=1))

def render(path, data=None):
    req = urllib.request.Request("https://api.render.com/v1" + path, data=json.dumps(data).encode() if data is not None else None,
                                 method="POST" if data is not None else "GET",
                                 headers={"Authorization": "Bearer " + KEY_FILE.read_text().strip(), "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r: return json.load(r)

def live_commit():
    for d in render(f"/services/{SERVICE}/deploys?limit=20"):
        d = d.get("deploy", d)
        if d["status"] == "live": return d["commit"]["id"]
    return None

def healthz():
    try:
        with urllib.request.urlopen(SITE + "/healthz", timeout=30) as r: return json.load(r).get("ok") is True
    except Exception: return False

def deploy(sha):
    d = render(f"/services/{SERVICE}/deploys", {"commitId": sha}); did = d["id"]
    log(f"deploy {did} triggered for {sha[:7]}")
    t0 = time.time()
    while time.time() - t0 < 1200:
        time.sleep(15); st = render(f"/services/{SERVICE}/deploys/{did}")["status"]
        if st == "live":
            for _ in range(10):
                if healthz(): log(f"deploy {did} live; healthz ok"); return True
                time.sleep(6)
            log(f"deploy {did} live but healthz NOT ok"); return False
        if st in ("build_failed", "update_failed", "canceled", "deactivated", "pre_deploy_failed"):
            log(f"deploy {did} ended: {st}"); return False
    log(f"deploy {did} timed out"); return False

def restart_worker():
    pidf = ROOT / "worker/run_forever.pid"
    if pidf.exists():
        try:
            pid = int(pidf.read_text())
            if os.getpgid(pid) == pid: os.killpg(pid, 15)   # run_forever.sh leads its own group (worker + tick loop)
            else: os.kill(pid, 15)
        except Exception as e: log(f"worker stop: {e}")
        time.sleep(3)
    subprocess.Popen(["bash", "worker/run_forever.sh"], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=open(ROOT / "worker.log", "a"),
                     stderr=subprocess.STDOUT, start_new_session=True)
    time.sleep(2); log(f"box worker restarted (pid {pidf.read_text().strip() if pidf.exists() else '?'})")

def anc(a, b): return subprocess.run(["git", "merge-base", "--is-ancestor", a, b], cwd=ROOT).returncode == 0

def cycle():
    s = state()
    git("fetch", "-q", "origin")
    if git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        log("checkout is not on main; skipping"); return
    local, remote = git("rev-parse", "main"), git("rev-parse", "origin/main")
    if local != remote:
        if anc("main", "origin/main"):
            changed = set(git("diff", "--name-only", local, remote).split())
            r = subprocess.run(["git", "merge", "--ff-only", "-q", "origin/main"], cwd=ROOT, capture_output=True, text=True)
            if r.returncode: log(f"fast-forward failed (dirty tree?): {r.stderr.strip()[:300]}"); return
            log(f"fast-forwarded main {local[:7]} -> {remote[:7]} ({len(changed)} files)")
            if "requirements.txt" in changed:
                subprocess.run(["uv", "pip", "install", "-q", "--python", ".venv/bin/python", "-r", "requirements.txt"], cwd=ROOT)
            if any(f.startswith(("worker/", "app/")) and f not in SELF for f in changed):
                s["worker_restart_pending"] = True; save(s)
            if changed & SELF:
                log("CI/CD script changed; exiting so the supervisor restarts the new version"); save(s); sys.exit(0)
        elif anc("origin/main", "main"):
            if s.get("ahead_logged") != local: log(f"local main {local[:7]} has unpushed commits; nothing to deploy until pushed")
            s["ahead_logged"] = local; save(s); return
        else:
            if s.get("diverged_logged") != remote: log(f"local main diverged from origin/main {remote[:7]}; not fast-forwarding (never force)")
            s["diverged_logged"] = remote; save(s); return
    head = git("rev-parse", "main")
    if s.get("worker_restart_pending"):
        restart_worker(); s.pop("worker_restart_pending"); save(s)
    if head == s.get("bad_sha") or head == s.get("deployed_ok"):
        return
    if s.get("tested_ok") != head:
        log(f"running tests at {head[:7]}")
        r = subprocess.run([".venv/bin/python", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True, text=True, timeout=1800)
        tail = (r.stdout.strip().splitlines() or ["?"])[-1]
        if r.returncode:
            log(f"TESTS FAILED at {head[:7]}: {tail}; not deploying, will not retry this commit"); s["bad_sha"] = head; save(s); return
        log(f"tests passed at {head[:7]}: {tail}"); s["tested_ok"] = head; save(s)
    live = live_commit()
    if live == head:
        log(f"render already live at {head[:7]}"); s["deployed_ok"] = head; save(s); return
    log(f"render live {str(live)[:7]} != {head[:7]}; deploying")
    if deploy(head): s["deployed_ok"] = head
    else: s["bad_sha"] = head   # don't hammer a failing deploy every minute
    save(s)

if __name__ == "__main__":
    log(f"cicd loop start (interval {INTERVAL}s)")
    while True:
        try: cycle()
        except SystemExit: raise
        except Exception as e: log(f"cycle error: {type(e).__name__}: {str(e)[:300]}")
        time.sleep(INTERVAL)
