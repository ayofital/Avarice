"""Portable job declarations. Local deployment supplies the private target."""


def get_cron_jobs(report_target="local"):
    return [
        dict(name="avarice-scan", schedule="every 10m", prompt="Run the deterministic Avarice paper scan.",
             script="avarice_scan.py", no_agent=True, deliver="local", failure_deliver="local"),
        dict(name="avarice-twice-daily-report", schedule="0 8,20 * * *", prompt="Render the persisted Avarice paper report.",
             script="avarice_report.py", no_agent=True, deliver=report_target),
        dict(name="avarice-realtime-alerts", schedule="every 2m", prompt="Check for fresh qualified buy signals and alert.",
             script="avarice_alerts.py", no_agent=True, deliver=report_target, failure_deliver="local"),
    ]


CRON_JOBS = get_cron_jobs()