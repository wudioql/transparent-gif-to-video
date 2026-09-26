"""transparent-gif-to-video internals.

Module map (see references/architecture.md):

    common        errors, warnings, tool lookup, colour parsing
    sources       frame + duration extraction from files and directories
    analysis      alpha/edge/bbox statistics and the JSON report
    background    background evidence, ranking, question, preview
    staging       bleed, compositing, PNG staging, CFR expansion, manifest
    timing        durations -> timestamps (CFR grid / VFR fallback)
    encoding      codec policy and ffmpeg command construction
    convert       the convert command
    verification  probe + decode based output checks
    cli           argument parsing and dispatch
"""

from .cli import main

__all__ = ["main"]
