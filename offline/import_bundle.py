"""Verify the pinned local service bundle and install its models and Docker images."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "offline/bundle-20261123.json"


def stream_check(source, spec, name, sink=None):
    digest = hashlib.sha256()
    size = 0
    while chunk := source.read(4 << 20):
        size += len(chunk)
        if size > spec["size"]:
            raise ValueError("Unexpected size: " + name)
        digest.update(chunk)
        if sink is not None:
            sink.write(chunk)
    if size != spec["size"] or digest.hexdigest() != spec["sha256"]:
        raise ValueError("Integrity check failed: " + name)


def check_zip(archive, manifest, staging=None):
    files = manifest["files"]
    with ZipFile(archive) as z:
        names = z.namelist()
        if len(names) != len(files) + 1 or set(names) != set(files) | {"manifest.json"}:
            raise ValueError("Unexpected or duplicate ZIP entries")
        if z.getinfo("manifest.json").file_size > 1024 * 1024 or json.loads(z.read("manifest.json")) != manifest:
            raise ValueError("This archive does not match the repository manifest")
        for name, spec in files.items():
            if z.getinfo(name).file_size != spec["size"]:
                raise ValueError("Unexpected size: " + name)
            with z.open(name) as source:
                if staging is None:
                    stream_check(source, spec, name)
                else:
                    dest = staging / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with dest.open("xb") as output:
                        stream_check(source, spec, name, output)


def missing_images(images):
    missing = []
    for tag, expected in images.items():
        proc = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", tag],
                              text=True, capture_output=True)
        if proc.returncode:
            if "no such" not in proc.stderr.lower():
                raise RuntimeError(proc.stderr.strip())
            missing.append(tag)
        elif proc.stdout.strip() not in expected:
            raise ValueError("A different local image uses this tag: " + tag)
    return missing


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("archive", type=Path)
    cli.add_argument("--verify-only", action="store_true", help="Check the archive without extracting or using Docker")
    args = cli.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if args.verify_only:
        check_zip(args.archive, manifest)
        print("Archive verified:", len(manifest["files"]), "files")
        return
    engine = subprocess.check_output(["docker", "info", "--format", "{{.OSType}}/{{.Architecture}}"], text=True).strip()
    if engine not in {"linux/amd64", "linux/x86_64"}:
        raise ValueError("This bundle requires a Linux x86-64 Docker engine; found " + engine)
    images = manifest["images"]
    missing = missing_images(images)
    base = ROOT / ".local/offline"
    base.mkdir(parents=True, exist_ok=True)
    target = base / manifest["version"]
    if target.exists():
        check_zip(args.archive, manifest)
        for name, spec in manifest["files"].items():
            with (target / name).open("rb") as installed:
                stream_check(installed, spec, str(target / name))
    else:
        with tempfile.TemporaryDirectory(dir=base) as temporary:
            staging = Path(temporary) / "verified"
            staging.mkdir()
            check_zip(args.archive, manifest, staging)
            staging.rename(target)
    if missing:
        subprocess.run(["docker", "load", "-i", str(target / "services.tar")], check=True)
        if missing_images(images):
            raise ValueError("Docker did not load every expected image")
    print("Verified models:", target / "models")
    print("Verified Docker images:", len(images))


if __name__ == "__main__":
    main()
