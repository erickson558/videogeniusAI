# VideoGeniusAI

VideoGeniusAI is a desktop app in Python for turning an idea into a structured video project using LM Studio as a local OpenAI-compatible backend.

## Main features

- Modern desktop UI built with `CustomTkinter`
- Persistent light/dark/system theme with a dedicated toggle in the UI
- Non-blocking generation flow so the UI stays responsive
- LM Studio connection test and model discovery
- Structured JSON generation with retries and validation
- Output modes:
  - Script only
  - Script + visual prompts
  - Full video project
- Project history
- Export to JSON, TXT and CSV
- Local storyboard frame rendering
- Optional MP4 assembly with FFmpeg
- Guided local setup for LM Studio, ComfyUI Desktop, and FFmpeg
- Shared ComfyUI models folder plus automatic `extra_models_config.yaml` wiring
- One-click installation of a recommended base ComfyUI checkpoint
- GPU detection plus multi-worker ComfyUI discovery on common local ports
- Richer video progress feedback with percentage and current render phase
- Startup-safe window restore so the GUI comes back on-screen after monitor/layout changes
- Optional local AI rendering through ComfyUI plus built-in Windows narration or optional Piper narration
- Simplified quick flow with a single end-to-end `Generar video completo` action
- Persistent `config.json`, window position memory and auto-save
- `log.txt` logging with timestamps
- Version visible in the UI

## Project structure

```text
videogeniusAI/
|-- videogeniusAI.pyw
|-- config.json
|-- requirements.txt
|-- build_exe.ps1
|-- videogenius_ai/
|   |-- config.py
|   |-- export_service.py
|   |-- generator_service.py
|   |-- gui.py
|   |-- history_service.py
|   |-- lmstudio_client.py
|   |-- logging_utils.py
|   |-- models.py
|   |-- paths.py
|   |-- setup_manager.py
|   |-- tts_service.py
|   |-- version.py
|   |-- video_render_service.py
|   `-- video_service.py
`-- tests/
```

## Requirements

- Windows
- Python 3.12+
- LM Studio running locally with an OpenAI-compatible server enabled
- The app can now prepare LM Studio, ComfyUI Desktop and FFmpeg automatically on Windows through the guided setup card
- ComfyUI running locally if you want local AI-generated scene visuals
- Piper installed locally only if you explicitly choose `Piper local`

## Dependencies

- `customtkinter` for the desktop interface
- `requests` for LM Studio HTTP calls
- `Pillow` for storyboard image generation
- `PyInstaller` for Windows packaging

## Installation

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Run

Double-click `videogeniusAI.pyw` or run:

```powershell
pythonw .\videogeniusAI.pyw
```

## User manual

Detailed usage instructions are available in [MANUAL_USUARIO.md](MANUAL_USUARIO.md).

## Video backends

The app now supports two final video backends:

- `Storyboard local`: create PNG storyboard frames and assemble them into an MP4 locally with FFmpeg.
- `Local AI video`: generate a local scene asset through ComfyUI for each scene, synthesize narration with `Windows local` by default or `Piper local` optionally, and assemble the final MP4 with FFmpeg. The guided setup now probes common local ComfyUI ports automatically, including Desktop defaults.
- If multiple ComfyUI workers are available on different local ports, the app can distribute scenes across them to reduce render time.

Recommended workflow:

1. Generate the structured project with LM Studio.
2. Review the scenes.
3. Choose the render backend.
4. Click `Generar video final`.

Quick workflow:

1. Write the prompt.
2. Click `Preparar entorno automatico` once.
3. Choose the essentials in `Quick setup`.
4. Click `Generar video completo`.

## Build EXE

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

The EXE is generated in the project root and uses the local `.ico`.

## Release automation

The repository includes a GitHub Actions workflow at `.github/workflows/release.yml`.

On every push to `main`, the workflow:

- installs dependencies
- runs the unit tests
- rebuilds `videogeniusAI.exe`
- creates or updates the Git tag that matches the app version
- publishes a GitHub Release with the compiled EXE attached

## Git / versioning workflow

Each commit should bump the patch version:

```text
0.0.1 -> 0.0.2 -> 0.0.3
```

The version must stay aligned in:

- App UI
- Source code
- Git tag or release notes
- GitHub repository state
- GitHub Release asset metadata

## Example LM Studio prompt contract

The app asks LM Studio to return valid JSON with keys like:

```json
{
  "title": "Video title",
  "summary": "Short summary",
  "general_script": "Global script",
  "structure": "High level structure",
  "scenes": [
    {
      "scene_number": 1,
      "scene_title": "Opening",
      "description": "What happens",
      "visual_description": "Shot description",
      "visual_prompt": "Detailed visual prompt",
      "narration": "Narration text",
      "duration_seconds": 8,
      "transition": "Fade to next scene"
    }
  ]
}
```

## Security notes

- No `shell=True`
- FFmpeg is called with argument lists only
- API keys are optional and never logged
- Config saves use atomic writes
- Output file names are sanitized

## License

Apache License 2.0
