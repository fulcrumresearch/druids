"""Music-themed slug generation for tasks and executions."""

from __future__ import annotations

import random


ADJECTIVES = [
    # calm
    "gentle",
    "soft",
    "calm",
    "quiet",
    "mellow",
    "serene",
    "tender",
    "still",
    "hushed",
    "placid",
    # ethereal
    "silver",
    "velvet",
    "cosmic",
    "ancient",
    "distant",
    "hazy",
    "fading",
    "misty",
    "lunar",
    "spectral",
    "floating",
    "crystal",
    "phantom",
    "gossamer",
    # melancholic
    "dark",
    "lonely",
    "somber",
    "wistful",
    "dusky",
    "twilight",
    "hollow",
    "ashen",
    "muted",
    # warm
    "warm",
    "golden",
    "bright",
    "radiant",
    "amber",
    "sunny",
    "copper",
    "honey",
    "rosy",
    # energetic
    "electric",
    "neon",
    "crisp",
    "vivid",
    "sparkling",
    "blazing",
    "keen",
    "brisk",
    "snappy",
    # intense
    "swift",
    "bold",
    "wild",
    "fierce",
    "soaring",
    "restless",
    "urgent",
    "burning",
    "storming",
    "iron",
    "stark",
    # natural
    "mossy",
    "frosted",
    "windswept",
    "sandy",
    "cedar",
    "ivory",
    "cobalt",
    "slate",
    "coral",
    "flint",
    # temporal
    "early",
    "late",
    "fleeting",
    "lasting",
    "sudden",
    "steady",
    "brief",
    "endless",
]

PIECES = [
    # dance
    "waltz",
    "minuet",
    "rondo",
    "polonaise",
    "bolero",
    "polka",
    "tango",
    "mazurka",
    "sarabande",
    "pavane",
    "gigue",
    # character pieces
    "nocturne",
    "prelude",
    "etude",
    "serenade",
    "impromptu",
    "intermezzo",
    "bagatelle",
    "ballade",
    "caprice",
    "romance",
    "elegy",
    "scherzo",
    "berceuse",
    "aubade",
    "barcarolle",
    "pastorale",
    "humoresque",
    # vocal/choral
    "aria",
    "cantata",
    "requiem",
    "motet",
    "madrigal",
    "lied",
    "anthem",
    "chorale",
    "hymn",
    "canticle",
    # sonata forms
    "sonata",
    "concerto",
    "symphony",
    "sonatina",
    "quartet",
    "quintet",
    "trio",
    "sextet",
    "octet",
    # free/virtuosic
    "fantasia",
    "rhapsody",
    "cadenza",
    "toccata",
    "variations",
    "divertimento",
    # orchestral
    "overture",
    "suite",
    "march",
    "fanfare",
    "sinfonia",
    # contrapuntal
    "fugue",
    "canon",
    "invention",
    "passacaglia",
    "chaconne",
    "ricercar",
]

# 90 adjectives × 63 pieces = 5,670 combinations
# With hex suffix fallback, effectively unlimited


def generate_task_slug() -> str:
    """Generate a music-themed task slug like "gentle-nocturne"."""
    adjective = random.choice(ADJECTIVES)
    piece = random.choice(PIECES)
    return f"{adjective}-{piece}"
