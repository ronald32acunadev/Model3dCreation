"""Install actions that need more than one command: resumable downloads, atomic extraction, idempotent patches, source copies."""

import os
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

from model3d_client.setup.runner import Command, StepContext, StepFailed

CHUNK = 1 << 20


def download(url: str, target: Path, ctx: StepContext) -> None:
    """Download into <target>.part, resuming a previous attempt, then rename."""
    if target.exists():
        return
    ctx.log(f"Downloading {url}")
    part = target.with_name(target.name + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    offset = part.stat().st_size if part.exists() else 0
    request = urllib.request.Request(url)
    if offset > 0:
        request.add_header("Range", f"bytes={offset}-")
    with urllib.request.urlopen(request, timeout=60) as response, part.open("ab" if response.status == 206 else "wb") as out:
        while chunk := response.read(CHUNK):
            if ctx.cancelled():
                raise StepFailed("cancelled", "Installation cancelled", "")
            out.write(chunk)
    os.replace(part, target)


def extract_tarball(archive: Path, target: Path, ctx: StepContext) -> None:
    """Extract a GitHub tarball without its top folder; target only appears once extraction finished."""
    ctx.log(f"Extracting {archive.name}")
    partial = target.with_name(target.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    try:
        with tarfile.open(archive, "r:*") as tar:
            for member in tar.getmembers():
                _, _, rest = member.name.partition("/")
                if rest:
                    member.name = rest
                    tar.extract(member, partial, filter="data")
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    if target.exists():
        shutil.rmtree(target)
    os.replace(partial, target)


def _already_applied(tail: list[str]) -> bool:
    reverse = subprocess.run(["patch", "-R", "--dry-run", "--batch", "-f", *tail], capture_output=True)
    return reverse.returncode == 0


def apply_patch(patch: Path, target_dir: Path, ctx: StepContext) -> None:
    """Apply a -p1 patch inside target_dir; a patch already applied is left alone."""
    tail = ["-d", str(target_dir), "-p1", "-i", str(patch)]
    if _already_applied(tail):
        ctx.log(f"{patch.name} already applied")
        return
    ctx.run(Command(["patch", "--forward", "--batch", *tail]))


def patch_file(patch: Path, file: Path, ctx: StepContext) -> None:
    """Patch one file; a symlink (a Hugging Face blob) becomes a patched copy so the shared blob stays as it is."""
    if file.is_symlink():
        tmp = file.with_name(file.name + ".tmp")
        shutil.copyfile(file.resolve(), tmp)
        os.replace(tmp, file)
    tail = [str(file), "-i", str(patch)]
    if _already_applied(tail):
        ctx.log(f"{patch.name} already applied to {file.name}")
        return
    ctx.run(Command(["patch", "--forward", "--batch", *tail]))


def copy_tree(source: Path, target: Path, ctx: StepContext) -> None:
    """Copy a source tree, leaving out environments, caches, and tests."""
    ctx.log(f"Copying {source.name}")
    shutil.copytree(source, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", "tests", "*.pyc"))
