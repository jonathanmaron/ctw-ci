# Changelog

All notable changes to the workflows in this repository are documented here.

This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html),
interpreted for reusable workflows as:

- **Major** — an input is removed or renamed, an input default changes in a way
  that alters pipeline behavior, or a job is removed or renamed. Consumers have
  to act.
- **Minor** — a new input or job is added, with a default that keeps existing
  pipelines behaving as they did.
- **Patch** — a fix inside an existing job that does not change its interface.

## 1.0.0

First release. `php-library` runs fourteen jobs for a Composer library:
`PHPUnit`, `PHPUnit lowest deps`, `PHPUnit canary`, `PHPStan`, `ECS`, `Rector`,
`Composer validate`, `Composer normalize`, `Composer audit`, `Composer
requirements`, `Composer unused`, `Composer outdated`, `Backward compatibility`
and `Infection`.

Twelve of them run on a push. `Infection` is off by default — it needs PSR-4
autoloaded classes and reports 0% rather than failing on a library it cannot
mutate — and `Composer outdated` runs on the weekly schedule only.

Six jobs run once per entry in `php_versions`. The rest run once, on
`php_version`. See the README for why each is where it is.

### Notes for anyone extending this

Three constraints are GitHub's rather than choices, and they shape the file:

- **Inputs may only be a string, a number or a boolean.** The version inputs are
  therefore JSON strings unwrapped with `fromJSON()`, and `'["8.5"]'` needs its
  quotes. `bin/validate-workflows.py` fails a default here that opens like a
  JSON array and does not parse as one; it cannot check what a consumer passes.
- **A reusable workflow cannot declare its own triggers.** There is no input for
  the paths that make a change worth a run — that filter is written into each
  caller's `on:` block, and `bin/rollout.py` is what writes it.
- **An empty matrix vector is an error, not an empty set of jobs.** `PHPUnit
  canary` therefore carries `if: inputs.php_versions_canary != '[]'`, so the job
  is skipped before the matrix is expanded. Without it, the default of `'[]'`
  would fail every consumer's run.

One cosmetic consequence of the last two: a job skipped by its `if` never has
its `name:` template interpolated, so `Composer outdated` appears in the run as
`Composer outdated [${{ matrix.php }}]` whenever the run is not a scheduled one.
The job behaves correctly; only the label is raw.
