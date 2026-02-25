"""Music-themed slug generation for tasks and executions."""

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
    # melancholic
    "dark",
    "lonely",
    "somber",
    "wistful",
    "dusky",
    "twilight",
    # warm
    "warm",
    "golden",
    "bright",
    "radiant",
    "amber",
    "sunny",
    # energetic
    "electric",
    "neon",
    "crisp",
    "vivid",
    "sparkling",
    "blazing",
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
    # vocal/choral
    "aria",
    "cantata",
    "requiem",
    "motet",
    "madrigal",
    "lied",
    "anthem",
    # sonata forms
    "sonata",
    "concerto",
    "symphony",
    "sonatina",
    "quartet",
    "quintet",
    "trio",
    # free/virtuosic
    "fantasia",
    "rhapsody",
    "cadenza",
    "toccata",
    "variations",
    # orchestral
    "overture",
    "suite",
    "march",
    # contrapuntal
    "fugue",
    "canon",
    "invention",
]


def generate_task_slug() -> str:
    """Generate a music-themed task slug like "gentle-nocturne"."""
    adjective = random.choice(ADJECTIVES)
    piece = random.choice(PIECES)
    return f"{adjective}-{piece}"


def generate_execution_slug(task_slug: str, program_name: str) -> str:
    """Generate an execution slug like "gentle-nocturne-claude"."""
    return f"{task_slug}-{program_name}"
