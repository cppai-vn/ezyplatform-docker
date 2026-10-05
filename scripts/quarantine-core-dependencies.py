#!/usr/bin/env python3
"""Quarantine plugin JARs already supplied by core. Dry-run unless --apply.

Use only on a stopped, backed-up runtime. The quarantine must be outside runtime.
This fixes duplicate providers, not plugin/core API compatibility.
"""
import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path


def artifact_name(path):
    # Match the artifact basename, excluding version and optional classifier.
    return re.split(r"-(?=\d+\.)", path.stem, maxsplit=1)[0]


def plan(root):
    moves = []
    for lane in ("admin", "web", "socket"):
        supplied = {}
        for directory in (root / "lib", root / lane / "lib"):
            for jar in sorted(directory.glob("*.jar")):
                supplied.setdefault(artifact_name(jar), jar)
        folders = [root / lane / "plugins"]
        if lane == "web":
            folders.append(root / lane / "themes")
        for folder in folders:
            for jar in sorted(folder.glob("*/lib/*.jar")):
                provider = supplied.get(artifact_name(jar))
                if provider:
                    moves.append((jar, provider))
    return moves


def apply(root, quarantine, moves):
    root = root.resolve()
    quarantine = quarantine.resolve()
    if quarantine == root or root in quarantine.parents:
        raise ValueError("Quarantine must be outside the runtime (including backup scans)")
    if quarantine.exists():
        raise ValueError("Quarantine already exists; choose a new destination")
    manifest = []
    for source, provider in moves:
        relative = source.relative_to(root)
        target = quarantine / relative
        if source.is_symlink() or root not in source.resolve().parents:
            raise ValueError(f"Refusing symlink/out-of-root JAR: {source}")
        manifest.append({"source": str(relative), "provider": str(provider.relative_to(root)),
                         "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
    quarantine.mkdir(parents=True, mode=0o700)
    # Write the complete plan before moving files, so an interrupted run is reviewable.
    (quarantine / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for source, _ in moves:
        target = quarantine / source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--quarantine", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if not (root / "settings/platform.properties").is_file():
        parser.error("--root must be an EzyPlatform runtime")
    moves = plan(root)
    for source, provider in moves:
        print(f"{source.relative_to(root)} -> supplied by {provider.relative_to(root)}")
    print(f"{len(moves)} duplicate providers; mode={'apply' if args.apply else 'dry-run'}")
    if args.apply:
        if args.quarantine is None:
            parser.error("--apply requires --quarantine")
        apply(root, args.quarantine, moves)


if __name__ == "__main__":
    main()
