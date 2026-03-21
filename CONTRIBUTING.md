# Contributing

## Development setup

Create a virtual environment and install the development dependencies:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

## Local development

- Run the desktop app with `pythonw .\videogeniusAI.pyw`.
- Run the test suite with `python -m unittest discover -s tests -v`.
- Build the Windows executable with `powershell -ExecutionPolicy Bypass -File .\build_exe.ps1`.

## Versioning

The project uses semantic versioning with a `Vx.y.z` display format.

- Increment `patch` for fixes, maintenance, and documentation releases.
- Increment `minor` for backward-compatible features.
- Increment `major` for breaking changes.

Use the version helper before creating a release commit:

```powershell
python .\bump_version.py patch --note "Short release note"
```

The helper updates the single source of truth in `videogenius_ai/version.py`, refreshes the README version banner, and updates the changelog and manual version banner.

## Release workflow

Recommended release flow:

1. Update the code and documentation.
2. Run `python .\bump_version.py patch --note "Describe the release"`.
3. Run the test suite.
4. Build `videogeniusAI.exe`.
5. Commit the release changes.
6. Create or push the matching `Vx.y.z` tag if needed.
7. Push to `main`.

On every push to `main`, GitHub Actions verifies that the pushed version changed, rebuilds the executable, ensures the matching `Vx.y.z` tag exists, and publishes a GitHub Release.

Release rule:

- Do not push a commit to `main` without bumping the app version first.
- Keep one version bump per release commit so the app UI, docs, executable metadata, tag, and GitHub Release stay in sync.

## Dependencies policy

- Keep `requirements.txt` limited to runtime dependencies required by end users.
- Keep build-only tooling in `requirements-dev.txt`.
- Pin versions for reproducible builds.

## Local artifacts

`config.json`, `log.txt`, generated media, temporary smoke-test folders, and built executables are local artifacts and should not be committed.
