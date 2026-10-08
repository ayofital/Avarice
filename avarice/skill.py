"""Compatibility helper; Hermes skills use SKILL.md, not a Python Skill API."""
from avarice.cli import main


def run(args):
    return main(args)