"""Machine / Image backends. Import from the concrete backend modules.

- :mod:`ramure.machines.base` — abstract ``Machine`` / ``Image`` + ``SSHCredentials``.
- :mod:`ramure.machines.local` — ``LocalMachine`` / ``LocalImage``.
- :mod:`ramure.machines.docker` — ``DockerMachine`` / ``DockerImage`` (uses the Docker CLI).
- :mod:`ramure.machines.morph` — ``MorphMachine`` / ``MorphImage`` (needs ``morphcloud``).
"""
