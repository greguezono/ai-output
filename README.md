# ai-output

Interactive report of my GitHub pull-request output before and after adopting AI coding tools (Dec 2025).

Live: https://greguezono.github.io/ai-output/

`index.html` is self-contained and generated from aggregate monthly PR stats. No repository names or PR contents are included.

## Refresh

`scripts/refresh.py` pulls PRs with `gh`, rebuilds `index.html` from `template.html` through the last complete month, and with `--publish` commits and pushes it. Raw pulls are cached in `.cache/` (gitignored).

A launchd job runs it with `--if-stale` at login and hourly. It exits in about 0.1 seconds with no network calls unless a new month has completed, so a Mac that is off on the 1st catches up the next time it is on:

```sh
cp scripts/com.greguezono.ai-output-refresh.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.greguezono.ai-output-refresh.plist
```

Logs: `~/Library/Logs/ai-output-refresh.log`.
