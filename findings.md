# Installer validation

On November 19, 2026 (UTC), actionlint 1.7.12 timed out while checking the
three repository workflows on the macOS worker.

```sh
timeout --kill-after=5s 600 actionlint .github/workflows/*.yml
```

The command exited 124 after 600 seconds with no diagnostics. A stack trace from the earlier
run showed actionlint waiting to write ShellCheck input. A local pipe probe
accepted 512 bytes before blocking. Firstmate will rerun workflow lint after
landing.
