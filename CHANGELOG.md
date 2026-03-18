# Changelog

## V0.0.9 - 2026-03-18

- Added a fully guided ComfyUI model path setup based on a shared `VideoGeniusAI` checkpoints folder plus automatic writing of `extra_models_config.yaml`.
- Added one-click installation of the recommended base checkpoint and a direct button to open the shared ComfyUI models folder.
- Extended automatic setup so it can download the default checkpoint, generate the workflow immediately, and tell the user when ComfyUI only needs a restart to detect the new model.

## V0.0.8 - 2026-03-18

- Improved guided ComfyUI setup by auto-detecting common local ports, especially `http://127.0.0.1:8000` for ComfyUI Desktop and `http://127.0.0.1:8188` for manual/web installs.
- Updated the guided setup UI to persist the detected ComfyUI URL automatically and surface clearer status when the API responds but no visual checkpoint is installed yet.

## V0.0.7 - 2026-03-18

- Fixed window startup instability by preventing transient tiny geometries from being persisted to `config.json`.
- Added startup geometry sanitization and deferred window showing until the final valid size is ready, removing the visible flicker where the GUI opened at `160x160` before resizing.

## V0.0.6 - 2026-03-18

- Added a guided setup flow that can analyze the local environment, install LM Studio, ComfyUI Desktop, and FFmpeg through `winget`, and preconfigure the app for non-technical users.
- Added automatic ComfyUI checkpoint detection plus generation of a ready-to-use default workflow JSON, removing the need to handcraft a workflow path in the common case.
- Added `Windows local` narration as the default text-to-speech option so voiceovers can work without installing Piper.
- Simplified the Local AI configuration area with status feedback, launch buttons for LM Studio and ComfyUI, and Piper fields that only appear when Piper is explicitly selected.

## V0.0.5 - 2026-03-18

- Simplified the main GUI flow around a new `Quick setup` card.
- Added a one-click `Generar video completo` action that generates the project and renders the final video in a single run.
- Reduced clutter by showing the Local AI backend settings only when `Local AI video` is selected.
- Added a new keyboard shortcut and menu entry for the end-to-end video generation flow.

## V0.0.4 - 2026-03-18

- Replaced the HeyGen-oriented direction with a local-only video pipeline.
- Added a configurable `Local AI video` backend based on ComfyUI workflow execution, optional Piper TTS, and FFmpeg scene assembly.
- Updated the GUI to configure ComfyUI, workflow JSON files, local captions, Piper paths, and local backend connectivity checks.
- Added local pipeline tests and refreshed the user manual for the local-only workflow.

## V0.0.3 - 2026-03-18

- Added selectable video render backends: local storyboard MP4, HeyGen Video Agent, and HeyGen Avatar.
- Added HeyGen API integration with connection testing, remote render polling, and automatic MP4 download.
- Extended the GUI with provider selection, aspect ratio controls, captions toggle, HeyGen credential fields, and new status indicators.
- Added unit tests for HeyGen prompt/script generation and config persistence of the new video settings.

## V0.0.2 - 2026-03-18

- Added persistent dark/light/system appearance mode with UI and menu controls.
- Updated the desktop UI palette so dark mode affects the full interface, not only the window shell.
- Added a configuration persistence test for the new appearance setting.
- Added GitHub Actions release automation to test, build, tag, and publish the EXE on pushes to `main`.

## V0.0.1 - 2026-03-18

- Initial public release of VideoGeniusAI.
- Added a modern CustomTkinter desktop UI with persistent configuration.
- Added LM Studio integration, structured JSON parsing, retries, exports, history, and FFmpeg storyboard video generation.
- Added versioning, logging, build script, tests, and GitHub-ready documentation.

