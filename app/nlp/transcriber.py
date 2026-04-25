"""Voice transcription via Groq Whisper API."""

import logging

from groq import Groq
from starlette.concurrency import run_in_threadpool

from app.config import settings

logger = logging.getLogger(__name__)


class VoiceTranscriber:
    """Transcribes audio using Groq's Whisper model."""

    def __init__(self, api_key: str, model: str) -> None:
        self.client = Groq(api_key=api_key)
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
            text = await run_in_threadpool(
                self._call_whisper, audio_bytes, filename
            )
            logger.info("Transcribed %d bytes → '%s'", len(audio_bytes), text[:80])
            return text.strip()
        except Exception:
            logger.exception("Voice transcription failed for %d bytes", len(audio_bytes))
            return ""

    def _call_whisper(self, audio_bytes: bytes, filename: str) -> str:
        """Synchronous Whisper API call — meant to be run via run_in_threadpool."""
        transcription = self.client.audio.transcriptions.create(
            file=(filename, audio_bytes),
            model=self.model,
            response_format="text",
        )
        return transcription
