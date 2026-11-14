"""Command modules. Each exposes ``register(app)`` and imports only light modules at top level."""

from videocli.cli.commands import agent, colour, compositing, deep, edit, env, eyes, gfx, perception, probe

COMMAND_MODULES = [env, probe, perception, deep, edit, compositing, colour, gfx, eyes, agent]
