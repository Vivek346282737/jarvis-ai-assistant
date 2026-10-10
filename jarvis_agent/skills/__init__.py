"""Skill registry. A skill is a function run(ctx) registered for one or more request kinds. Adding a future capability
(mobile builder, deployment agent, ...) = drop a module here that calls @register(...) - the core never changes."""
SKILLS = {}


def register(*kinds):
    def deco(fn):
        for k in kinds:
            SKILLS[k] = fn
        return fn
    return deco


def load_all():
    from . import project, coding, data, research  # noqa: F401  (importing registers them)
    return SKILLS
