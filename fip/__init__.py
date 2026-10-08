"""FastImpact Preach — turn long sermons into impactful vertical shorts.

Pipeline:  long video → audio (FFmpeg) → word-level transcript (Whisper)
           → clip selection (Claude) → 9:16 reframing + animated ASS subtitles (FFmpeg)
"""
__version__ = "2.0.0"
