#!/usr/bin/env python3
"""Install pinned official plugins into an ISOLATED LOCAL runtime only.

Stop local app containers first. Old module directories are moved to --rollback.
Downloaded ZIPs are verified against plugin-lock.json before modifying runtime.
Do not reuse this tool for production deployment/database migration.
"""
import argparse
import hashlib
import json
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


def archive_path(name, project):
    # Official release ZIPs have a leading /<project>/. Normalize ONLY that prefix.
    parts = PurePosixPath(name.lstrip("/")).parts
    if ".." in parts or not parts or parts[0] != project:
        raise ValueError(f"Unexpected archive path: {name}")
    relative = parts[1:]
    if len(relative) < 3:
        if name.endswith("/") and (not relative or (
                relative[0] in ("admin", "web", "socket") and
                (len(relative) == 1 or relative[1] in ("plugins", "apps")))):
            return None  # container directories, never extracted
        raise ValueError(f"Archive writes outside its module: {name}")
    lane, kind, module = relative[:3]
    allowed = {("admin", "plugins"), ("web", "plugins"),
               ("socket", "plugins"), ("socket", "apps")}
    if (lane, kind) not in allowed or module != project:
        raise ValueError(f"Archive writes outside its module: {name}")
    return Path(*relative)


def properties(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if "=" in line and not line.lstrip().startswith("#"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--rollback", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--lock", type=Path, default=Path("plugin-lock.json"))
    parser.add_argument("--only", help="Comma-separated subset of locked plugins")
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    rollback = args.rollback.resolve()
    setup = properties(runtime / "settings/setup.properties")
    if setup.get("datasource.jdbc_url") != "jdbc:mysql://mysql:3306/blog":
        parser.error("Refusing non-local database URL")
    if rollback == runtime or runtime in rollback.parents or rollback.exists():
        parser.error("Rollback must be a new directory outside runtime")
    lock = json.loads(args.lock.read_text())
    if args.only:
        names = set(args.only.split(","))
        if names - {p["name"] for p in lock["plugins"]}:
            parser.error("--only contains an unlocked plugin")
        lock["plugins"] = [p for p in lock["plugins"] if p["name"] in names]
    if properties(runtime / "settings/platform.properties").get("ezyplatform.version") != lock["platform_version"]:
        parser.error("Runtime core version does not match lock")
    args.cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ezy-modules-") as temp:
        staged = Path(temp)
        modules = []
        for plugin in lock["plugins"]:
            name, version = plugin["name"], plugin["version"]
            if not name.isalnum() or not all(c.isdigit() or c == "." for c in version):
                raise ValueError("Invalid locked name/version")
            archive = args.cache / f"{name}-{version}.zip"
            if not archive.exists():
                url = (f"https://ezyplatform.com/api/v1/released-projects/{name}/{version}"
                       f"/download?platformVersion={lock['platform_version']}")
                partial = archive.with_suffix(".zip.partial")
                subprocess.run(["curl", "-fLsS", "--max-time", "120", url, "-o", str(partial)], check=True)
                partial.rename(archive)
            if hashlib.sha256(archive.read_bytes()).hexdigest() != plugin["sha256"]:
                raise ValueError(f"Checksum mismatch: {archive}")
            with zipfile.ZipFile(archive) as z:
                for entry in z.infolist():
                    relative = archive_path(entry.filename, name)
                    if entry.is_dir() or relative is None:
                        continue
                    if stat.S_ISLNK(entry.external_attr >> 16):
                        raise ValueError("ZIP symlinks are not supported")
                    destination = staged / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(z.read(entry))
            for lane, kind in (("admin", "plugins"), ("web", "plugins"),
                               ("socket", "plugins"), ("socket", "apps")):
                relative = Path(lane) / kind / name
                module = staged / relative
                if module.exists():
                    if properties(module / "module.properties").get("version") != version:
                        raise ValueError(f"Module version mismatch: {module}")
                    modules.append(relative)
            print(f"Verified {name} {version}")
        # Preflight current paths and caches too, before the first runtime move.
        for relative in modules:
            current = runtime / relative
            if current.is_symlink() or runtime not in current.resolve().parents:
                raise ValueError(f"Refusing symlink/out-of-runtime module: {current}")
        caches = {}
        for lane in ("admin", "web", "socket"):
            cache_file = runtime / ".runtime" / lane / "plugins.txt"
            cache = json.loads(cache_file.read_text()) if cache_file.exists() else []
            if not isinstance(cache, list) or any(not isinstance(item, dict) or "name" not in item for item in cache):
                raise ValueError(f"Invalid runtime plugin cache: {cache_file}")
            caches[lane] = cache
        # All archives, paths, caches and module versions are validated before moving.
        rollback.mkdir(parents=True, mode=0o700)
        (rollback / "plugin-lock.json").write_text(json.dumps(lock, indent=2) + "\n")
        for relative in modules:
            current = runtime / relative
            if current.exists():
                previous = rollback / relative
                previous.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(current), str(previous))
            current.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged / relative), str(current))
        for lane in ("admin", "web", "socket"):
            cache_file = runtime / ".runtime" / lane / "plugins.txt"
            cache = caches[lane]
            known = {item["name"] for item in cache}
            added = [relative.name for relative in modules
                     if relative.parts[:2] == (lane, "plugins") and relative.name not in known]
            if added:
                previous_cache = rollback / ".runtime" / lane / "plugins.txt"
                previous_cache.parent.mkdir(parents=True, exist_ok=True)
                if cache_file.exists():
                    shutil.copy2(cache_file, previous_cache)
                cache.extend({"name": name, "active": True} for name in added)
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps(cache) + "\n")
        print(f"Installed {len(modules)} local module directories; rollback: {rollback}")


if __name__ == "__main__":
    main()
