"""CLI entry point.

    python backup.py all --output ./homelab --context my-cluster
    python backup.py capture --output ./homelab
    python backup.py generate --output ./homelab --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from . import capture, generate
from .filetracker import RunReport
from .procutil import ToolError, require_tool

_SCRIPT_SOURCE_DIR = Path(__file__).resolve().parent.parent  # .../scripts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backup.py",
        description="Back up a Kubernetes cluster into a Flux-GitOps-ready repo.",
    )
    parser.add_argument(
        "subcommand",
        nargs="?",
        default="all",
        choices=["capture", "generate", "all"],
        help="capture: read the cluster only. generate: synthesize Flux CRs from a "
        "prior capture only (offline). all: both, in order. Default: all.",
    )
    parser.add_argument(
        "--output",
        default="./homelab",
        help="Output repo directory (default: ./homelab).",
    )
    parser.add_argument("--context", default=None, help="kubectl context to use (default: current).")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print planned actions without writing any files."
    )
    parser.add_argument("--verbose", action="store_true", help="Per-resource logging.")
    return parser


def _check_tools() -> None:
    require_tool("kubectl", "Install it from https://kubernetes.io/docs/tasks/tools/.")
    require_tool("helm", "Install it from https://helm.sh/docs/intro/install/.")
    try:
        import ruamel.yaml  # noqa: F401
    except ImportError as exc:
        raise ToolError(
            "The 'ruamel.yaml' Python package is required. Install it with: pip install ruamel.yaml"
        ) from exc


def _check_output_is_safe(output: Path) -> None:
    """Refuse to run at all if --output overlaps with this tool's own
    scripts/ source directory, in either direction.

    This is not hypothetical: running with cwd inside scripts/ (or a
    relative --output that happened to resolve there) previously made
    _copy_scripts_into_repo() shutil.copytree() scripts/ into a
    subdirectory of itself. Each run nested one layer deeper into the
    PREVIOUS run's leftover copy (nothing failed the first few times --
    copytree only errors once the accumulated path length exceeds
    Windows' ~260-char MAX_PATH), so the failure showed up several runs
    after the actual mistake, as repeated .../scripts/homelab/scripts/
    homelab/... segments and `[WinError 3] The system cannot find the
    path specified`. Even short of that crash, --output landing inside
    scripts/ (or scripts/ landing inside --output) lets generate.py's
    FileTracker treat this tool's own source files as orphans to sweep.
    """
    src = _SCRIPT_SOURCE_DIR.resolve()
    out = output.resolve()
    if out == src or out.is_relative_to(src) or src.is_relative_to(out):
        raise ToolError(
            f"--output ({out}) overlaps with this tool's own source directory ({src}). "
            "Point --output at a separate directory -- the actual repo you're backing up "
            "into -- not a path inside scripts/ or an ancestor of it. (If you got here via "
            "a relative --output, double-check your current working directory.)"
        )


def _copy_scripts_into_repo(output: Path, dry_run: bool) -> None:
    """Mirror the tool's own source into <output>/scripts/, replacing
    whatever was there before -- not a purely additive copy. Without a
    full replace, a file removed or renamed in the source (e.g. a module
    split apart or renamed) would linger forever in every repo this tool
    was ever run against, since nothing else ever revisits scripts/.
    """
    dest = output / "scripts"
    if dry_run:
        return
    if dest.resolve() == _SCRIPT_SOURCE_DIR.resolve():
        return
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(_SCRIPT_SOURCE_DIR, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))


def _activate_pre_commit_hook(output: Path, dry_run: bool) -> None:
    """If `output` is (or already is) a git repo, point it at the tracked
    .githooks/pre-commit gate. Never touches core.hooksPath if it's already
    set to something else -- this only activates the default we ship, it
    doesn't override an operator's own hook setup.
    """
    if dry_run or not (output / ".git").is_dir():
        return
    current = subprocess.run(
        ["git", "-C", str(output), "config", "--get", "core.hooksPath"],
        capture_output=True, text=True,
    )
    if current.returncode == 0 and current.stdout.strip() not in ("", ".githooks"):
        print(f"\ncore.hooksPath is already set to '{current.stdout.strip()}'; leaving it alone.")
        return
    subprocess.run(["git", "-C", str(output), "config", "core.hooksPath", ".githooks"], check=False)
    print("\ngit config core.hooksPath set to .githooks (runs scripts/tests before each commit).")


def _print_report(title: str, report: RunReport, stats: dict) -> None:
    print()
    print(f"== {title} ==")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    print(f"  files added:    {len(report.added)}")
    print(f"  files modified: {len(report.modified)}")
    print(f"  files unchanged:{len(report.unchanged)}")
    print(f"  files removed:  {len(report.removed)}")
    if report.removed:
        for rel in report.removed:
            print(f"    - removed: {rel}")
    if report.warnings:
        print(f"  warnings: {len(report.warnings)}")
        for w in report.warnings:
            print(f"    ! {w}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        _check_tools()
    except ToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    output = Path(args.output).resolve()
    try:
        _check_output_is_safe(output)
    except ToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"[dry-run] would write to: {output}")
    output.mkdir(parents=True, exist_ok=True)

    try:
        if args.subcommand in ("capture", "all"):
            report, stats = capture.run(output, args.context, args.dry_run, args.verbose)
            _print_report("capture summary", report, stats)

        if args.subcommand in ("generate", "all"):
            report, stats = generate.run(output, args.dry_run, args.verbose)
            _print_report("generate summary", report, stats)

        if args.subcommand == "all":
            _copy_scripts_into_repo(output, args.dry_run)
            print(f"\nscripts/ copied into {output / 'scripts'}" if not args.dry_run else "\n[dry-run] would copy scripts/ into output repo")

        if args.subcommand in ("generate", "all"):
            _activate_pre_commit_hook(output, args.dry_run)

    except ToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("\nDone." if not args.dry_run else "\n[dry-run] no files were written.")
    return 0
