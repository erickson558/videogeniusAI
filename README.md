# VideoGeniusAI

VideoGeniusAI is a desktop app in Python for turning an idea into a structured video project using LM Studio as a local OpenAI-compatible backend.

## Main features

- Modern desktop UI built with `CustomTkinter`
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
|   |-- version.py
|   `-- video_service.py
`-- tests/
```

## Requirements

- Windows
- Python 3.12+
- LM Studio running locally with an OpenAI-compatible server enabled
- FFmpeg in `PATH` if you want MP4 output

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

## Build EXE

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

The EXE is generated in the project root and uses the local `.ico`.

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

