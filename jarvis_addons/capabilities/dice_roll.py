import random
NAME = "dice_roll"
DESCRIPTION = "Rolls a six-sided dice and says the number."
TRIGGERS = ["roll a dice", "roll the dice", "dice roll", "pasa phenko"]
EXAMPLE = "roll a dice"

def run(text, ctx):
    return "You rolled a %d." % random.randint(1, 6)

def self_test():
    for _ in range(20):
        assert run(EXAMPLE, None) in ["You rolled a %d." % n for n in range(1, 7)]
