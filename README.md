# VideoGeniusAI

VideoGeniusAI is a Windows desktop application built in Python that turns a prompt into a structured short-form video project and can render the final MP4 locally.

It uses `LM Studio` as a local OpenAI-compatible text backend and supports two video pipelines:

- `Storyboard local` for fast frame-based previews
- `Local AI video` for scene generation through `ComfyUI`, narration with `Windows local` or `Piper local`, and final assembly with `FFmpeg`

## What the program does

The app helps you move from an idea to a production-ready deliverable:

1. Generate a structured JSON project with title, summary, script, scenes, narration, visual prompts, and timing.
2. Review the result in the desktop UI.
3. Export the project to JSON, TXT, or CSV.
4. Render a local MP4 with either storyboard frames or ComfyUI-generated scene assets.

The primary UX goal is a one-click flow for end users: write the prompt, click `Generar video completo`, and let the app prepare the environment automatically.

## Main capabilities

- Modern desktop UI built with `CustomTkinter`
- LM Studio connection testing and model discovery
- Structured JSON generation with retries and validation
- Automatic cleanup of reasoning-style `<think>` sections before JSON parsing
- Automatic one-click preparation for FFmpeg, LM Studio, and ComfyUI when possible
- Safe fallback project generation when LM Studio is unavailable
- Safe fallback to `Storyboard local` when `Local AI video` is not ready in time
- Local setup helpers for LM Studio, ComfyUI Desktop, and FFmpeg
- Automatic ComfyUI port discovery on common local endpoints
- Support for multiple ComfyUI workers across ports
- Optional subtitle burning and local narration
- Persistent local configuration and history next to the app
- Version shown inside the UI with release-aligned `Vx.y.z` format

## Requirements

- Windows
- Python 3.12+
- LM Studio running locally with an OpenAI-compatible server enabled
- FFmpeg available locally for MP4 generation
- ComfyUI running locally if you want `Local AI video`
- Piper installed only if you choose `Piper local`

## Dependencies

Runtime dependencies are pinned in `requirements.txt`:

- `customtkinter`
- `Pillow`
- `requests`

Build dependencies are pinned in `requirements-dev.txt`:

- everything from `requirements.txt`
- `PyInstaller`

## Installation

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

## Run the app

```powershell
pythonw .\videogeniusAI.pyw
```

The app creates local runtime files such as `config.json`, `log.txt`, `history/`, `output/`, and generated media next to the executable or source tree. Those files are intentionally not tracked in Git.

## Quick start

1. Start `LM Studio` and enable the local server.
2. Run `VideoGeniusAI`.
3. Write your prompt in `Project brief`.
4. Choose the generation mode and render backend.
5. Click `Generar video completo`.

If local AI services are missing or still starting up, the app now attempts to prepare them automatically and can still complete the video through a local fallback path instead of failing immediately.

Recommended LM Studio model guidance:

- Prefer chat or instruct models for JSON generation.
- Avoid reasoning-heavy models when strict JSON is required because they may spend too long in internal reasoning or emit non-JSON content first.

## Local video backends

### Storyboard local

- Generates one PNG per scene
- Uses FFmpeg to assemble the final MP4
- Best for fast previews and low-resource workflows

### Local AI video

- Uses ComfyUI to generate a local asset per scene
- Supports multiple workers across local ports
- Uses `Windows local` narration by default
- Supports `Piper local` narration optionally
- Burns subtitles locally when enabled
- Uses FFmpeg to assemble the final MP4

## Development

Run the unit tests:

```powershell
python -m unittest discover -s tests -v
```

Build the Windows executable:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

The build script:

- reads the version from `videogenius_ai/version.py`
- writes the `.exe` into the project root next to `videogeniusAI.pyw`
- uses `videogeniusai.ico` from the project root
- embeds matching Windows executable version metadata

## Versioning and releases

VideoGeniusAI uses semantic versioning with a display format of `Vx.y.z`.

The single source of truth is:

- `videogenius_ai/version.py`

Use the helper to bump versions:

```powershell
python .\bump_version.py patch --note "Describe the release"
```

This updates:

- `videogenius_ai/version.py`
- `CHANGELOG.md`
- `MANUAL_USUARIO.md`

GitHub Actions workflow:

- runs on every push to `main`
- installs development dependencies
- executes the test suite
- rebuilds `videogeniusAI.exe`
- ensures the matching `Vx.y.z` tag exists
- publishes a GitHub Release with the compiled executable

## Project structure

```text
videogeniusAI/
|-- .github/
|   `-- workflows/
|-- tests/
|-- videogenius_ai/
|-- build_exe.ps1
|-- bump_version.py
|-- CONTRIBUTING.md
|-- MANUAL_USUARIO.md
|-- README.md
|-- requirements.txt
|-- requirements-dev.txt
|-- videogeniusAI.pyw
`-- videogeniusai.ico
```

## Documentation

- User guide: [MANUAL_USUARIO.md](MANUAL_USUARIO.md)
- Contribution and release workflow: [CONTRIBUTING.md](CONTRIBUTING.md)
- Change history: [CHANGELOG.md](CHANGELOG.md)

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
