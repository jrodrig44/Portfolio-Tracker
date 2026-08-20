import unittest
from unittest.mock import patch

import app


class HistoricalPerformanceRangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.history = [
            {"history_date": "2023-12-31", "portfolio_value": 90.0, "source_type": "stash_statement"},
            {"history_date": "2024-02-29", "portfolio_value": 100.0, "source_type": "stash_statement"},
            {"history_date": "2024-03-31", "portfolio_value": 110.0, "source_type": "stash_statement"},
            {"history_date": "2024-04-15", "portfolio_value": 115.0, "source_type": "live_snapshot"},
            {"history_date": "2024-04-30", "portfolio_value": 118.0, "source_type": "stash_statement"},
            {"history_date": "2024-05-31", "portfolio_value": 120.0, "source_type": "stash_statement"},
        ]

    def test_all_and_default_return_every_point_in_chronological_order(self) -> None:
        unsorted_history = list(reversed(self.history))

        explicit = app._filter_performance_history(unsorted_history, "ALL")
        default = app._filter_performance_history(unsorted_history)

        self.assertEqual(explicit, self.history)
        self.assertEqual(default, self.history)

    def test_one_month_includes_boundary_and_excludes_older_points(self) -> None:
        result = app._filter_performance_history(self.history, "1M")

        self.assertEqual([row["history_date"] for row in result], ["2024-04-30", "2024-05-31"])

    def test_three_months_includes_boundary_and_excludes_older_points(self) -> None:
        result = app._filter_performance_history(self.history, "3M")

        self.assertEqual(
            [row["history_date"] for row in result],
            ["2024-02-29", "2024-03-31", "2024-04-15", "2024-04-30", "2024-05-31"],
        )

    def test_one_year_includes_boundary_and_excludes_older_points(self) -> None:
        history = [
            {"history_date": "2023-02-27", "portfolio_value": 80.0},
            {"history_date": "2023-02-28", "portfolio_value": 90.0},
            {"history_date": "2024-02-29", "portfolio_value": 100.0},
        ]

        result = app._filter_performance_history(history, "1Y")

        self.assertEqual([row["history_date"] for row in result], ["2023-02-28", "2024-02-29"])

    def test_empty_history_returns_safely(self) -> None:
        self.assertEqual(app._filter_performance_history([], "1M"), [])

    def test_mixed_monthly_and_daily_records_are_preserved_without_mutation(self) -> None:
        history = [
            {"history_date": "2024-05-31", "portfolio_value": 120.0, "source_type": "stash_statement"},
            {"history_date": "2024-05-15", "portfolio_value": 118.0, "source_type": "live_snapshot"},
        ]
        original = [dict(row) for row in history]

        result = app._filter_performance_history(history, "1M")

        self.assertEqual(result, list(reversed(history)))
        self.assertEqual(history, original)
        self.assertIsNot(result[0], history[1])

    def test_newest_actual_date_is_the_anchor(self) -> None:
        history = [
            {"history_date": "2020-12-31", "portfolio_value": 80.0},
            {"history_date": "2021-01-31", "portfolio_value": 90.0},
        ]

        result = app._filter_performance_history(history, "1M")

        self.assertEqual(result, history)

    def test_month_end_subtraction_from_march_31_uses_february_month_end(self) -> None:
        history = [
            {"history_date": "2024-02-28", "portfolio_value": 90.0},
            {"history_date": "2024-02-29", "portfolio_value": 95.0},
            {"history_date": "2024-03-31", "portfolio_value": 100.0},
        ]

        result = app._filter_performance_history(history, "1M")

        self.assertEqual([row["history_date"] for row in result], ["2024-02-29", "2024-03-31"])

    def test_invalid_range_raises_clear_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported history range"):
            app._filter_performance_history(self.history, "6M")

    def test_range_selector_options_and_default_are_stable(self) -> None:
        self.assertEqual(app.HISTORY_RANGE_OPTIONS, ("1M", "3M", "1Y", "ALL"))
        self.assertEqual(app.DEFAULT_HISTORY_RANGE, "ALL")
