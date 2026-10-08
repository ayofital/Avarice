"""Scheduling declarations must not spend model tokens or embed private targets."""
import unittest


class CronTests(unittest.TestCase):
    def test_job_definitions_are_script_only_and_target_is_supplied_explicitly(self):
        try:
            from avarice.cron.jobs import get_cron_jobs
        except (AttributeError, ImportError) as exc:
            self.fail(f"Cron definitions must use supported no-agent jobs: {exc}")
        jobs = get_cron_jobs("telegram:1234")
        self.assertEqual(len(jobs), 2)
        self.assertTrue(all(job["no_agent"] for job in jobs))
        self.assertEqual(jobs[0]["deliver"], "local")
        self.assertEqual(jobs[1]["deliver"], "telegram:1234")
        self.assertEqual(jobs[1]["schedule"], "0 8,20 * * *")
        self.assertTrue(all(job["script"].endswith(".py") for job in jobs))
        self.assertNotIn("chat -q", repr(jobs))
