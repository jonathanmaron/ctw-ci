#!/usr/bin/env python3
"""
Validate every reusable workflow under .github/workflows/.

GitHub only reports a mistyped input name when a consumer repository calls the
workflow, which means a bad push here surfaces as a broken run in some other
repository. These checks run in this repository's own pipeline instead:

  1. the file parses as YAML, and no mapping key is defined twice;
  2. it is a reusable workflow: on.workflow_call.inputs is present;
  3. every input declares a type and a description;
  4. every input whose default looks like a JSON array really is one;
  5. every inputs.NAME reference resolves to a declared input;
  6. every declared input is referenced at least once;
  7. every job declares runs-on;
  8. a job reading matrix.NAME declares that matrix dimension;
  9. no step's script assigns a shell variable that shadows one of the
     environment variables in scope for that step.

Run it from anywhere:

    python3 bin/validate-workflows.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

# ${{ inputs.foo }}, and the bare inputs.foo that an `if:` may use.
INPUT_REFERENCE = re.compile(r"inputs\.([A-Za-z0-9_]+)")
MATRIX_REFERENCE = re.compile(r"matrix\.([A-Za-z0-9_]+)")

# A shell assignment at the start of a line. Anchored so that a flag such as
# --working-dir="$CI_TOOLS_DIR" is not mistaken for one.
SHELL_ASSIGNMENT = re.compile(r"(?:^|\n)\s*([A-Za-z_][A-Za-z0-9_]*)=")

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

# The workflow this repository publishes. Its own pipeline is not a reusable
# workflow and is checked only for the things that apply to any workflow.
REUSABLE = ("php-library.yml",)


class DuplicateKey(Exception):
    """A mapping defines the same key twice."""


class Loader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate keys.

    YAML says nothing useful about a repeated key and PyYAML quietly keeps the
    last one, so a file defining a job twice parses clean and looks right. That
    is worth failing on rather than inheriting whichever copy came last.
    """

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise DuplicateKey(f"duplicate key {key!r}")
            seen.add(key)
        return super().construct_mapping(node, deep)


def triggers(document: dict):
    """Return the `on:` block.

    YAML 1.1 reads an unquoted `on` as the boolean true, and PyYAML follows it,
    so the key is True rather than the string every reader expects.
    """
    return document.get("on", document.get(True)) or {}


def environment(*scopes) -> set[str]:
    """Names of the environment variables in scope, innermost last."""
    names: set[str] = set()
    for scope in scopes:
        if isinstance(scope, dict):
            names |= {str(key) for key in (scope.get("env") or {})}
    return names


def validate_inputs(document: dict, raw: str) -> list[str]:
    errors: list[str] = []

    call = triggers(document).get("workflow_call")
    if call is None:
        return ["declares no on.workflow_call: this is not a reusable workflow"]

    declared = call.get("inputs") or {}
    if not declared:
        return ["declares on.workflow_call but no inputs"]

    for name, definition in sorted(declared.items()):
        definition = definition or {}

        if "type" not in definition:
            errors.append(f"input '{name}' declares no type")
        if not definition.get("description"):
            errors.append(f"input '{name}' has no description")

        # The version inputs carry a JSON array in a string, because a reusable
        # workflow input cannot be an array. A default that does not parse
        # would fail at fromJSON() in the consumer's run, not here.
        default = definition.get("default")
        if isinstance(default, str) and default.lstrip().startswith("["):
            try:
                if not isinstance(json.loads(default), list):
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                errors.append(
                    f"input '{name}' has a default that opens like a JSON "
                    f"array but does not parse as one: {default!r}"
                )

    referenced = set(INPUT_REFERENCE.findall(raw))

    for name in sorted(referenced - set(declared)):
        errors.append(f"references undeclared input '{name}'")

    for name in sorted(set(declared) - referenced):
        errors.append(f"declares input '{name}' but never references it")

    return errors


def validate_jobs(document: dict) -> list[str]:
    errors: list[str] = []
    jobs = document.get("jobs") or {}

    if not jobs:
        return ["declares no jobs"]

    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue

        # A job that calls another workflow has no runs-on of its own.
        if "runs-on" not in job and "uses" not in job:
            errors.append(f"job '{job_name}' declares neither runs-on nor uses")

        matrix = ((job.get("strategy") or {}).get("matrix") or {})
        dimensions = {str(key) for key in matrix if key not in ("include", "exclude")}

        body = yaml.safe_dump(job)
        for name in sorted(set(MATRIX_REFERENCE.findall(body)) - dimensions):
            errors.append(
                f"job '{job_name}' reads matrix.{name}, which its strategy "
                f"does not declare"
            )

        errors += validate_shell_shadowing(document, job_name, job)

    return errors


def validate_shell_shadowing(document: dict, job_name: str, job: dict) -> list[str]:
    """Catch a step whose script assigns over an environment variable it reads.

    A step passes its inputs in as environment variables, then often builds up
    state in shell variables of its own. When the two pick the same name the
    assignment wins and the input is silently discarded, which no amount of
    YAML validation would notice.
    """
    errors: list[str] = []

    for index, step in enumerate(job.get("steps") or []):
        if not isinstance(step, dict) or "run" not in step:
            continue

        in_scope = environment(document, job, step)
        assigned = set(SHELL_ASSIGNMENT.findall(str(step["run"])))
        label = step.get("name") or f"step {index + 1}"

        for name in sorted(in_scope & assigned):
            errors.append(
                f"job '{job_name}', {label}: assigns shell variable "
                f"'{name}', which shadows an environment variable of that name"
            )

    return errors


def validate(path: Path) -> list[str]:
    raw = path.read_text()

    try:
        document = yaml.load(raw, Loader=Loader)
    except DuplicateKey as exception:
        return [f"has a {exception}"]
    except yaml.YAMLError as exception:
        return [f"is not valid YAML: {exception}"]

    if not isinstance(document, dict):
        return ["is not a YAML mapping"]

    errors = validate_jobs(document)

    if path.name in REUSABLE:
        errors = validate_inputs(document, raw) + errors

    return errors


def main() -> int:
    workflows = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))

    if not workflows:
        print(f"No workflows found under {WORKFLOWS}", file=sys.stderr)
        return 1

    missing = [name for name in REUSABLE if not (WORKFLOWS / name).exists()]
    if missing:
        print(f"Expected reusable workflow(s) not found: {', '.join(missing)}",
              file=sys.stderr)
        return 1

    failed = False

    for path in workflows:
        name = path.relative_to(WORKFLOWS.parent.parent)
        errors = validate(path)

        if errors:
            failed = True
            print(f"FAIL {name}")
            for error in errors:
                print(f"       {error}")
        else:
            print(f"OK   {name}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
