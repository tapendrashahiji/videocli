"""Command modules. Each exposes ``register(app)`` and imports only light modules at top level."""

from videocli.cli.commands import edit, env, perception, probe

COMMAND_MODULES = [env, probe, perception, edit]
