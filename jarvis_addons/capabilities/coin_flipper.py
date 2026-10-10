NAME = "coin_flipper"
DESCRIPTION = "Flips a virtual coin and returns heads or tails."
TRIGGERS = ["flip a coin", "toss a coin", "flip coin", "coin toss karo"]
EXAMPLE = "flip a coin"

import random

def run(text, ctx):
    result = random.choice(["Heads", "Tails"])
    return f"It landed on {result}."

def self_test():
    res = run(EXAMPLE, None)
    assert isinstance(res, str)
    assert "Heads" in res or "Tails" in res
