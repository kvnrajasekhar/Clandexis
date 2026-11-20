import subprocess
from pathlib import Path
from typing import Optional
from app.utils.client import get_genai_client

def synthesize_speech(
    text: str,
    output_audio_path: str = "output.aiff",
    voice: Optional[str] = None,
    use_gemini_if_available: bool = False,
) -> str:
    """
    Converts text to speech (TTS).
    
    Supports:
    1. Built-in high-quality native macOS TTS (`say`), saving directly to audio files (.aiff, .wav).
    2. Gemini Omni/Live audio output if available on your account.
    """
    out_path = Path(output_audio_path).resolve()

    if use_gemini_if_available:
        try:
            client = get_genai_client()
            res = client.models.generate_content(
                model="gemini-omni-flash-preview",
                contents=f"Read this text aloud clearly: {text}",
                config={"response_modalities": ["AUDIO"]},
            )
            for part in res.candidates[0].content.parts:
                if hasattr(part, "inline_data") and part.inline_data:
                    out_path.write_bytes(part.inline_data.data)
                    return str(out_path)
        except Exception:
            pass

    cmd = ["say", "-o", str(out_path)]
    if voice:
        cmd.extend(["-v", voice])
    cmd.append(text)

    subprocess.run(cmd, check=True)
    return str(out_path)
