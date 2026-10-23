#!/usr/bin/env python3
import argparse
import json
import stat
import zipfile
from pathlib import Path, PurePosixPath

CONTAINERS = {("admin", "plugins"), ("web", "plugins"), ("web", "themes"),
              ("socket", "plugins"), ("socket", "apps")}


def module_entry(name, project):
    parts = PurePosixPath(name.lstrip("/")).parts
    if not parts or parts[0] != project or ".." in parts:
        raise ValueError(f"Unexpected archive path: {name}")
    relative = parts[1:]
    if len(relative) < 3:
        return None
    lane, kind, module = relative[:3]
    if (lane, kind) not in CONTAINERS or module != project:
        raise ValueError(f"Archive writes outside its module: {name}")
    return Path(*relative)


def project_name(archive):
    names = {PurePosixPath(entry.lstrip("/")).parts[0] for entry in archive.namelist() if entry.strip("/")}
    if len(names) != 1:
        raise ValueError(f"Expected one project folder, found: {sorted(names)}")
    name = names.pop()
    if not name.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"Invalid project name: {name}")
    return name


def properties(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if "=" in line and not line.lstrip().startswith("#"))


def install(root, archive_path):
    with zipfile.ZipFile(archive_path) as archive:
        project = project_name(archive)
        files = []
        for entry in archive.infolist():
            relative = module_entry(entry.filename, project)
            if relative is None or entry.is_dir():
                continue
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("ZIP symlinks are not supported")
            files.append((entry, relative))
        modules = {relative.parts[:3] for _, relative in files}
        if not modules:
            raise ValueError(f"No module found in {archive_path}")
        for entry, relative in files:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(entry))
    for lane, kind, module in sorted(modules):
        metadata = properties(root / lane / kind / module / "module.properties")
        activate(root, lane, kind, module)
        print(f"Installed {module} {metadata.get('version', '?')} ({lane}/{kind})")


def activate(root, lane, kind, module):
    runtime_file = root / ".runtime" / lane / f"{kind}.txt"
    entries = json.loads(runtime_file.read_text()) if runtime_file.exists() else []
    if not isinstance(entries, list) or any(not isinstance(item, dict) or "name" not in item for item in entries):
        raise ValueError(f"Invalid runtime module cache: {runtime_file}")
    entries = [item for item in entries if item["name"] != module]
    entries.append({"name": module, "active": True})
    runtime_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_file.write_text(json.dumps(entries) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Extract official module ZIPs into an EzyPlatform folder and activate them")
    parser.add_argument("root", type=Path)
    parser.add_argument("archives", type=Path, nargs="+")
    args = parser.parse_args()
    root = args.root.resolve()
    if not (root / "settings/platform.properties").is_file():
        parser.error(f"{root} is not an EzyPlatform folder")
    print(f"EzyPlatform {properties(root / 'settings/platform.properties').get('ezyplatform.version', '?')}")
    for archive in args.archives:
        install(root, archive)


if __name__ == "__main__":
    main()
