"""Voice transcription via OpenRouter's audio transcriptions API."""

import logging

from openai import AsyncOpenAI

from app.nlp.parser import AIUnavailableError, retry_ai_call

logger = logging.getLogger(__name__)

TRANSCRIBE_TIMEOUT_SECONDS = 60.0


class VoiceTranscriber:
    """Transcribes audio using an OpenRouter STT model."""

    def __init__(self, api_key: str, model: str) -> None:
        # max_retries=0: retry_ai_call owns retries (the SDK's own would multiply them)
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            timeout=TRANSCRIBE_TIMEOUT_SECONDS,
            max_retries=0,
        )
        self.model = model

    async def transcribe(
        self, audio_bytes: bytes, filename: str = "voice.ogg"
    ) -> str:
        """Transcribe audio bytes to text.

        Args:
            audio_bytes: Raw audio file bytes (OGG, MP3, WAV, etc.).
            filename: Filename hint for the API (determines format).

        Returns:
            Transcribed text string, or empty string on failure.
        """
        try:
            text = await retry_ai_call(
                lambda: self._call_transcribe_async(audio_bytes, filename),
                label="Transcription",
            )
            logger.info("Transcribed %d bytes of audio", len(audio_bytes))
            return text.strip()
        except AIUnavailableError:
            raise
        except Exception:
            logger.exception("Voice transcription failed for %d bytes", len(audio_bytes))
            return ""

    async def _call_transcribe_async(self, audio_bytes: bytes, filename: str) -> str:
        """Async transcription call (JSON response — text/srt/vtt formats are rejected)."""
        transcription = await self.client.audio.transcriptions.create(
            file=(filename, audio_bytes),
            model=self.model,
        )
        return transcription.text
