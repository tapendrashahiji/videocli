"""Command modules. Each exposes ``register(app)`` and imports only light modules at top level."""

from videocli.cli.commands import agent, colour, compositing, edit, env, eyes, perception, probe

COMMAND_MODULES = [env, probe, perception, edit, compositing, colour, eyes, agent]
