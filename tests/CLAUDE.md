# Writing tests

- **A suite fails when something breaks, not when something is added.** Assert the invariant,
  not a count: "helper called from 2 places" breaks on a legitimate third caller and catches
  nothing; "the logic appears exactly once" is what was meant. Derive the contract from the
  code rather than pinning rendered text.
- **Negative-test every new guard.** Reintroduce the bug, watch it go red, restore it. A guard
  that has never failed has not been shown to work. The fixture is where this goes wrong
  quietly: build it so the guard's absence would change the result, in a
  `t.scratch(name, fresh=True)` folder, since leftovers satisfy "the file exists" while the
  code under test fails.
- **A scanner carries its own self-test and asserts its denominator.** Write the scan as a
  function and feed it code that must be caught and code that must not, on every run; count
  what it read with `t.scanned`. A pattern or filter that stops matching keeps reporting zero
  forever, which reads as a clean tree.
- **Every positive result needs a positive control.** "Nothing was applied" proves little until
  the same harness is shown applying on a false.
- **Never call `t.check` with a deliberate failure**, and never echo a child process's output
  unfiltered: the runner counts FAIL lines as text, so the suite reddens on its own probe.
- **Test the script through `t.import_script`, and replace its boundary on the module it
  returns** (`module.urlopen = fake`), after the import. A function copied into a suite is a
  second copy of the code, and tests the copy.
- **Address files by name** with `t.project_file`, and select files with `t.tracked_files`,
  never a tree walk - a walk also reads what `.gitignore` keeps out.
- **Derive figures; do not type them.** A literal count in a suite is a line someone edits to
  make the run green. Ceilings live in `test_config.toml`, lowered in the commit that earns it.
- **Adapt a convention suite through `_project.py` and its ADAPT region, nothing else.** The
  fake in `_project.py` serves every convention suite at once; a suite with a stub of its own
  drifts from the others. A promise only this script makes gets a suite of its own on that fake.
- **Verify a suite the way the runner runs it**: `py -3 tests/test-<name>.py` on Windows,
  `python3 tests/test-<name>.py` elsewhere - the Python that runs the runner, not another one
  that happens to be on PATH.
