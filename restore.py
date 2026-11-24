"""Prepare a pinned Nexent source tree and bind a portable review configuration."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / ".local"
SRC = LOCAL / "nexent"
REVIEW = LOCAL / "review"
PROFILE = SRC / "backend/management/services/agent/case_review_profiles.json"
REV = "628b53d66c875094eb6fa1ed3b2dff6590bad9e5"
BASE = "https://github.com/ModelEngine-Group/nexent.git"


def run(*args, cwd=ROOT, **kwargs):
    return subprocess.run([str(a) for a in args], cwd=cwd, check=True, **kwargs)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def prepare():
    LOCAL.mkdir(exist_ok=True)
    if not SRC.exists():
        run("git", "-c", "core.autocrlf=false", "clone", "--depth", "1", "--branch", "v2.8.5", BASE, SRC)
    head = run("git", "rev-parse", "HEAD", cwd=SRC, capture_output=True).stdout.decode().strip()
    if head != REV:
        raise SystemExit("Unexpected upstream revision: " + head)
    for f in (ROOT / "overlay").rglob("*"):
        if f.is_file():
            dest = SRC / f.relative_to(ROOT / "overlay")
            if dest == PROFILE and dest.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, dest)
    shutil.copytree(ROOT / "review", REVIEW, dirs_exist_ok=True)
    for name, digest in read(REVIEW / "本地法规库/snapshot-hashes.json")["files"].items():
        if hashlib.sha256((REVIEW / "本地法规库" / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Snapshot hash mismatch: " + name)
    env = SRC / "deploy/env/.env"
    if not env.exists():
        shutil.copyfile(SRC / "deploy/env/.env.example", env)
    monitoring = SRC / "deploy/env/monitoring.env"
    if not monitoring.exists():
        shutil.copyfile(SRC / "deploy/env/monitoring.env.example", monitoring)
    print(SRC)


def compose(*args):
    env = dict(os.environ, REVIEW_SNAPSHOT_ROOT=REVIEW.as_posix())
    return run("docker", "compose", "-p", "nexent", "--env-file", "deploy/docker/.env.generated",
               "--env-file", "deploy/env/.env", "-f", "deploy/docker/compose/docker-compose.prod.yml",
               "-f", ROOT / "restore/compose.yaml", *args, cwd=SRC, env=env)


def start():
    prepare()
    if not (SRC / "deploy/docker/.env.generated").exists():
        if os.name == "nt":
            run(sys.executable, "-X", "utf8", "deploy_windows.py", cwd=SRC)
        else:
            run("bash", "./deploy.sh", "docker", "--defaults", "--version", "v2.8.5", "--components",
                "infrastructure,application,data-process,supabase", "--port-policy", "production",
                "--image-source", "general", cwd=SRC)
    run("docker", "build", "-f", "restore/backend.Dockerfile", "-t", "nexent-test/backend:2.8.5", ".")
    run("docker", "build", "-f", "deploy/images/dockerfiles/web/Dockerfile", "-t", "nexent-test/web:2.8.5", ".", cwd=SRC)
    compose("up", "-d", "--no-deps", "--pull", "never", "nexent-config", "nexent-runtime",
            "nexent-northbound", "nexent-web", "review-rules")


def review_agent(template, target, native_export=None):
    data = read(native_export) if native_export else template
    agent = data["agent_info"][str(data["agent_id"])]
    source_models = agent.get("model_ids") or []
    if (len(data["agent_info"]) != 1 or agent["name"] != "discipline_case_review"
            or agent.get("model_names") != ["deepseek-flash"] or len(source_models) > 1
            or agent.get("managed_agents") or agent.get("skill_names")
            or agent.get("business_logic_model_id") or agent.get("business_logic_model_name")
            or [m["mcp_server_name"] for m in data["mcp_info"]] != ["review_rules"]):
        raise ValueError("This restore profile supports the single discipline_case_review agent with deepseek-flash and review_rules.")
    override_key = str(source_models[0]) if source_models else "deepseek-flash"
    overrides = agent.get("model_params_override") or {}
    if set(overrides) - {override_key}:
        raise ValueError("Unrecognized model parameter binding")
    data["mcp_info"] = template["mcp_info"]
    agent["author"] = template["agent_info"]["1"]["author"]
    agent["tenant_id"] = target["tenant"]
    agent["model_ids"] = [target["model_id"]]
    agent["model_params_override"] = {str(target["model_id"]): overrides[override_key]} if overrides else {}
    for tool in agent["tools"]:
        if tool["class_name"] == "KnowledgeBaseSearchTool":
            if len(tool["params"].get("index_names") or []) > 1:
                raise ValueError("This restore profile binds one review knowledge base")
            tool["params"]["index_names"] = [target["index"]]
    return data


def tenant(command, email, kb, native_export=None):
    cfg = ROOT / "review"
    hashes = read(cfg / "本地法规库/snapshot-hashes.json")
    request = dict(command=command, email=email, kb=kb, models=read(cfg / "models.json"),
                   parser=read(cfg / "parser.json"), hashes=hashes["files"],
                   sql=(ROOT / "database/test-seed.sql").read_text(encoding="utf-8"))
    if command == "bind" and not (LOCAL / "review-owner.json").exists():
        raise SystemExit("Generate agent.json before binding.")
    request["expected_tenant"] = read(LOCAL / "review-owner.json") if (LOCAL / "review-owner.json").exists() else None
    helper = (ROOT / "restore/tenant.py").read_text(encoding="utf-8")
    p = run("docker", "exec", "-i", "-w", "/opt/backend", "-e", "PYTHONPATH=/opt/backend",
            "nexent-runtime", "python", "-c", helper,
            input=json.dumps(request).encode(), stdout=subprocess.PIPE)
    result = json.loads(p.stdout.decode().strip().splitlines()[-1])
    if command == "seed":
        print("Model catalog and parser defaults are ready.")
        return
    owner = LOCAL / "review-owner.json"
    if owner.exists() and read(owner) != result["tenant"]:
        raise SystemExit("This review MCP snapshot belongs to another tenant. Use a separate deployment.")
    if command == "agent":
        a = review_agent(read(cfg / "agent.template.json"), result, native_export)
        snapshot = REVIEW / "本地法规库"
        write(snapshot / "source-locations.json", result["locations"])
        hashes["files"]["source-locations.json"] = hashlib.sha256((snapshot / "source-locations.json").read_bytes()).hexdigest()
        write(snapshot / "integrity.json", hashes)
        write(owner, result["tenant"])
        write(LOCAL / "agent.json", a)
        print(LOCAL / "agent.json")
    else:
        if not owner.exists():
            raise SystemExit("Generate the tenant agent.json before binding.")
        profiles = read(PROFILE)
        target = profiles.setdefault(result["tenant"], {})
        target.setdefault(str(result["agent"]), {"first_version": result["version"], "law_service": "review_rules"})
        write(PROFILE, profiles)
        print("Bound published agent", result["agent"], "version", result["version"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "start", "seed", "agent", "bind"])
    parser.add_argument("--account", help="Registered destination Nexent administrator email")
    parser.add_argument("--agent-export", type=Path, help="Native review agent JSON to rebind with the agent command")
    parser.add_argument("--kb", default="纪检审理本地法规库")
    args = parser.parse_args()
    if args.agent_export and args.command != "agent":
        parser.error("--agent-export requires the agent command")
    if args.command == "prepare":
        prepare()
    elif args.command == "start":
        start()
    elif not args.account or not PROFILE.exists():
        parser.error("Run prepare/start first and supply --account.")
    else:
        tenant(args.command, args.account, args.kb, args.agent_export)
