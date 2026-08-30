# ctw-ci

Reusable GitHub Actions workflows for the `ctw` libraries, so a pipeline is
defined once here instead of copied into every repository.

| Workflow      | Purpose                                                                                          |
|---------------|--------------------------------------------------------------------------------------------------|
| `php-library` | Full pipeline for a Composer library: tests, static analysis, coding standards, package hygiene. |

## `php-library`

### Usage

A library's entire `.github/workflows/ci.yml` becomes a trigger block and a
call:

```yaml
name: CI

on:
    push:
        branches: [master]
        tags: ['**']
    pull_request:
        paths:
            - '**/*.php'
            - '**/*.phtml'
            - 'composer.json'
            - 'phpunit.xml.dist'
            - '.github/workflows/**'
    schedule:
        - cron: '17 4 * * 1'
    workflow_dispatch:

concurrency:
    group: ${{ github.workflow }}-${{ github.ref }}
    cancel-in-progress: true

permissions:
    contents: read

jobs:
    php-library:
        uses: jonathanmaron/ctw-ci/.github/workflows/php-library.yml@v1
        with:
            php_version: '8.5'
            php_versions: '["8.5"]'
```

`bin/rollout.py` writes exactly that file, so there is no need to type it. See
[Rollout](#rollout).

Every job is on by default. Switch off what a library does not need with the
boolean inputs rather than by redefining jobs locally:

```yaml
        with:
            php_versions: '["8.4", "8.5", "8.6"]'
            php_version: '8.4'
            rector: false
```

### The three JSON inputs

`php_versions`, `php_versions_canary` and any other list have to be **JSON
strings**, because a reusable workflow input may only be a string, a number or
a boolean. The workflow unwraps them with `fromJSON()`.

```yaml
            php_versions: '["8.5", "8.6"]'      # correct
            php_versions: ["8.5", "8.6"]        # rejected: not a string
            php_versions: '[8.5, 8.6]'          # wrong: 8.5 is a number, and
                                                # the matrix would render 8.5
                                                # as the string "8.5" anyway
```

`bin/validate-workflows.py` fails the build if a default in this repository
opens like a JSON array and does not parse as one. It cannot check what a
consumer passes, so mind the quotes.

### Jobs

| Job                      | Runs                           | Notes                                                                           |
|--------------------------|--------------------------------|---------------------------------------------------------------------------------|
| `PHPUnit`                | once per `php_versions`        | Coverage, JUnit and Cobertura reports, uploaded as an artifact.                 |
| `PHPUnit lowest deps`    | once per `php_versions`        | `--prefer-lowest --prefer-stable`, guards the lower bounds in `composer.json`.  |
| `PHPUnit canary`         | once per `php_versions_canary` | The suite on a version being tried out. `continue-on-error`. None by default.   |
| `PHPStan`                | once per `php_versions`        | Annotations on the diff, plus a grouped summary artifact.                       |
| `ECS`                    | once per `php_versions`        | Checkstyle artifact — ECS ships no GitHub formatter.                            |
| `Rector`                 | once per `php_versions`        | Dry run.                                                                        |
| `Composer validate`      | once, on `php_version`         | No install at all, so it is the fastest signal.                                 |
| `Composer normalize`     | once, on `php_version`         | `composer.json` is normalized. Reports a diff, never writes.                    |
| `Composer audit`         | once, on `php_version`         | Security advisories against the graph that job resolved.                        |
| `Composer requirements`  | once, on `php_version`         | Every used symbol is covered by a declared dependency.                          |
| `Composer unused`        | once, on `php_version`         | Every declared dependency is used. Annotations on the diff.                     |
| `Composer outdated`      | once per `php_versions`        | Scheduled runs only. `continue-on-error`.                                       |
| `Backward compatibility` | once, on `php_version`         | API break against the last tagged minor.                                        |
| `Infection`              | once, on `php_version`         | Mutation testing. **Off by default**, and not right for every library.          |

The QA tools are split into separate jobs rather than run through `composer qa`,
so all of them report in parallel instead of the pipeline stopping at whichever
one fails first. Nothing declares `needs:`, so every job starts at once.

Every job opens by printing `php --version`, so a log says which PHP produced it
without cross-referencing the job name — which matters most for the six matrix
jobs.

### Which jobs run on every PHP version

Six jobs run once per entry in `php_versions`: `PHPUnit`, `PHPUnit lowest deps`,
`PHPStan`, `ECS`, `Rector` and `Composer outdated`. GitHub renders the version
in the job name, so they appear as `ECS [8.5]`, `ECS [8.6]` and so on.

The reason is that `composer.lock` is not committed, so every job runs
`composer update` afresh and the 8.5 and 8.6 jobs can resolve a different
dependency graph. Anything reading that graph can legitimately reach a different
verdict per PHP version. For the two PHPUnit jobs that is the entire point.
Three others look like they are driven purely by their config file, and none of
them is:

- **PHPStan** resolves its analysis target in this order: `phpVersion` in
  `phpstan.neon`, then `config.platform.php` in `composer.json`, then the
  running binary. The `ctw` libraries set neither of the first two, so PHPStan
  analyzes against whatever PHP the job runs on. A deprecation introduced in 8.6
  is reported by the 8.6 job only.
- **Rector** does pin a level set through `ctw/ctw-qa`'s `DefaultSets` — but the
  same array also contains `PHPUnitSetList::COMPOSER_BASED`. That set registers
  its rules through `ruleWithConfigurationComposerVersionBound()`, which
  activates each one only if the **installed** `phpunit/phpunit` satisfies a
  version constraint, read out of `vendor/composer/installed.json` rather than
  out of `rector.php`. Rector also falls back to the running PHP version when
  `composer.json` declares neither `require.php` nor `config.platform.php`.
- **ECS** wraps PHP-CS-Fixer and PHP_CodeSniffer, both of which tokenize with
  the native `token_get_all()` rather than an emulative lexer, so identical
  source is not guaranteed to tokenize identically on two PHP versions.

Running any of those once would assert a determinism the pipeline does not have.
If you would rather have the determinism than the coverage, the way to get it is
to commit a lock file and set `config.platform.php` — which pins Rector's
composer-bound rules and PHPStan's `phpVersion` at the same time — not to drop
the matrix.

Eight jobs stay on `php_version` alone:

- **`Composer validate`** installs nothing, so there is no resolved graph to
  differ. It is also the fastest signal in the run.
- **`Composer normalize`** reads `composer.json` and nothing else, so it has no
  resolved graph either. It also has to run on a PHP its own tool supports:
  version 2.52 declares nothing above `~8.5.0`.
- **`Composer audit`**, **`Composer requirements`** and **`Composer unused`** do
  read the resolved graph, so in principle they could differ per version. They
  are off the matrix by choice: in practice an advisory, a missing `ext-*`
  requirement or an unused package found on 8.5 is the same one found on 8.6,
  and the pipeline already pays for six matrix jobs.
- **`Backward compatibility`** compares two API surfaces, and that comparison
  does not change with the PHP running it. It also has to run on a PHP its own
  tool supports, which is a narrower set.
- **`Infection`** is the slowest thing in the pipeline, and a mutant that
  survives on one PHP version survives on the others.

### When a pipeline runs

Triggers cannot be shared. A reusable workflow may not declare its own `on:`, so
each caller writes out the paths that make a change worth a run. The policy
`bin/rollout.py` writes:

- **Pull requests are path-filtered.** That is where the volume is. A pull
  request touching none of the listed paths starts no run at all — and GitHub,
  like most forges, treats *no run* as *not succeeded*, so either leave
  **Require status checks to pass** off, or add the paths such a pull request
  touches.
- **Pushes to the default branch and tags always run**, filtered by nothing.
  `paths:` applies to a whole `push` event and cannot be narrowed to branches,
  so rather than let it swallow a release tag it is left off the push trigger
  entirely. A merge to the default branch is post-merge verification; a tag is a
  release.
- **Scheduled runs always run.** A schedule reads the dependency graph rather
  than the diff, which is what makes it a drift detector, and it is the only
  thing that makes `Composer outdated` run at all.

The default path list is the source the jobs read plus the files that change
what they do:

```yaml
- '**/*.php'
- '**/*.phtml'
- 'composer.json'
- 'phpunit.xml.dist'
- '.github/workflows/**'
```

`composer.json` earns its place several times over: `Composer validate`,
`Composer normalize`, `Composer audit`, `Composer requirements` and `Composer
unused` all read it, and a constraint bump has to be tested even when no source
file moves. `.github/workflows/**` is there so that changing the `@v1` pin runs
the pipeline. `ecs.php`, `rector.php` and `composer-unused.php` need no entry of
their own — they are `.php`. A library whose suite reads fixtures of another kind
adds them.

**One deliberate divergence** from a pipeline that runs on every push: a branch
pushed with no pull request open gets no run. GitHub cannot express "push,
unless a pull request is open for this ref" without duplicating every run, so
the branch gets its pipeline when the pull request opens. The **Run workflow**
button on the Actions tab covers the ad-hoc case before then.

### Inputs

#### Versions

| Input                 | Type   | Default            | Description                                                                   |
|-----------------------|--------|--------------------|-------------------------------------------------------------------------------|
| `php_version`         | string | `'8.5'`            | PHP for the eight jobs that are off the matrix.                               |
| `php_versions`        | string | `'["8.5", "8.6"]'` | JSON array of every PHP version the library is tested against.                |
| `php_versions_canary` | string | `'[]'`             | JSON array of versions tried out rather than supported. Non-blocking.         |

The canary is **currently off in every `ctw` library**, and `bin/rollout.py`
writes the input commented out rather than omitting it. 8.6 could not be
installed at all wherever a library reaches Laminas —
`laminas/laminas-diactoros` 3.8.0 declares
`php ~8.2.0 || ~8.3.0 || ~8.4.0 || ~8.5.0` — so the job failed at
`composer update` and reported nothing about the library it was testing.
Uncomment it once the ecosystem allows 8.6.
| `source_dir`          | string | `'src'`            | Library source directory. Read by the require checker whitelist and Infection. |

Keep `php_version` at the **lowest** PHP version the library supports.
`Backward compatibility` and `Composer normalize` both have to run on a PHP their
own tool supports, and both tools lag the newest release.

#### System packages

| Input               | Type   | Default | Description                                                               |
|---------------------|--------|---------|---------------------------------------------------------------------------|
| `apt_packages`      | string | `''`    | Extra Debian packages for every job that resolves dependencies.           |
| `apt_packages_test` | string | `''`    | Extra Debian packages for the PHPUnit jobs only, on top of the above.     |

A library whose tests shell out to a binary names the packages here and the jobs
install them before resolving dependencies:

```yaml
            apt_packages_test: 'mono-runtime mono-utils mono-devel'
```

Both are space-separated lists installed with `apt-get install -y
--no-install-recommends`. Both are empty by default, and a job with nothing to
install fetches no apt index at all.

Prefer `apt_packages_test`. It covers the usual case, where only the test suite
needs the binary, and leaves PHPStan, ECS, Rector and the package hygiene jobs
running as fast as they did.

#### Job toggles

Every job is on by default except `Infection`. Switching one off renders it as a
skipped row rather than removing it from the run; either way it does not
execute.

| Input                    | Job                      | Default |
|--------------------------|--------------------------|---------|
| `phpunit`                | `PHPUnit`                | `true`  |
| `phpunit_lowest_deps`    | `PHPUnit lowest deps`    | `true`  |
| `phpstan`                | `PHPStan`                | `true`  |
| `ecs`                    | `ECS`                    | `true`  |
| `rector`                 | `Rector`                 | `true`  |
| `composer_validate`      | `Composer validate`      | `true`  |
| `composer_normalize`     | `Composer normalize`     | `true`  |
| `composer_audit`         | `Composer audit`         | `true`  |
| `composer_requirements`  | `Composer requirements`  | `true`  |
| `composer_unused`        | `Composer unused`        | `true`  |
| `composer_outdated`      | `Composer outdated`      | `true`  |
| `backward_compatibility` | `Backward compatibility` | `true`  |
| `infection`              | `Infection`              | `false` |

`backward_compatibility` needs a tagged release to compare against and is wrong
for a `0.x` library, where SemVer permits a break in a minor. Neither case fails
a run on its own — an untagged repository passes without checking anything — but
a `0.x` library should switch it off, as should the pull request that makes a
deliberate break.

#### Behavior

| Input                                    | Type    | Default | Description                                                                               |
|------------------------------------------|---------|---------|-------------------------------------------------------------------------------------------|
| `composer_requirements_symbol_whitelist` | boolean | `false` | Generate a symbol whitelist from `source_dir` and pass it to composer-require-checker.    |
| `composer_requirements_config`           | string  | `''`    | Path to a composer-require-checker config the library commits, for optional dependencies. |
| `infection_min_msi`                      | string  | `''`    | Minimum Mutation Score Indicator, as a percentage. Empty enforces nothing.                |
| `infection_min_covered_msi`              | string  | `''`    | Minimum MSI counting only covered mutants. Empty enforces nothing.                        |

`composer_requirements_symbol_whitelist` is only for libraries that declare
namespaced functions or constants and call them unqualified — the checker reports
those as unknown global symbols. The generated whitelist replaces the checker's
own default configuration, so leave it off for ordinary class-based libraries.

`composer_requirements_config` is for the other case the checker cannot model: a
**genuinely optional** dependency. The checker has no concept of one, so a symbol
reached only through a helper the consumer may never use is still reported as
unknown. The tempting fix — promoting the package to a hard `require` — makes
every consumer install it, which is wrong when only a handful of source files
touch it. Setting both merges the generated symbols into the committed file's
`symbol-whitelist`, so neither cancels the other out.

Adopt the Infection thresholds empty. The job then reports the score and passes
whatever it is; read a few runs, then set a floor you are already above.

### Reports

GitHub has no single report widget, so each job publishes what its tool can
produce:

| Job                     | Where the result shows up                                          |
|-------------------------|--------------------------------------------------------------------|
| `PHPUnit`               | `Total coverage: NN.NN%` in the run summary; HTML, Cobertura and JUnit in an artifact. |
| `PHPStan`               | Inline annotations on the diff; grouped summary in an artifact.     |
| `ECS`                   | Checkstyle XML in an artifact.                                      |
| `Composer unused`       | Inline annotations on the diff.                                     |
| `Infection`             | Inline annotations on the diff; text log in an artifact.            |
| `Composer requirements` | Console output, plus the generated config when one was generated.   |
| everything else         | Console output.                                                     |

Artifacts are kept for seven days.

ECS is the one tool with no annotation route. `console`, `checkstyle`, `junit`,
`json` and `gitlab` are its whole set of formatters, so checkstyle to an
artifact is the best available.

### Out-of-tree tools

Five jobs need a tool that must not join the library's dependency graph, because
it would change the very resolution they are checking. Each installs its tool
into a throwaway Composer project under `/tmp/ci-tools`:

| Job                      | Tool                                        |
|--------------------------|---------------------------------------------|
| `Composer normalize`     | `ergebnis/composer-normalize:^2.52`         |
| `Composer requirements`  | `maglnet/composer-require-checker:^4.24`    |
| `Composer unused`        | `icanhazstring/composer-unused:^0.9.6`      |
| `Backward compatibility` | `roave/backward-compatibility-check:^8.21`  |
| `Infection`              | `infection/infection:^0.35`                 |

The throwaway project sets `allow-plugins true`, which is safe there in a way it
would not be in the library's own tree: it holds one tool and nothing else. A
tool shipped as a Composer plugin installs but never activates unless it is
allowed, and a plugin-optional one says so in a warning rather than an error, so
its command would simply not exist.

**A config file these tools read cannot assume the library's autoloader.** The
tool runs from `/tmp/ci-tools`, so its process knows nothing of the library's
namespaces — while the same tool run from the library's own `vendor/bin` knows
them all. A `composer-unused.php` or `composer-require-checker.json` that names
a class from the library it configures therefore works locally and fatals in
CI, which is the worst shape a difference can take.

`ctw/ctw-qa` hit exactly this: its `composer-unused.php` filters using its own
`Ctw\Qa\…` classes. The fix belongs in the config file rather than in this
workflow —

```php
require_once __DIR__ . '/vendor/autoload.php';
```

— a no-op when the tool runs in tree, and the whole fix when it does not.
Composer appends its autoloader rather than prepending it, so the tool's own
classes keep priority and nothing it has already loaded is displaced.

## Rollout

`bin/rollout.py` writes `.github/workflows/ci.yml` into the libraries cloned
beside this repository. Dry run by default:

```bash
python3 bin/rollout.py                            # plan for every sibling
python3 bin/rollout.py --only ctw-http            # plan for one
python3 bin/rollout.py --only ctw-http --apply    # write and commit
python3 bin/rollout.py --apply --push             # everything that is ready
```

It refuses a repository that is missing `phpunit.xml.dist` or any of the
`test`, `phpstan`, `ecs` and `rector` Composer scripts, that has a dirty working
tree, or that is already on the shared workflow. It warns about a `ctw/ctw-qa`
older than 6, a committed `composer.lock`, and a `composer.json` allowing a PHP
older than the pipeline covers.

A package whose `type` is not `library` is skipped entirely — `ctw-composer-plugin-composerlenientplugin`
is a `composer-plugin` and needs a pipeline of its own.

Work in batches. These repositories have only ever run a single test job, so the
first run of the full pipeline is as likely to surface a genuine static analysis
or package hygiene failure as it is to surface a CI problem.

## Validation

`bin/validate-workflows.py` runs in this repository's own pipeline, because
GitHub only reports a broken reusable workflow when a consumer calls it — a bad
push here would otherwise surface as a failing run in somebody else's
repository. It checks that the file parses, that no mapping key is defined
twice, that every input declares a type and a description, that every
`inputs.NAME` reference resolves and every declared input is used, that a
JSON-array default really parses, that every job declares `runs-on`, that a job
reading `matrix.NAME` declares that dimension, and that no step's script assigns
over an environment variable it reads.

`actionlint` runs alongside it and covers what the Python cannot: expression
syntax, unknown contexts, bad `uses:` references, and — through `shellcheck` —
the `run:` scripts themselves.

```bash
pip install pyyaml
python3 bin/validate-workflows.py

bash <(curl -sSL https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash)
./actionlint
```

**Install `shellcheck` before trusting a local `actionlint` run.** It is a
separate binary that `actionlint` shells out to, and it is absent from a plain
workstation and present on a GitHub runner — so without it a local run passes
and the same commit fails in CI. Everything under a `run:` goes unchecked until
it is installed.

The two shell idioms it objects to here are deliberate and carry a
`# shellcheck disable` with the reason: `set -- $APT_PACKAGES` wants the word
splitting that turns a space-separated input into positional parameters
(SC2086), and the coverage one-liner's `$m` belongs to PHP rather than to the
shell (SC2016).

## Versioning

Consumers pin `@v1`. The tag moves as releases land, so a library picks up
fixes without touching its `ci.yml`.

- **Major** — an input is removed or renamed, an input default changes in a way
  that alters pipeline behavior, or a job is removed or renamed. Consumers have
  to act, and `v2` is cut rather than `v1` moved.
- **Minor** — a new input or job is added, with a default that keeps existing
  pipelines behaving as they did.
- **Patch** — a fix inside an existing job that does not change its interface.

Cutting a release moves the major tag onto the new commit:

```bash
git tag -a v1.1.0 -m 'See CHANGELOG.md'
git tag -f -a v1 -m 'Moved to v1.1.0'
git push origin v1.1.0
git push origin v1 --force
```

Pinning a moving tag is the GitHub Actions convention, and it is what makes a
fix here reach eighteen repositories at once. It also means a bad push reaches
them just as fast, which is what the validation jobs are for.
