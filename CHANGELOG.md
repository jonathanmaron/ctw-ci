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

## 1.1.1

### Fixed

- **A reporting step that crashed reported success.** `PHPStan`'s `Annotate`
  and the `Report` steps of `ECS` and `Composer dependencies` each ran their
  tool with `|| true`, because finding something is the *next* step's business:
  the reporting run produces the artifact or the annotations, and a plain run
  after it decides the job.

  `|| true` is too broad for that. It flattens 0 (clean), 1 (reported
  something) and every crash — 255 for a PHP fatal, 137 for an out-of-memory
  kill, 139 for a segfault — into success, so a tool that died left a truncated
  file, uploaded it as the report, and showed a green step while doing it.

  All three now end in `|| [ "$?" -le 1 ]`, which tolerates a verdict and
  nothing else.

  This does **not** separate "found something" from "the tool was
  misconfigured": `shipmonk/composer-dependency-analyser` exits 1 for a bad
  config file as well as for a finding, and PHPStan does the same. Only a crash
  is newly caught. The plain run that follows already surfaced any of these in
  the log; what changes is that the step no longer claims success while
  producing nothing.

## 1.1.0

> **On the version.** Replacing two jobs with one, removing a third, and
> dropping eight inputs between them is a major change under the policy above,
> and this ships as a minor deliberately. Every consumer of this workflow is in this fleet and under the
> same ownership, so the migration is a scheduling problem rather than a
> compatibility one.
>
> What that costs is the opt-in. `@v1` is a moving tag resolved when a run
> starts, so this reaches all eighteen consumers on their next run whether or
> not they are ready — which is why the five repositories that pass a removed
> input were changed *before* the tag moved. See "Upgrading" below.

### Changed

- **`Composer requirements` and `Composer unused` are one job, `Composer
  dependencies`**, running `shipmonk/composer-dependency-analyser`.

  The two owned one half each of the same question, and between them missed the
  other half of it entirely:

| Finding                                  | Was                       |
|------------------------------------------|---------------------------|
| Shadow dependency                        | `Composer requirements`   |
| Unused dependency                        | `Composer unused`         |
| Dev dependency in production code        | nothing                   |
| Prod dependency used only in dev paths   | nothing                   |

  The two new rows are the ones a published library feels. A `require-dev`
  package used from `src/` is absent for every consumer, because nobody
  installs somebody else's `require-dev`; a `require` package only the tests
  touch is installed by every consumer for nothing. Neither tool had a concept
  of the split, so neither could see either.

  It is also faster by an order of magnitude — the tool's own benchmark reports
  two seconds against 124 and 72 for the two it replaces, on a codebase of
  15 000 files — and it has no Composer dependencies at all, which makes the
  out-of-tree install into `/tmp/ci-tools` cheaper than it was for either.

  Two things are lost, both worth naming rather than discovering:

  - **No annotations on the diff.** `console` and `junit` are the two formats
    the tool has, so the job uploads JUnit as an artifact, the way `ECS` uploads
    checkstyle. `Composer unused` spoke GitHub's annotation format and put its
    findings on the diff.
  - **No unknown-*constant* error.** It reports unknown classes and unknown
    functions; a constant reached through an undeclared dependency is the one
    thing `composer-require-checker` caught that this does not.

  `ctw/ctw-qa` 6.3.4 requires the same tool, so `composer qa` runs it locally
  and the job is no longer the first place a finding appears.

### Removed

- **`composer_requirements` and `composer_unused`**, replaced by the single
  `composer_dependencies`. Same default, `true`.

- **`composer_requirements_symbol_whitelist`**, together with the sixty lines
  of shell and `jq` that generated a config from `source_dir`, merged it into a
  committed one, and uploaded `build/composer-requirements-config.json`. The
  case it existed for cannot arise: it silenced a library's own namespaced
  functions called unqualified, which the require checker reported as unknown
  global symbols, and this tool does not record unqualified names in the
  current namespace at all.

- **`composer_requirements_config`**. Exclusions live in a
  `composer-dependency-analyser.php` the library commits beside its
  `composer.json`; the tool loads that name from the project root by itself, so
  nothing points at it and no input has to exist for it. It is `.php`, so the
  default path filter already covers it.

- **The `Infection` job, and the `infection`, `infection_min_msi` and
  `infection_min_covered_msi` inputs.** Mutation testing leaves the workflow.

  The job was off by default and no library in the fleet ever switched it on,
  so what goes is a hundred lines that never ran: the out-of-tree install of
  `infection/infection`, the `XDEBUG_MODE: coverage` override, the search for a
  committed `infection.json5` or `infection.json` and the generated fallback
  when there was neither, the two optional thresholds built up as arguments
  because an empty one had to mean "omit the flag", and the
  `build/infection-text.log` artifact.

  Nothing replaces it. A library that wants mutation testing defines the job in
  its own `ci.yml`; the definition this release deletes is in the `v1.0.0` tag,
  as is the README section explaining when Infection is worth running.

- **`source_dir`.** Its two readers were the require checker symbol whitelist
  and the generated Infection configuration, and both are gone. `Composer
  dependencies` takes its paths from the `autoload` and `autoload-dev` sections
  of `composer.json`, so nothing reads the input at all — and
  `bin/validate-workflows.py` fails a workflow that declares an input it never
  references, so leaving it behind was not an option.

### Upgrading

There is no pin to bump, and that is the thing to plan around: every consumer
pins `@v1`, which resolves when a run starts, so moving the tag hands the new
job to all eighteen at once. GitHub rejects an input the workflow does not
declare — the whole call fails to start, and no job reports at all — so a
library still passing a removed one loses its entire run rather than quietly
losing the setting. The five below were landed before the tag moved.

| Repository                                    | Change                                                             |
|-----------------------------------------------|--------------------------------------------------------------------|
| `ctw-qa`                                      | Deleted `composer_requirements: false`; the new job passes         |
| `ctw-skeleton`                                | Deleted `composer_requirements: false` and `composer-unused.php`   |
| `ctw-composer-plugin-composerlenientplugin`   | Traded the JSON whitelist for a `composer-dependency-analyser.php` |
| `ctw-middleware-htmlminifier`                 | Traded the JSON whitelist for a `composer-dependency-analyser.php` |
| `ctw-middleware-httpexception`                | Traded the JSON whitelist for a `composer-dependency-analyser.php` |

`ctw-qa` switched `Composer requirements` off because the checker reported some
fifty ECS fixer and PHPStan extension classes as unknown symbols, and
whitelisting a list that grows with every config change would have failed the
job on ordinary work. That is now handled where it belongs: `ctw/ctw-qa` ships
`DefaultIgnoredUnknownClassPatterns` and `DefaultIgnoredPackageErrors`, which
exclude the two bundled namespaces by pattern and the PHPStan packages by name,
so the job runs with everything else still guarded.

`ctw-skeleton` needed `composer-unused.php` to stop `php` being reported as an
unused requirement, its one placeholder class referencing no core symbol. The
tool does not consider `php` a dependency at all — it looks at
`vendor/name` packages and `ext-*` — so the file goes with no replacement.

The other thirteen pass no removed input and report nothing new. That is a
quieter landing than this change usually gets, and it is because
`ctw/ctw-qa` 6.3.4 had already put the same tool in `composer qa`: the findings
were dealt with locally before this release existed.

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
