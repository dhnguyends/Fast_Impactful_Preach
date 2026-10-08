"""Exception hierarchy. Every message is written for the end user (shown in the UI / CLI)."""


class FipError(Exception):
    """Base class for all expected pipeline failures."""

    exit_code = 1


class ConfigError(FipError):
    """Missing / invalid configuration (API key, binaries, options)."""

    exit_code = 2


class MediaError(FipError):
    """Input file unreadable, FFmpeg failure while probing or extracting."""

    exit_code = 3


class TranscriptionError(FipError):
    exit_code = 4


class LLMError(FipError):
    """Claude API failure or unusable answer."""

    exit_code = 5


class RenderError(FipError):
    exit_code = 6


class Cancelled(FipError):
    exit_code = 130
