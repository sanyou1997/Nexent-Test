# Copyright (c) 2025-2026 Huawei Technologies Co., Ltd. All rights reserved.
"""Start this local Nexent installation using Docker Desktop and Git Bash."""

import datetime
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STATUS = ROOT.parent / "nexent-deploy-status.log"
NETWORK = "nexent_network"
PROJECT = "nexent"
EXPECTED = {
    "nexent-elasticsearch", "nexent-postgresql", "nexent-minio", "nexent-redis",
    "nexent-config", "nexent-runtime", "nexent-mcp", "nexent-northbound",
    "nexent-web", "nexent-data-process", "supabase-db-mini",
    "supabase-auth-mini", "supabase-kong-mini",
}


def note(message):
    print(message, flush=True)
    with STATUS.open("a", encoding="utf-8") as log:
        log.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {message}\n")


def run(args, *, check=True):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace")
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result


def docker(*args, check=True):
    return run(["docker", "--context", "desktop-linux", *args], check=check)


def network_exists():
    listing = docker("network", "ls", "--format", "{{.Name}}").stdout.splitlines()
    if NETWORK not in listing:
        return False
    net = json.loads(docker("network", "inspect", NETWORK).stdout)[0]
    owner = (net.get("Labels") or {}).get("com.docker.compose.project")
    if owner != PROJECT or net.get("Driver") != "bridge":
        raise RuntimeError(f"Existing {NETWORK} is not a bridge owned by the Nexent project (owner={owner!r}).")
    return True


PRIVATE_BLOCKS = tuple(ipaddress.ip_network(s) for s in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def occupied_windows_routes(route_table):
    # Group by gateway and interface. Some VPNs express the default route as
    # several prefixes. Those forwarding rules do not allocate LAN subnets.
    by_gateway = {}
    for line in route_table.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        try:
            route = ipaddress.IPv4Network((fields[0], fields[1]), strict=False)
        except ValueError:
            continue
        if route.prefixlen:
            by_gateway.setdefault((fields[2], fields[3]), []).append(route)
    for routes in by_gateway.values():
        total = sum(net.num_addresses for net in ipaddress.collapse_addresses(routes))
        default_like = total >= 255 * 2**24
        for route in routes:
            if default_like and route.prefixlen <= 8 and not any(route.subnet_of(block) for block in PRIVATE_BLOCKS):
                continue
            yield route


def free_subnets():
    used = list(occupied_windows_routes(run(["route.exe", "print", "-4"]).stdout))
    ids = docker("network", "ls", "-q").stdout.split()
    if ids:
        for net in json.loads(docker("network", "inspect", *ids).stdout):
            for cfg in (net["IPAM"].get("Config") or []):
                if cfg.get("Subnet"):
                    used.append(ipaddress.ip_network(cfg["Subnet"], strict=False))
    for block in ("10.239.0.0/16", "172.30.0.0/16", "192.168.0.0/16", "10.0.0.0/8"):
        for candidate in ipaddress.ip_network(block).subnets(new_prefix=24):
            if all(not candidate.overlaps(other) for other in used if other.version == 4):
                yield str(candidate)


def prepare_network():
    if network_exists():
        note("Using the existing Nexent bridge.")
        return
    args = ["network", "create", "--driver", "bridge", "--label",
            "com.docker.compose.project=nexent", "--label", "com.docker.compose.network=nexent"]
    result = docker(*args, NETWORK, check=False)
    if result.returncode == 0 or network_exists():
        note("Nexent bridge is ready.")
        return
    error = result.stderr.strip()
    if not any(s in error.lower() for s in ("fully subnetted", "non-overlapping ipv4 address pool",
                                             "pool overlaps", "address pools are exhausted")):
        raise RuntimeError(error or "Docker network create failed without an error message.")
    note(error)
    for subnet in free_subnets():
        result = docker(*args, "--subnet", subnet, NETWORK, check=False)
        if result.returncode == 0 or network_exists():
            note(f"Nexent bridge is ready on {subnet}.")
            return
        if "overlap" not in result.stderr.lower():
            raise RuntimeError(result.stderr.strip())
    raise RuntimeError("No free private /24 found among the checked Docker networks and Windows routes.")


def deploy():
    bash = Path(os.environ["ProgramFiles"]) / "Git" / "bin" / "bash.exe"
    if not bash.is_file():
        raise RuntimeError(f"Git Bash was not found at {bash}.")
    env = dict(os.environ, DOCKER_CONTEXT="desktop-linux", CHERE_INVOKING="1",
               MSYS2_ARG_CONV_EXCL="/bin/;/usr/;/opt/;/dev/;/var/;/tmp/",
               COMPOSE_PROGRESS="plain")
    command = [str(bash), "--login", "./deploy.sh", "docker", "--defaults", "--version",
               "v2.8.5", "--components", "infrastructure,application,data-process,supabase",
               "--port-policy", "production", "--image-source", "general"]
    # Persist only deployment stages and errors. Scrub configured secrets before logging them.
    protected = []
    for line in (ROOT / "deploy/env/.env").read_text(encoding="utf-8-sig").splitlines():
        key, _, value = line.partition("=")
        if re.search(r"password|token|secret|(?:^|_)key(?:$|_)", key, re.I):
            value = value.strip().strip("'\"")
            if len(value) >= 5:
                protected.append(value)
    with subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, encoding="utf-8", errors="replace") as proc:
        for raw in proc.stdout:
            line = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw).rstrip()
            for secret in protected:
                line = line.replace(secret, "[redacted]")
            if re.search(r"password|secret|authorization|bearer |api.?key|jwt|密码|密钥", line, re.I):
                continue
            if re.search(r"starting |failed|error|denied|unhealthy|timed out|no space left|"
                         r"could not|cannot |exited|not found|失败|错误", line, re.I):
                note(line)
            elif line:
                print(line, flush=True)
        if proc.wait():
            raise RuntimeError("The upstream deployment script stopped. See the preceding error.")


def verify():
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    api = "http://127.0.0.1:5013/nb/v1/health"
    web = "http://127.0.0.1:3000/zh/workbench"
    auth_health = "http://127.0.0.1:3000/api/user/service_health"
    deadline = time.monotonic() + 240
    detail = ""
    while time.monotonic() < deadline:
        states = docker("ps", "--filter", "label=com.docker.compose.project=nexent",
                        "--format", "{{.Names}} {{.Status}}").stdout.splitlines()
        running = {s.split(" ", 1)[0] for s in states}
        missing = sorted(EXPECTED - running)
        health = [s for s in states if "unhealthy" in s or "health: starting" in s]
        try:
            with client.open(web, timeout=4) as page:
                if page.status != 200 or page.headers.get_content_type() != "text/html":
                    raise ValueError("Port 3000 has not returned the Nexent workbench.")
            with client.open(auth_health, timeout=4) as page:
                if page.status != 200:
                    raise ValueError("The web authentication service is not ready.")
            with client.open(api, timeout=4) as page:
                if json.load(page) != {"status": "healthy", "service": "northbound-api"}:
                    raise ValueError("The northbound API health response is unexpected.")
            if not missing and not health:
                note("READY: all 13 containers are running, Nexent web returned HTTP 200, northbound API is healthy.")
                return
            detail = "Pending containers: " + ", ".join(missing + health)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            detail = str(exc)
        time.sleep(4)
    raise RuntimeError("Startup checks timed out. " + detail)


def main():
    if sys.argv[1:] == ["--check"]:
        verify()
        return
    note("Starting the local Nexent deployment.")
    note(docker("info", "--format", "Docker Engine {{.ServerVersion}} / {{.OSType}}").stdout.strip())
    prepare_network()
    deploy()
    verify()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        note(f"FAILED: {exc}")
        sys.exit(1)
