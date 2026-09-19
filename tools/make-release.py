#!/usr/bin/env python3
"""Build deterministic source archives and checksummed Arch package recipes."""
import argparse
import gzip
import hashlib
import io
from pathlib import Path
import shutil
import tarfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_SHA256 = "d0e405c3703f9cde5f0f3662be2b30d2a31bbd6bc1a3788fb1017ead642dfaad"
COMMIT = "31c6b3802071a6efc6fbd5a821e9c97f97240fd7"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def release(output):
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    prefix = f"spatial-workbench-{version}"
    core = output / "core"
    companion = output / "companion"
    orender = output / "orender"
    core.mkdir(parents=True, exist_ok=True)
    companion.mkdir(parents=True, exist_ok=True)
    orender.mkdir(parents=True, exist_ok=True)
    content = io.BytesIO()
    included = ("spatial", "tests", "docs", "examples", "config", "plasma", "wireplumber", "LICENSES",
                "packaging", "tools", ".github", "LICENSE", "README.md", "STATUS.md",
                "pyproject.toml", "build.sh", "install.sh", "Makefile", "MANIFEST.in",
                "CONTRIBUTING.md", ".gitignore", "test-results.txt")
    with tarfile.open(fileobj=content, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for item in included:
            path = ROOT / item
            if not path.exists():
                continue
            paths = sorted(path.rglob("*")) if path.is_dir() else [path]
            for file in paths:
                if not file.is_file() or "__pycache__" in file.parts or file.suffix == ".pyc":
                    continue
                if file.is_symlink():
                    raise ValueError(f"Release inputs must be regular files: {file}")
                info = archive.gettarinfo(str(file), arcname=f"{prefix}/{file.relative_to(ROOT)}")
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                info.mode = 0o755 if file.stat().st_mode & 0o111 else 0o644
                with file.open("rb") as handle:
                    archive.addfile(info, handle)
    source = core / f"{prefix}.tar.gz"
    source.write_bytes(gzip.compress(content.getvalue(), mtime=0))
    recipe = (ROOT / "packaging/PKGBUILD.in").read_text().replace("@VERSION@", version)
    (core / "PKGBUILD").write_text(recipe.replace("@SOURCE_SHA256@", digest(source)))
    shutil.copyfile(ROOT / "plasma/companion.patch", companion / "companion.patch")
    shutil.copyfile(ROOT / "packaging/companion-PROVENANCE.md", companion / "PROVENANCE.md")
    shutil.copyfile(ROOT / "plasma/tests/CMakeLists.txt", companion / "native-qml-CMakeLists.txt")
    shutil.copyfile(ROOT / "plasma/tests/qml-smoke.cpp", companion / "native-qml-smoke.cpp")
    replacements = {
        "@VERSION@": version, "@UPSTREAM_SHA256@": UPSTREAM_SHA256,
        "@PATCH_SHA256@": digest(companion / "companion.patch"),
        "@PROVENANCE_SHA256@": digest(companion / "PROVENANCE.md"),
        "@QML_CMAKE_SHA256@": digest(companion / "native-qml-CMakeLists.txt"),
        "@QML_SMOKE_SHA256@": digest(companion / "native-qml-smoke.cpp"),
    }
    recipe = (ROOT / "packaging/companion-PKGBUILD.in").read_text()
    for token, value in replacements.items():
        recipe = recipe.replace(token, value)
    (companion / "PKGBUILD").write_text(recipe)
    shutil.copyfile(ROOT / "packaging/orender-loopback.patch", orender / "orender-loopback.patch")
    shutil.copyfile(ROOT / "packaging/orender-live-channels.patch", orender / "orender-live-channels.patch")
    shutil.copyfile(ROOT / "packaging/orender-Cargo.lock", orender / "orender-Cargo.lock")
    recipe = (ROOT / "packaging/orender-PKGBUILD.in").read_text().replace(
        "@PATCH_SHA256@", digest(orender / "orender-loopback.patch")).replace(
        "@LIVE_PATCH_SHA256@", digest(orender / "orender-live-channels.patch")).replace(
        "@LOCK_SHA256@", digest(orender / "orender-Cargo.lock"))
    (orender / "PKGBUILD").write_text(recipe)
    print(f"{source}: sha256 {digest(source)}")
    print(f"{companion / 'PKGBUILD'}: pinned upstream {COMMIT}")
    print(f"{orender / 'PKGBUILD'}: pinned Omniphony 0.5.2 with local OSC and positioned PCM input patches")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/arch")
    release(parser.parse_args().output.resolve())
