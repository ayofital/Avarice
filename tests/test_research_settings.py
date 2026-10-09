"""Configuration contracts use no network or main-account state."""
import unittest
from avarice.config.settings import AvariceConfig


class ResearchSettingsTests(unittest.TestCase):
    def test_malformed_research_options_and_excessive_safety_age_are_rejected(self):
        cases=[{"research_enabled":0},{"research_enabled":"false"},
               {"safety_max_age_seconds":601},{"safety_max_age_seconds":True},
               {"safety_max_age_seconds":"600"},{"safety_max_age_seconds":float("nan")},
               {"research_horizon_minutes":"60"},{"research_horizon_minutes":False},
               {"research_max_label_delay_minutes":float("inf")},
               {"research_min_samples":True},{"research_min_samples":0},
               {"research_min_validation_days":2.5},{"research_min_validation_days":False}]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                AvariceConfig(**values)

    def test_research_and_risk_defaults_are_explicit_and_disable_is_valid(self):
        settings=AvariceConfig()
        self.assertEqual(settings.research_horizon_minutes,60)
        self.assertEqual(settings.research_max_label_delay_minutes,30)
        self.assertEqual(settings.research_min_samples,20)
        self.assertEqual(settings.research_min_validation_days,3)
        self.assertTrue(settings.research_enabled)
        self.assertEqual(settings.safety_max_age_seconds,600)
        disabled=AvariceConfig(research_enabled=False)
        self.assertFalse(disabled.research_enabled)
        self.assertFalse(disabled.to_dict()["research_enabled"])
