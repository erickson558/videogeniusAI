# Changelog

## V0.1.6 - 2026-03-24

- Harden startup import bootstrap against stale Python bytecode caches.

## V0.1.5 - 2026-03-24

- Disabled burned captions automatically for silent briefs and prevented render services from falling back to scene descriptions as subtitle text.
- Updated silent-brief status messaging and added regression coverage for caption-free silent renders in storyboard and Local AI pipelines.

## V0.1.4 - 2026-03-24

- Fixed startup config loading so the app ignores unexpected or legacy JSON keys instead of crashing during AppConfig initialization.
- Added regression coverage for resilient config loading across mixed-version config files.

## V0.1.3 - 2026-03-24

- Fixed fallback brief parsing so Spanish and English prompts generate videos around the requested theme instead of literal command phrases.
- Localized Spanish fallback copy and preserved silent briefs without narration in local fallback generation.
- Rewrote normalized config files on load so the persisted app version stays aligned with the running release.

## V0.1.2 - 2026-03-24

- Fixed silent-video briefs so narration is removed from generated scenes and TTS is disabled automatically for non-avatar renders.
- Added regression coverage for no-narration intent detection and normalization.

## V0.1.1 - 2026-03-24

- Added GPU-aware FFmpeg render selection with automatic CPU fallback on hardware encode failure.
- Surfaced detected GPUs and active encoder in the desktop UI with persisted render preferences.
- Stabilized storyboard and Local AI video rendering behind shared renderer abstractions and regression tests.

## V0.1.0 - 2026-03-21

- Added cinematic shot planning and richer AI scene direction for faceless video generation.
- Storyboard local now renders multi-shot cinematic sequences and can use ComfyUI image workflows for AI-enhanced visuals.
- Improved local AI prompt quality with stronger cinematic prompts and negative prompt defaults.

## V0.0.15 - 2026-03-21

- Fixed silent storyboard MP4 generation by routing legacy storyboard builds through the narrated render pipeline.
- Added Local Avatar workflow support, ComfyUI avatar setup guidance, and release/version consistency checks for pushes to main.
- Prepared ComfyUI Desktop custom node links for EchoMimic and VideoHelperSuite in the local Windows setup.
- Hardened the Windows build script to compile with a temporary work directory outside OneDrive-locked build folders and aligned release assets with Apache 2.0 documentation.

## V0.0.14 - 2026-03-19

- Added GUI GPU detection and selection for auto-launched ComfyUI renders.
- Improved rotating logging with module, thread, and task lifecycle context.
- Added tests for GPU selection and logging configuration.

## V0.0.13 - 2026-03-19

- Added one-click full video preparation with automatic LM Studio and ComfyUI startup attempts.
- Added safe local fallbacks so full video generation can continue when LM Studio or ComfyUI are unavailable.
- Updated the documentation to describe the zero-touch flow for end users.

## V0.0.12 - 2026-03-19

- Hardened LM Studio JSON generation and timeout guidance for local reasoning models.
- Standardized release versioning, GitHub tagging, and Windows EXE metadata under Vx.y.z.
- Improved README, manual, contribution guide, and separated runtime from build dependencies.

## V0.0.11 - 2026-03-18

- Hardened startup so the main window is restored on-screen even when a saved geometry would place it outside the current monitor layout.
- Reworked the initial window show sequence to avoid hidden-start issues on `.pyw` and packaged `.exe` launches.
- Fixed shutdown cleanup by cancelling pending Tk scheduled jobs before destroying the app, preventing Tcl errors during quick closes.

## V0.0.10 - 2026-03-18

- Added GPU detection and surfaced multi-GPU guidance directly in the guided setup summary.
- Added ComfyUI worker URL discovery and configuration so the app can distribute scene rendering across multiple local ComfyUI instances.
- Improved the video creation progress experience with an explicit percentage indicator plus current render phase details in the status strip.

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

