#!/usr/bin/env bash

# Separate process groups let the watchdog stop a stage and its descendants on both macOS and Linux.
run_bounded() (
	local seconds=$1 pid watchdog status=0
	shift
	set -m
	"$@" </dev/null &
	pid=$!
	(
		sleep "$seconds"
		kill -TERM -- "-$pid" 2>/dev/null || true
	) >/dev/null 2>&1 &
	watchdog=$!
	set +m
	wait "$pid" 2>/dev/null || status=$?
	kill -TERM -- "-$watchdog" 2>/dev/null || true
	wait "$watchdog" 2>/dev/null || true
	return "$status"
)
