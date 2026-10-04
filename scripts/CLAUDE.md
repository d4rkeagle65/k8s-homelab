# Working in scripts/

`backup.py` is the capture-and-generate tool, and this is its only copy; `README.md` here
explains the files it owns and the recipes. `tests/` here is pytest's suite, which
`tests/test-scripts-pytest.py` at the repo root runs for the commit gate; tests that need
capture's local files (`kubernetes/.local/`, `docs/`) skip themselves without them.

- **Run the tool from the repo root.** `--output` defaults to the current
  directory; from inside `scripts/` or above the repo, the tool refuses to run.
