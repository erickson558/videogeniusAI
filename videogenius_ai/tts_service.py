from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from shutil import which

CREATE_NO_WINDOW = 0x08000000


@dataclass
class PiperTTSService:
    executable_path: str = ""
    model_path: str = ""

    def _resolve_executable(self) -> str:
        candidate = self.executable_path.strip()
        if candidate:
            path = Path(candidate)
            if not path.exists():
                raise FileNotFoundError(f"Piper executable not found: {path}")
            return str(path)

        located = which("piper") or which("piper.exe")
        if not located:
            raise FileNotFoundError("Piper executable was not found. Configure piper_executable_path or add piper to PATH.")
        return located

    def _resolve_model(self) -> Path:
        path = Path(self.model_path.strip())
        if not path.exists():
            raise FileNotFoundError(f"Piper model not found: {path}")
        return path

    def synthesize(self, text: str, output_path: str | Path) -> Path:
        clean_text = (text or "").strip()
        if not clean_text:
            raise ValueError("Narration text cannot be empty for Piper synthesis.")

        executable = self._resolve_executable()
        model_path = self._resolve_model()
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        command = [
            executable,
            "--model",
            str(model_path),
            "--output_file",
            str(output),
        ]

        subprocess.run(
            command,
            input=clean_text.encode("utf-8"),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
        )
        return output


@dataclass
class WindowsTTSService:
    voice_name: str = ""
    rate: int = 0

    def synthesize(self, text: str, output_path: str | Path) -> Path:
        clean_text = (text or "").strip()
        if not clean_text:
            raise ValueError("Narration text cannot be empty for Windows speech synthesis.")

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            text_path = temp_path / "speech.txt"
            script_path = temp_path / "speak.ps1"
            text_path.write_text(clean_text, encoding="utf-8")

            voice_block = ""
            if self.voice_name.strip():
                voice = self.voice_name.strip().replace("'", "''")
                voice_block = f"try {{ $synth.SelectVoice('{voice}') }} catch {{ }}\n"

            script = (
                "Add-Type -AssemblyName System.Speech\n"
                "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
                f"{voice_block}"
                f"$synth.Rate = {int(self.rate)}\n"
                f"$text = Get-Content -Raw -Path '{text_path.as_posix()}'\n"
                f"$synth.SetOutputToWaveFile('{output.as_posix()}')\n"
                "$synth.Speak($text)\n"
                "$synth.Dispose()\n"
            )
            script_path.write_text(script, encoding="utf-8")

            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script_path),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
            )
        return output
