#!/usr/bin/env python3
"""Apply a reviewed, hash-validated filesystem patch to a stopped production volume.

This is NOT the local plugin installer and never changes JDBC settings or runs SQL.
Requires the exact PayFS volume, service replicas=0, no running volume users,
verified before-state fingerprints, and a new rollback directory outside runtime.
The payload contains only pinned official module directories and runtime caches.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path, PurePosixPath

VOLUME = "production-services-ezyplatformapp-kuwm2o_ezyplatform-data"
SERVICE = "production-services-ezyplatformapp-kuwm2o_ezy-payfs-landing"
ROOT = Path("/var/lib/docker/volumes") / VOLUME / "_data"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(path):
    if not path.exists():
        return None
    records = []
    for entry in sorted(path.rglob("*")):
        if entry.is_symlink():
            raise ValueError(f"Symlink rejected: {entry}")
        if entry.is_file():
            records.append([entry.relative_to(path).as_posix(), sha(entry)])
    return hashlib.sha256(json.dumps(sorted(records), separators=(",", ":")).encode()).hexdigest()


def relative(value):
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise ValueError(f"Unsafe path: {value}")
    return Path(*p.parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--rollback", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = ROOT.resolve()
    payload = args.payload.resolve()
    rollback = args.rollback.resolve()
    if root != ROOT or rollback == root or root in rollback.parents or rollback.exists():
        parser.error("Unexpected runtime or rollback location")
    if subprocess.check_output(["docker", "ps", "-q", "--filter", "volume=" + VOLUME], text=True).strip():
        parser.error("Application volume still has running containers")
    replicas = subprocess.check_output(["docker", "service", "inspect", SERVICE, "--format", "{{.Spec.Mode.Replicated.Replicas}}"], text=True).strip()
    if replicas != "0":
        parser.error("Service must have replicas=0")
    manifest = json.loads((payload / "manifest.json").read_text())
    for name, expected in manifest["unchanged"].items():
        p = root / relative(name)
        if p.is_symlink() or root not in p.resolve().parents or sha(p) != expected:
            raise ValueError(f"Before-state changed: {name}")
    for item in manifest["modules"]:
        rel = relative(item["path"])
        if len(rel.parts) != 3 or rel.parts[0] not in ("admin", "web", "socket") or rel.parts[1] not in ("plugins", "apps") or rel.parts[2] not in manifest["locked_names"]:
            raise ValueError(f"Unapproved module path: {rel}")
        current = root / rel
        incoming = payload / "runtime" / rel
        if current.is_symlink() or incoming.is_symlink() or root not in current.resolve().parents or payload not in incoming.resolve().parents:
            raise ValueError(f"Unsafe module: {rel}")
        if tree(current) != item["before"] or tree(incoming) != item["after"]:
            raise ValueError(f"Module fingerprint mismatch: {rel}")
    for item in manifest["quarantine"]:
        rel = relative(item["source"])
        p = root / rel
        if len(rel.parts) != 5 or rel.parts[0] not in ("admin", "web", "socket") or rel.parts[1] not in ("plugins", "themes") or rel.parts[3] != "lib" or p.suffix != ".jar":
            raise ValueError(f"Unapproved JAR path: {rel}")
        if p.is_symlink() or root not in p.resolve().parents or sha(p) != item["sha256"]:
            raise ValueError(f"JAR fingerprint mismatch: {rel}")
    for item in manifest["caches"]:
        rel = relative(item["path"])
        if rel.as_posix() not in (".runtime/admin/plugins.txt", ".runtime/web/plugins.txt", ".runtime/socket/plugins.txt"):
            raise ValueError(f"Unapproved cache path: {rel}")
        current = root / rel
        incoming = payload / "runtime" / rel
        if current.is_symlink() or root not in current.resolve().parents or payload not in incoming.resolve().parents:
            raise ValueError(f"Unsafe cache: {rel}")
        if (sha(current) if current.exists() else None) != item["before"] or sha(incoming) != item["after"]:
            raise ValueError(f"Cache fingerprint mismatch: {rel}")
        data = json.loads(incoming.read_text())
        if not isinstance(data, list) or any(not isinstance(x, dict) or "name" not in x for x in data):
            raise ValueError(f"Invalid cache: {rel}")
    print(f"Validated {len(manifest['modules'])} modules, {len(manifest['quarantine'])} JARs and {len(manifest['caches'])} caches; no DB/settings changes")
    if not args.apply:
        print("Dry-run only")
        return
    rollback.mkdir(parents=True, mode=0o700)
    shutil.copy2(payload / "manifest.json", rollback / "manifest.json")
    operations = []
    def record(operation):
        operations.append(operation)
        (rollback / "journal.json").write_text(json.dumps(operations, indent=2) + "\n")
    try:
        for item in manifest["quarantine"]:
            rel = relative(item["source"])
            saved = rollback / "quarantine" / rel
            saved.parent.mkdir(parents=True, exist_ok=True)
            record({"kind": "quarantine", "path": str(rel)})
            shutil.move(str(root / rel), str(saved))
        for item in manifest["modules"]:
            rel = relative(item["path"])
            current = root / rel
            saved = rollback / "modules" / rel
            existed = current.exists()
            record({"kind": "module", "path": str(rel), "existed": existed})
            if existed:
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(current), str(saved))
            current.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(payload / "runtime" / rel, current)
        for item in manifest["caches"]:
            rel = relative(item["path"])
            current = root / rel
            saved = rollback / "caches" / rel
            existed = current.exists()
            if existed:
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(current, saved)
            record({"kind": "cache", "path": str(rel), "existed": existed})
            current.parent.mkdir(parents=True, exist_ok=True)
            temporary = current.with_name(current.name + ".recovery-new")
            shutil.copy2(payload / "runtime" / rel, temporary)
            temporary.replace(current)
        for name, expected in manifest["unchanged"].items():
            if sha(root / relative(name)) != expected:
                raise ValueError(f"Protected file changed: {name}")
        for item in manifest["modules"]:
            if tree(root / relative(item["path"])) != item["after"]:
                raise ValueError(f"After-state mismatch: {item['path']}")
        (rollback / "COMPLETED").write_text("Filesystem patch completed; database not touched by this tool.\n")
        print(f"Applied patch; rollback originals and journal: {rollback}")
    except Exception:
        # Reverse only filesystem operations; no database migrations occur here.
        for op in reversed(operations):
            rel = relative(op["path"])
            current = root / rel
            if op["kind"] == "cache":
                saved = rollback / "caches" / rel
                if saved.exists():
                    shutil.copy2(saved, current)
                elif not op["existed"]:
                    current.unlink(missing_ok=True)
                current.with_name(current.name + ".recovery-new").unlink(missing_ok=True)
            elif op["kind"] == "module":
                saved = rollback / "modules" / rel
                if saved.exists():
                    if current.exists():
                        shutil.rmtree(current)
                    shutil.move(str(saved), str(current))
                elif not op["existed"] and current.exists():
                    shutil.rmtree(current)
            else:
                saved = rollback / "quarantine" / rel
                if saved.exists():
                    current.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(saved), str(current))
        raise


if __name__ == "__main__":
    main()
