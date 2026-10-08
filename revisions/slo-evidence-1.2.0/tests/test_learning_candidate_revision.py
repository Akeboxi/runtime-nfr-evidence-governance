import unittest
import numpy as np
import pandas as pd
from scripts.run_learning_candidate_revision import fit_candidate


class LearningContracts(unittest.TestCase):
    def test_holdout_outcomes_cannot_change_candidate_predictions(self):
        start=pd.Timestamp("2026-01-01T00:00:00Z");end=start+pd.Timedelta(days=1)
        times=pd.date_range(start,periods=1440,freq="min")
        values=10+np.sin(np.arange(1440)/50)
        first,meta=fit_candidate(values,times,start,end)
        perturbed=values.copy();perturbed[times >= start+(end-start)*.7]=1e6
        second,meta2=fit_candidate(perturbed,times,start,end)
        np.testing.assert_allclose(first[0],second[0],rtol=0,atol=0)
        self.assertEqual(meta["train_end"],meta2["train_end"])
        self.assertLess(pd.Timestamp(meta["train_end"]),pd.Timestamp(meta["holdout_start"]))

    def test_too_short_history_has_explicit_failure(self):
        start=pd.Timestamp("2026-01-01T00:00:00Z");end=start+pd.Timedelta(days=1)
        times=pd.date_range(start,periods=30,freq="min")
        result,meta=fit_candidate(np.ones(30),times,start,end)
        self.assertIsNone(result)
        self.assertEqual(meta["status"],"insufficient_training_or_holdout")


if __name__ == "__main__":unittest.main()
