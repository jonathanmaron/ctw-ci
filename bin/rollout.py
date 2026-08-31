#!/usr/bin/env python3
"""
Roll the php-library workflow out across the ctw libraries.

Works on the clones you already have side by side on disk, so what it writes is
what you can read, run and amend before anything reaches a remote.

Dry run by default: it prints what it would do and changes nothing. --apply
writes .github/workflows/ci.yml and commits it; --push then pushes the branch
the repository is on.

    python3 bin/rollout.py                            # plan for every sibling
    python3 bin/rollout.py --only ctw-http            # plan for one
    python3 bin/rollout.py --only ctw-http --apply
    python3 bin/rollout.py --apply --push             # everything that is ready

Work in batches. These repositories have only ever run a single test job, so
the first run of the full pipeline is as likely to surface a genuine static
analysis or package hygiene failure as it is to surface a CI problem.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

WORKFLOW = "jonathanmaron/ctw-ci/.github/workflows/php-library.yml@v1"

# PHP versions the pipeline covers. A library whose composer.json allows an
# older version than these is reported, not silently tested on the wrong set.
PHP_VERSIONS = ["8.5"]

# Tried out rather than supported: the suite runs and cannot fail the build.
# Empty for now — the caller still gets the input, commented out, so turning a
# canary back on is one line rather than a hunt through the README.
PHP_VERSIONS_CANARY = []

# ctw/ctw-qa below this swallows PHPStan's exit code, so the PHPStan job would
# report success no matter what it found.
MIN_CTW_QA_MAJOR = 6

# Composer scripts the pipeline calls by name.
REQUIRED_SCRIPTS = ("test", "phpstan", "ecs", "rector")

CI_YML = """# Pipeline for {package}.
#
# The jobs themselves live in the php-library workflow, in jonathanmaron/ctw-ci.
# Everything specific to this repository is below.
#
# The triggers are the one part of that workflow that cannot be shared: a
# reusable workflow may not declare its own `on:`, so the paths that make a
# change worth a run are written out here instead:
#
#   - Pull requests are path-filtered, which is where the volume is. A pull
#     request touching none of these paths starts no run at all — and GitHub
#     treats no run as not succeeded, so leave "Require status checks" off, or
#     add the paths such a pull request touches.
#   - Pushes to {branch} and tags always run, filtered by nothing. `paths:`
#     applies to a whole `push` event and cannot be narrowed to branches, so
#     rather than let it swallow a release tag it is left off the push trigger
#     entirely. A merge to {branch} is post-merge verification; a tag is a
#     release.
#   - A schedule is the drift detector: it reads the dependency graph rather
#     than the diff, so it is filtered by nothing either. It is also what makes
#     Composer outdated run, that job being scheduled-only.
#
# One deliberate divergence from a pipeline that runs on every push: GitHub
# cannot express "push, unless a pull request is open for this ref" without
# duplicating every run, so a branch gets its pipeline when the pull request
# opens. Use the Actions tab's Run workflow button for an ad-hoc run before
# then.

name: CI

on:
    push:
        branches:
            - {branch}
        tags:
            - '**'
    pull_request:
        paths:
            - '**/*.php'
            - '**/*.phtml'
            - 'composer.json'
            - 'phpunit.xml.dist'
            - '.github/workflows/**'
    schedule:
        # Weekly, Monday 04:17 UTC. The odd minute keeps it off the hour, where
        # GitHub queues every cron in the world.
        - cron: '17 4 * * 1'
    workflow_dispatch:

concurrency:
    group: ${{{{ github.workflow }}}}-${{{{ github.ref }}}}
    cancel-in-progress: true

permissions:
    contents: read

jobs:
    php-library:
        uses: {workflow}
        with:
            php_version: '{php_version}'
            php_versions: '{php_versions}'
{canary}
            # Every job is on by default. Uncomment to switch one off.
            #
            # phpunit: false
            # phpunit_lowest_deps: false
            # phpstan: false
            # ecs: false
            # rector: false
            # composer_validate: false
            # composer_normalize: false
            # composer_audit: false
            # composer_dependencies: false
            # composer_outdated: false
            # backward_compatibility: false
            # infection: true
"""

CANARY = """
            # {canary_list} is what this library is trying out, not what it
            # supports: the suite runs there and cannot break the build. When it
            # is reliably green, move it into php_versions, where it blocks, and
            # drop this.
            php_versions_canary: '{php_versions_canary}'
"""

# What the caller gets when PHP_VERSIONS_CANARY is empty. Commented out rather
# than omitted, because the reason a canary is off is worth carrying next to
# the switch that turns it on.
NO_CANARY = """
            # No canary for now: 8.6 is deliberately not tested. Where
            # these libraries reach Laminas the version cannot even be
            # installed — laminas/laminas-diactoros 3.8.0 declares
            # php ~8.2.0 || ~8.3.0 || ~8.4.0 || ~8.5.0 — and a job that fails
            # at composer update reports nothing about the library. Uncomment
            # once the ecosystem allows 8.6.
            #
            # php_versions_canary: '["8.6"]'
"""

COMMIT_MESSAGE = """\U0001f477 ci: Replaced the single test job with the shared php-library pipeline

The workflow now runs thirteen jobs instead of one: PHPUnit, PHPUnit
lowest deps, PHPUnit canary, PHPStan, ECS, Rector, Composer validate,
Composer normalize, Composer audit, Composer dependencies, Composer
outdated, Backward compatibility and Infection.

The job definitions live in jonathanmaron/ctw-ci and are called with
uses:, pinned to the v1 major, so ci.yml keeps only what is specific to
this repository.

Infection is off by default and Composer outdated runs on the weekly
schedule only, so eleven of the thirteen run on a push.
"""


def git(repository: Path, *arguments, check=False):
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True, text=True,
    )
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout.strip(), result.returncode


def default_branch(repository: Path) -> str:
    head, code = git(repository, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if not code and head.startswith("origin/"):
        return head[len("origin/"):]
    branch, code = git(repository, "rev-parse", "--abbrev-ref", "HEAD")
    return branch if not code else "master"


def tracked(repository: Path, path: str) -> bool:
    out, code = git(repository, "ls-files", "--error-unmatch", path)
    return code == 0 and bool(out)


def assess(repository: Path) -> dict | None:
    """Decide whether one repository is ready for the shared workflow."""
    if not (repository / ".git").exists():
        return None

    composer_json = repository / "composer.json"
    if not composer_json.exists():
        return None

    try:
        composer = json.loads(composer_json.read_text())
    except json.JSONDecodeError:
        return None

    if composer.get("type", "library") != "library":
        return None

    blockers: list[str] = []
    warnings: list[str] = []

    workflow_dir = repository / ".github" / "workflows"
    caller = workflow_dir / "ci.yml"
    existing = caller.read_text() if caller.exists() else ""

    if "ctw-ci/.github/workflows/php-library.yml" in existing:
        blockers.append("already on the shared workflow")

    scripts = set(composer.get("scripts", {}))
    missing = [name for name in REQUIRED_SCRIPTS if name not in scripts]
    if missing:
        blockers.append("composer scripts missing: " + ", ".join(missing))

    if not (repository / "phpunit.xml.dist").exists():
        blockers.append("no phpunit.xml.dist")

    dirty, _ = git(repository, "status", "--porcelain")
    if dirty:
        blockers.append("working tree is not clean")

    if not existing:
        warnings.append("no ci.yml yet; one will be created")

    local_copy = workflow_dir / "php-library.yml"
    if local_copy.exists():
        warnings.append("a local php-library.yml will be removed in favor of the shared one")

    development = composer.get("require-dev", {})
    qa = development.get("ctw/ctw-qa", "")
    major = re.search(r"(\d+)", qa)
    if not qa:
        warnings.append("no ctw/ctw-qa in require-dev")
    elif major and int(major.group(1)) < MIN_CTW_QA_MAJOR:
        warnings.append(f"ctw/ctw-qa {qa} predates 6.1.0; PHPStan would never fail")

    php = composer.get("require", {}).get("php", "")
    floor = re.search(r"(\d+\.\d+)", php)
    if floor and floor.group(1) < PHP_VERSIONS[0]:
        warnings.append(
            f"composer.json allows php {php}, but the pipeline only covers "
            f"{', '.join(PHP_VERSIONS)}"
        )

    if tracked(repository, "composer.lock"):
        warnings.append("composer.lock is committed; the pipeline resolves afresh")

    return dict(
        path=repository,
        name=repository.name,
        package=composer.get("name", repository.name),
        branch=default_branch(repository),
        blockers=blockers,
        warnings=warnings,
        local_copy=local_copy.exists(),
    )


def render(target: dict) -> str:
    canary = NO_CANARY
    if PHP_VERSIONS_CANARY:
        canary = CANARY.format(
            canary_list=", ".join(PHP_VERSIONS_CANARY),
            php_versions_canary=json.dumps(PHP_VERSIONS_CANARY),
        )
    return CI_YML.format(
        package=target["package"],
        branch=target["branch"],
        workflow=WORKFLOW,
        php_version=PHP_VERSIONS[0],
        php_versions=json.dumps(PHP_VERSIONS),
        canary=canary,
    )


def apply_to(target: dict, push: bool) -> tuple[str | None, str | None]:
    """Write ci.yml, commit it, and optionally push."""
    repository = target["path"]
    workflow_dir = repository / ".github" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)

    (workflow_dir / "ci.yml").write_text(render(target))

    if target["local_copy"]:
        # Superseded by the shared workflow. git rm rather than unlink, so the
        # deletion is staged along with the rewrite.
        git(repository, "rm", "--quiet", ".github/workflows/php-library.yml")

    try:
        git(repository, "add", ".github/workflows/ci.yml", check=True)
        git(repository, "commit", "--quiet", "-m", COMMIT_MESSAGE, check=True)
    except RuntimeError as exception:
        return None, f"commit failed: {exception}"

    revision, _ = git(repository, "rev-parse", "--short", "HEAD")

    if push:
        try:
            git(repository, "push", "origin", target["branch"], check=True)
        except RuntimeError as exception:
            return None, f"push failed: {exception}"
        return f"{revision} pushed to {target['branch']}", None

    return f"{revision} committed, not pushed", None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=None,
                        help="directory holding the clones (default: the parent "
                             "of this repository)")
    parser.add_argument("--only", nargs="*", metavar="REPO",
                        help="limit to these repository directory names")
    parser.add_argument("--apply", action="store_true",
                        help="actually write ci.yml and commit it")
    parser.add_argument("--push", action="store_true",
                        help="with --apply, push each commit to origin")
    arguments = parser.parse_args()

    here = Path(__file__).resolve().parent.parent
    root = (arguments.root or here.parent).resolve()

    candidates = sorted(path for path in root.iterdir()
                        if path.is_dir() and path != here)

    if arguments.only:
        wanted = set(arguments.only)
        candidates = [path for path in candidates if path.name in wanted]
        for name in sorted(wanted - {path.name for path in candidates}):
            print(f"  ?  {name}: no such directory in {root}")

    targets = [target for target in map(assess, candidates) if target]

    ready = sorted((t for t in targets if not t["blockers"]), key=lambda t: t["name"])
    blocked = sorted((t for t in targets if t["blockers"]), key=lambda t: t["name"])

    print(f"{len(targets)} composer libraries in {root}")
    print(f"  {len(ready)} ready, {len(blocked)} blocked\n")

    if blocked:
        print("--- blocked ---")
        for target in blocked:
            print(f"  {target['name']:<44} {'; '.join(target['blockers'])}")
        print()

    print("--- ready ---" if not arguments.apply else "--- applying ---")
    for target in ready:
        print(f"  {target['name']}")
        for warning in target["warnings"]:
            print(f"      warning: {warning}")
        if arguments.apply:
            result, error = apply_to(target, arguments.push)
            print(f"      {'-> ' + result if result else 'FAILED: ' + error}")

    if not arguments.apply:
        print(f"\nDry run. Re-run with --apply to update {len(ready)} repositories.")
        print("Work in batches: --only <repo> [<repo> ...] --apply")

    return 0


if __name__ == "__main__":
    sys.exit(main())
