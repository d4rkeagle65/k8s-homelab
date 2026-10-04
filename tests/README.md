# Tests

**7 suites.** Static and fake-based: they touch no external system, need no credentials, and
change nothing. The pre-commit hook runs every suite except the environment ones.

```sh
py -3 tests/run-all-tests.py                          # the commit gate
py -3 tests/run-all-tests.py --name test-doc-style    # one suite, while iterating
py -3 tests/run-all-tests.py --include-environment    # after changing which modules, packages or tools are used
```

On Linux and macOS, `python3` in place of `py -3`.

Suites run in parallel, one child process each, in the Python that runs the runner. Output
stays in file order. `--parallel 1` runs them one at a time, which is the thing to reach for
when a parallel run reports something strange.

**After cloning, turn the hook on** with `git config core.hooksPath .githooks`: git does not copy
it into a clone, and until it is on nothing runs at commit. `test-harness` fails until it is.

**Treat a green run as "nothing regressed", not as "this works."** What a call does against a
real system is outside what a static suite can see; prove that with a real run.

## The suites

| Suite | What it holds |
|---|---|
| `test-harness.py` | The harness itself: `check` compares as strings, the runner's verdict believes the worst of sign-off, FAIL lines and exit code, `import_script` refuses a script that acts when imported, the probe answers in each Python, and every suite follows the conventions below. Also rejects a `test_config.toml` key the harness does not know. |
| `test-script-health.py` | What every script keeps whatever it does: it compiles in each target Python, which has every standard-library module, name and keyword it uses; it runs nothing when imported; it has a docstring and argparse help - and the ratchets on a failed read becoming an answer (`except: pass`, `except: return False`, a bare `except:`, `suppress(Exception)`) and on text opened with no encoding. |
| `test-repo-hygiene.py` | Bytes nobody can see: UTF-16 files, stray control characters, CRLF where the repo is LF, conflict markers. |
| `test-doc-style.py` | Docs say what is true now; `CLAUDE.md` stays rules within budget and says each thing once; suites named in a `CLAUDE.md` exist; this table lists every suite; `QUEUE.md`'s contents match its sections. |
| `test-scripts-pytest.py` | The generator's own pytest suite (`scripts/tests`: the repo's structure and secret-hygiene checks and the generator's unit tests) passes, with each failing test named; a pytest that cannot run or collects nothing is red, never a pass. |
| `test-import-preflight.py` | **Environment, opt-in.** Every module the scripts import is installed and imports, with every name they take from it and every keyword they pass, in each target Python. |
| `test-prerequisites.py` | **Environment, opt-in.** The packages the scripts need - `test_config.toml` `Prerequisites.Packages` plus every script's `# /// script` dependencies - are installed in each target Python at a version they accept, each target Python meets the scripts' `requires-python`, and each native tool in `Prerequisites.Tools` is on PATH, answers within its timeout, prints its version, and meets its minimum. |

## Convention suites and this project's own

Convention suites hold a convention the script has - a config file, an output folder, a
snapshot. Each is adapted to this project through `_project.py` (names, a fake of the external
boundary, the run entry points) and the short ADAPT region at its own top; nothing below that
region is edited per project.

| Suite | What it holds |
|---|---|
<!-- rows for installed convention suites go above this line -->

## Writing a suite

```python
"""What this suite holds."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _testcommon as t

tool = t.import_script(t.project_file("my-tool.py"))
tool.urlopen = lambda *a, **k: (_ for _ in ()).throw(OSError("refused (403)"))   # after the import

t.section("what this section establishes")
t.check("a specific, falsifiable claim", got, want)

t.complete()
```

Then add a row to the table above and raise the count in the header; `test-doc-style` fails
until you do.

| Harness function | Use |
|---|---|
| `check` / `skip` / `complete` | Assertion, a check that could not run here, and the exit convention |
| `section` / `detail` / `advise` | A heading; evidence under a check; what to do about a failure |
| `ratchet` / `scanned` / `budget` | A count that may fall and never rise; a scan's minimum denominator; a fixed limit. All read `test_config.toml` |
| `project_file` / `source` | A tracked file by name; its text with LF line endings |
| `scripts_under_test` / `tracked_files` | The scripts the health checks cover; every file git would commit |
| `import_script` | A script loaded as a fresh module, refused if its top level would act |
| `scratch(name, fresh, shared)` | A scratch folder for this suite run alone, removed when the suite signs off; `shared` is the repo's folder, for what outlives a run |
| `parse` / `imports_of` / `calls_of` / `resolve_call` | A parsed script; what it imports; every call it makes to an import or a builtin, with the keywords it passes |
| `run` / `stop_tree` | A command's exit code and output, stopped with every process it started past a timeout |
| `target_pythons` / `find_python` / `probe` | Each target Python's executable; a compile and import check in a child of one |
| `prerequisites` / `loose_version` | `test_config.toml` Prerequisites, well-formed entries and malformed ones apart; the first version in a line of text |
