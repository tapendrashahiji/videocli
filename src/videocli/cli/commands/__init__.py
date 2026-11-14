"""Command modules. Each exposes ``register(app)`` and imports only light modules at top level."""

from videocli.cli.commands import env, probe

COMMAND_MODULES = [env, probe]
