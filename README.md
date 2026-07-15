# youtube-to-obsidian

YouTube video-to-Obsidian workflow with transcript extraction, keyframe
analysis, semantic notes, and optional diagram or email output.

## Runtime

Requires Python, ffmpeg, and the dependencies declared in pyproject.toml.
Transcript, OCR, Whisper, and mail features are separated so the core
workflow can run without private credentials.

Run scripts/install.sh and then scripts/doctor.sh. Downloaded media, cookies,
and generated notes remain outside Git.
