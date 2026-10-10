NAME = "space_fun_fact"
DESCRIPTION = "Provides a random fun fact about space."
TRIGGERS = ["tell me a space fact", "random space fact", "space trivia", "ek space fact batao"]
EXAMPLE = "tell me a space fact"

import random

def run(text, ctx):
    # Return a short space fact regardless of input.
    facts = [
        "A day on Venus is longer than its year.",
        "Neutron stars can spin at a rate of 700 rotations per second.",
        "There are more trees on Earth than stars in the Milky Way.",
        "The footprints on the Moon will stay there for millions of years.",
        "A spoonful of a neutron star would weigh about a billion tons on Earth.",
        "Space is completely silent because there is no medium for sound to travel.",
        "The largest known star, UY Scuti, would engulf the orbit of Saturn if placed in our solar system.",
        "One million Earths could fit inside the Sun.",
        "The observable universe is about 93 billion light‑years across.",
        "Mars has the tallest volcano in the solar system, Olympus Mons."
    ]
    return random.choice(facts)

def self_test():
    # Ensure run returns a non‑empty string.
    result = run(EXAMPLE, None)
    assert isinstance(result, str) and result, "run() must return a non‑empty string"
