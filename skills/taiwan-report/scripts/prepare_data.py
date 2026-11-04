"""Resumable GitHub CLI transport for a single stock report; never browser-drive dispatch."""

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

REPOSITORY = "wei0223/taiwan-tradingagents"
WORKFLOW = "taiwan-prepare.yml"


class TransportError(RuntimeError):
    pass


class GhClient:
    def __init__(self, executable):
        self.executable = executable
        self.env = dict(os.environ, GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1")
        self.env.pop("FINMIND_TOKEN", None)

    def call(self, args, data=None):
        result = subprocess.run([self.executable, *args], input=data, capture_output=True,
            encoding="utf-8", env=self.env, timeout=60, check=False)
        if result.returncode:
            # Do not echo subprocess output or any credentials to the assistant/report.
            raise TransportError("GitHub CLI operation failed; exit " + str(result.returncode))
        return result.stdout

    def authenticated(self):
        try:
            self.call(["auth", "status", "--hostname", "github.com"])
            return True
        except TransportError:
            return False

    def api(self, endpoint):
        return json.loads(self.call(["api", endpoint]))

    def dispatch(self, inputs):
        values = {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in inputs.items()}
        self.call(["workflow", "run", WORKFLOW, "--repo", REPOSITORY, "--ref", "main", "--json"],
                  json.dumps(values))

    def download(self, artifact_id, output):
        with output.open("xb") as handle:
            result = subprocess.run([self.executable, "api", f"repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip"],
                stdout=handle, stderr=subprocess.PIPE, env=self.env, timeout=150, check=False)
        if result.returncode:
            raise TransportError("Artifact download failed; exit " + str(result.returncode))


def timestamp(value):
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None:
        raise ValueError("Timestamp requires timezone")
    return instant.astimezone(UTC)


def persist(path, state):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)
    history = path.parent / "preparation-history"
    history.mkdir(exist_ok=True)
    with (history / (uuid.uuid4().hex + ".json")).open("x", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)


def extract_archive(archive, target):
    """Validate every member before writing anything; retain the original ZIP."""
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        if len(members) > 10000 or sum(m.file_size for m in members) > 1024**3:
            raise ValueError("Artifact exceeds extraction limits")
        seen = set()
        for member in members:
            path = PurePosixPath(member.filename.replace("\\", "/"))
            identity = str(path).casefold()
            if (path.is_absolute() or ".." in path.parts or ":" in str(path)
                    or identity in seen or stat.S_ISLNK(member.external_attr >> 16)):
                raise ValueError("Unsafe or duplicate artifact member")
            seen.add(identity)
        target.mkdir(parents=True, exist_ok=False)
        for member in members:
            path = target.joinpath(*PurePosixPath(member.filename.replace("\\", "/")).parts)
            if member.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, path.open("xb") as handle:
                    shutil.copyfileobj(source, handle)


def verify_snapshot(folder, request, state, now):
    # The workflow also emits integrated/snapshot.json. It is not another raw input.
    path = folder / (request["stock_id"] + "-" + request["as_of"]) / "snapshot.json"
    if not path.is_file():
        raise ValueError("Artifact lacks the expected primary snapshot")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("instrument", {}).get("stock_id") != request["stock_id"] or data.get("as_of") != request["as_of"]:
        raise ValueError("Primary snapshot company/date mismatch")
    captures = [timestamp(d["retrieved_at"]) for d in data.get("datasets", {}).values() if d.get("retrieved_at")]
    if not captures or not any(timestamp(state["dispatch_started_at"]) - timedelta(seconds=5) <= t <= now + timedelta(minutes=2) for t in captures):
        raise ValueError("Snapshot freshness cannot be bound to this dispatch")
    if data.get("data_mode") == "synthetic":
        raise ValueError("Synthetic data cannot complete live preparation")
    return path


def prepare(request_path, client, clock=None):
    now = (clock or (lambda: datetime.now(UTC)))()
    if now.tzinfo is None:
        raise ValueError("Clock requires timezone")
    request_path = Path(request_path).resolve()
    if os.name == "nt" and not str(request_path).startswith("\\\\?\\"):
        request_path = Path("\\\\?\\" + str(request_path))
    raw = request_path.read_bytes()
    request = json.loads(raw)
    workflow = request["workflow"]
    if (workflow["repository"] != REPOSITORY or workflow["file"] != WORKFLOW or workflow["ref"] != "main"
            or workflow["inputs"]["symbols"] != request["symbol"]
            or workflow["inputs"]["as_of"] != request["as_of"]):
        raise ValueError("Untrusted or mismatched preparation request")
    root = request_path.parent
    path = root / "preparation.json"
    identity = hashlib.sha256(raw).hexdigest()
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema_version": 1, "request_sha256": identity}
    if state["request_sha256"] != identity:
        raise ValueError("Request changed after preparation began")
    if state.get("status") in {"data_available", "data_available_with_workflow_warnings"}:
        snapshot = root / state["snapshot"]
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != state["snapshot_sha256"]:
            raise ValueError("Prepared snapshot was modified")
        return state
    lock = root / ".preparation.lock"
    with lock.open("x"):
        pass
    try:
        if not client.authenticated():
            state["status"] = "github_cli_authentication_required"
            persist(path, state)
            return state
        if not state.get("dispatch_attempted"):
            state.update(actor=client.api("user")["login"],
                expected_head_sha=client.api(f"repos/{REPOSITORY}/git/ref/heads/main")["object"]["sha"])
            runs = client.api(f"repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/runs?event=workflow_dispatch&branch=main&per_page=30")["workflow_runs"]
            state.update(baseline_run_ids=[r["id"] for r in runs], dispatch_started_at=now.isoformat(),
                         dispatch_attempted=True, status="dispatch_intent")
            persist(path, state)  # A crash or uncertain response must not cause another dispatch.
            try:
                client.dispatch(workflow["inputs"])
            except (TransportError, TimeoutError, subprocess.TimeoutExpired):
                state["status"] = "dispatch_uncertain_do_not_redispatch"
                persist(path, state)
                return state
        if now > timestamp(state["dispatch_started_at"]) + timedelta(minutes=25):
            state["status"] = "preparation_timeout_requires_review"
            persist(path, state)
            return state
        if not state.get("run_id"):
            runs = client.api(f"repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/runs?event=workflow_dispatch&branch=main&per_page=30")["workflow_runs"]
            candidates = [r for r in runs if r["id"] not in state["baseline_run_ids"]
                and r.get("head_branch") == "main" and r.get("event") == "workflow_dispatch"
                and r.get("head_sha") == state["expected_head_sha"] and r.get("actor", {}).get("login") == state["actor"]
                and timestamp(r["created_at"]) >= timestamp(state["dispatch_started_at"]) - timedelta(seconds=5)]
            if len(candidates) != 1:
                state["status"] = "ambiguous_runs_requires_review" if candidates else "awaiting_run"
                persist(path, state)
                return state
            state["run_id"] = candidates[0]["id"]
            state["status"] = "run_bound"
            persist(path, state)
        run = client.api(f"repos/{REPOSITORY}/actions/runs/{state['run_id']}")
        if run.get("head_branch") != "main" or run.get("head_sha") != state["expected_head_sha"] or run.get("event") != "workflow_dispatch":
            raise ValueError("Selected workflow run provenance changed")
        state.update(workflow_status=run["status"], workflow_conclusion=run.get("conclusion"),
                     run_url=run.get("html_url"), checked_at=now.isoformat())
        if run["status"] != "completed":
            state["status"] = "awaiting_workflow_completion"
            persist(path, state)
            return state
        artifacts = client.api(f"repos/{REPOSITORY}/actions/runs/{state['run_id']}/artifacts")["artifacts"]
        artifacts = [a for a in artifacts if a["name"] == "taiwan-research-data-" + str(state["run_id"]) and not a.get("expired")]
        if len(artifacts) != 1:
            state["status"] = "workflow_finished_without_usable_artifact"
            persist(path, state)
            return state
        archive = root / ("artifact-" + str(artifacts[0]["id"]) + ".zip")
        if not archive.exists():
            client.download(artifacts[0]["id"], archive)
        if not zipfile.is_zipfile(archive):
            raise ValueError("Incomplete artifact download; do not overwrite or redispatch")
        target = root / "data"
        if not target.exists():
            extract_archive(archive, target)
        snapshot = verify_snapshot(target, request, state, now)
        state.update(status="data_available" if run.get("conclusion") == "success" else "data_available_with_workflow_warnings",
                     artifact_id=artifacts[0]["id"], artifact_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                     snapshot=str(snapshot.relative_to(root)), snapshot_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                     report_generated=False)
        persist(path, state)
        return state
    finally:
        lock.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--gh")
    args = parser.parse_args(argv)
    config_file = Path(__file__).with_name("project.json")
    config = json.loads(config_file.read_text(encoding="utf-8")) if config_file.exists() else {}
    executable = args.gh or config.get("gh_executable") or shutil.which("gh")
    if not executable or not Path(executable).is_file():
        print(json.dumps({"status": "github_cli_not_installed", "report_generated": False}))
        return 2
    try:
        result = prepare(args.request, GhClient(executable))
    except (ValueError, OSError, TransportError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "preparation_blocked", "error_type": type(exc).__name__, "report_generated": False}))
        return 2
    print(json.dumps({k: result.get(k) for k in ("status", "run_id", "run_url", "snapshot", "workflow_conclusion", "report_generated")}))
    return 0 if result["status"].startswith("data_available") else 1


if __name__ == "__main__":
    raise SystemExit(main())
