# VideoGeniusAI

Current app version: `V0.0.15`

VideoGeniusAI is a Windows desktop application built in Python that turns a prompt into a structured short-form video project and can render the final MP4 locally.

It uses `LM Studio` as a local OpenAI-compatible text backend and supports three video pipelines:

- `Storyboard local` for fast frame-based previews
- `Local AI video` for real scene clips generated through `ComfyUI` video/gif workflows, narration with `Windows local` or `Piper local`, and final assembly with `FFmpeg`
- `Local Avatar video` for talking-avatar style clips generated through a ComfyUI avatar/lipsync workflow plus a source image and local narration

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
- GPU detection in the GUI with a selectable GPU for auto-launched `ComfyUI` renders
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
- `ComfyUI_EchoMimic` plus `ComfyUI-VideoHelperSuite` in ComfyUI `custom_nodes` if you want `Local Avatar video`
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

The app creates local runtime files such as `config.json`, `log.txt`, `history/`, `output/`, and generated media next to the executable or source tree. `log.txt` rotates automatically and includes timestamps, module names, thread names, and source lines for troubleshooting. Those files are intentionally not tracked in Git.

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

- Requires a ComfyUI workflow that outputs real video or gif assets per scene
- Rejects workflows that only output static images
- Lets you choose which detected GPU to use when VideoGeniusAI opens ComfyUI automatically
- Supports multiple workers across local ports
- Uses `Windows local` narration by default
- Supports `Piper local` narration optionally
- Burns subtitles locally when enabled
- Uses FFmpeg to assemble the final MP4

### Local Avatar video

- Requires a ComfyUI workflow that outputs real video or gif assets
- Requires an avatar source image
- Uses local narration audio per scene for lipsync-style workflows
- Requires the ComfyUI custom nodes exposed as `Echo_LoadModel`, `Echo_Predata`, `Echo_Sampler`, `VHS_LoadAudio`, `VHS_LoadImagePath`, and `VHS_VideoCombine`
- Rejects workflows that only output static images
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

## Release discipline

- Treat each commit pushed to `main` as a release commit with a fresh semantic version bump.
- Keep `videogenius_ai/version.py`, `README.md`, `MANUAL_USUARIO.md`, `CHANGELOG.md`, the Windows executable metadata, and the GitHub tag aligned to the same `Vx.y.z`.
- Prefer a single release-ready commit per push to `main` so the generated GitHub Release, tag, and executable all map to one exact app version.

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
- `README.md`
- `CHANGELOG.md`
- `MANUAL_USUARIO.md`

GitHub Actions workflow:

- runs on every push to `main`
- installs development dependencies
- executes the test suite
- rebuilds `videogeniusAI.exe`
- ensures the matching `Vx.y.z` tag exists
- publishes a GitHub Release with the compiled executable plus Apache 2.0 license files

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
|-- LICENSE
|-- MANUAL_USUARIO.md
|-- NOTICE
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

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
