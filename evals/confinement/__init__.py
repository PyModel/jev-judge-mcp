"""The confinement boundary for live agent eval runs (ADR-0074).

`broker` is the credential-owning sidecar process, `shim` the agent container's HTTP→Unix-socket
forwarder, `launch` the host-side orchestration (`run_confined` mirrors `evals.agent.run_agent`).
`protocol` is the framed JSON both container-side modules share; it is stdlib-only, and so are the
two processes, because they run from plain Python images with these files mounted in.
"""
