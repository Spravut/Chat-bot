# Run the JMeter load test via Docker — no local JMeter install required.
#
# Usage (from project root):
#   .\loadtest\run-docker.ps1
#
# Prerequisites:
#   - docker-compose up -d  (the bot must be reachable inside its network)
#
# Output:
#   loadtest/results.jtl      raw JMeter log
#   loadtest/report/          HTML report with percentile graphs

$ErrorActionPreference = "Stop"

# Detect the compose network name (depends on project directory name).
$network = (docker network ls --filter name=_default --format "{{.Name}}" | Select-String "chat-bot").ToString()
if (-not $network) {
    Write-Error "Could not find docker-compose network. Is 'docker-compose up' running?"
    exit 1
}
Write-Host "Using docker network: $network" -ForegroundColor Cyan

# 1. Run the load test
Write-Host "`nStarting JMeter load test (~90s)..." -ForegroundColor Cyan
docker run --rm `
    --network $network `
    -v "${PWD}\loadtest:/tests" `
    justb4/jmeter `
    -n -t /tests/dating_bot_load.jmx `
    -l /tests/results.jtl `
    -JHOST=bot `
    -JPORT=9100

# 2. Generate the HTML report
Write-Host "`nGenerating HTML report..." -ForegroundColor Cyan
# Wipe old report directory if any — JMeter refuses to write into a non-empty dir.
if (Test-Path "loadtest\report") { Remove-Item -Recurse -Force "loadtest\report" }

docker run --rm `
    -v "${PWD}\loadtest:/tests" `
    justb4/jmeter `
    -g /tests/results.jtl `
    -o /tests/report

Write-Host "`nDone. Open loadtest\report\index.html to view results." -ForegroundColor Green
