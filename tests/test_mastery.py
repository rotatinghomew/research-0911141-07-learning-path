"""掌握判断、练习建议、策略调整与历史回测的口径测试。"""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json
import unittest

from learning_path.domain import validate
from learning_path.mastery import (MODEL_VERSION, backtest, mastery_view,
                                   normalize_policy, effective_item_pool,
                                   beta_quantile, beta_incomplete)
from learning_path.server import make_server

ROOT = Path(__file__).resolve().parents[1]


def load():
    return json.loads((ROOT / "fixtures" / "data.json").read_text())


def minimal_data():
    return {
        "skills": ["a", "b"],
        "items": [
            {"item_id": "easy_a", "skills": ["a"], "active_from": "2026-01-01",
             "retired_at": None, "difficulty_hint": "easy"},
            {"item_id": "adv_a", "skills": ["a"], "active_from": "2026-01-01",
             "retired_at": None, "difficulty_hint": "advanced"},
            {"item_id": "shared_ab", "skills": ["a", "b"], "active_from": "2026-01-01",
             "retired_at": None, "difficulty_hint": "mixed"},
            {"item_id": "easy_b", "skills": ["b"], "active_from": "2026-01-01",
             "retired_at": "2026-03-01", "difficulty_hint": "easy"},
            {"item_id": "mix_b", "skills": ["b"], "active_from": "2026-01-01",
             "retired_at": None, "difficulty_hint": "mixed"},
        ],
        "attempts": [],
        "prerequisites": [{"before": "a", "after": "b"}],
    }


def attempt(identifier, learner, item, day, result, reason="adaptive"):
    return {"attempt_id": identifier, "learner_id": learner, "item_id": item,
            "presented_at": "2026-02-%02dT08:00:00Z" % day,
            "observed_at": "2026-02-%02dT08:12:00Z" % day,
            "result": result, "selection_reason": reason}


class BetaTests(unittest.TestCase):
    def test_quantile_inverts_cdf(self):
        for a, b, q in ((2, 3, 0.5), (1, 1, 0.9), (5, 2, 0.25), (10, 10, 0.7)):
            x = beta_quantile(a, b, q)
            self.assertAlmostEqual(beta_incomplete(a, b, x), q, delta=1e-4)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.data = load()

    def test_default_policy_matches_dataset(self):
        policy = normalize_policy(self.data)
        self.assertEqual(policy["prerequisites"], self.data["prerequisites"])
        self.assertEqual(policy["excluded_items"], [])
        self.assertEqual(policy["as_of"], "2026-02-22")

    def test_cycle_and_self_reference_rejected(self):
        with self.assertRaises(ValueError):
            normalize_policy(self.data, {"prerequisites": [
                {"before": "measurement", "after": "statistics"},
                {"before": "statistics", "after": "measurement"}]})
        with self.assertRaises(ValueError):
            normalize_policy(self.data, {"prerequisites": [
                {"before": "measurement", "after": "measurement"}]})

    def test_unknown_skill_and_item_rejected(self):
        with self.assertRaises(ValueError):
            normalize_policy(self.data, {"prerequisites": [{"before": "nope", "after": "statistics"}]})
        with self.assertRaises(ValueError):
            normalize_policy(self.data, {"excluded_items": ["ghost_item"]})

    def test_exclusion_and_retirement_shrink_pool(self):
        full = {item["item_id"] for item in effective_item_pool(self.data, normalize_policy(self.data))}
        excluded = effective_item_pool(self.data, normalize_policy(self.data, {"excluded_items": ["item_01"]}))
        self.assertNotIn("item_01", {item["item_id"] for item in excluded})
        cutoff = effective_item_pool(self.data, {"as_of": "2026-03-02"})
        self.assertNotIn("item_00", {item["item_id"] for item in cutoff})
        self.assertIn("item_01", full)


class EvidenceTests(unittest.TestCase):
    def test_easy_streak_alone_does_not_master(self):
        data = minimal_data()
        data["attempts"] = [attempt("e%d" % i, "s1", "easy_a", i + 1, "correct") for i in range(4)]
        view = mastery_view(data, "s1")
        row = next(row for row in view["skill_rows"] if row["skill"] == "a")
        self.assertNotEqual(row["label"], "mastered")
        self.assertTrue(any("简单题" in reason for reason in row["uncertainties"]))

    def test_non_easy_correct_can_master_with_interval(self):
        data = minimal_data()
        data["attempts"] = [attempt("e%d" % i, "s1", "adv_a", i + 1, "correct") for i in range(6)]
        view = mastery_view(data, "s1")
        row = next(row for row in view["skill_rows"] if row["skill"] == "a")
        self.assertEqual(row["label"], "mastered")
        self.assertGreaterEqual(row["credible_low"], 0.65)
        self.assertTrue(row["difficulty_evidence"]["advanced"]["correct"] >= 6)

    def test_skipped_is_not_wrong(self):
        data = minimal_data()
        data["attempts"] = [attempt("e0", "s1", "easy_a", 1, "skipped"),
                            attempt("e1", "s1", "easy_a", 2, "correct")]
        row = next(row for row in mastery_view(data, "s1")["skill_rows"] if row["skill"] == "a")
        self.assertEqual(row["wrong"], 0)
        self.assertEqual(row["skipped"], 1)
        self.assertEqual(row["correct"], 1)

    def test_repeated_item_is_downweighted(self):
        data = minimal_data()
        data["attempts"] = [attempt("e0", "s1", "adv_a", 1, "wrong"),
                            attempt("e1", "s1", "adv_a", 2, "wrong")]
        view = mastery_view(data, "s1")
        row = next(row for row in view["skill_rows"] if row["skill"] == "a")
        self.assertEqual(row["repeat_attempts"], 1)
        first = next(ref for ref in row["evidence"] if ref["attempt_id"] == "e0")
        second = next(ref for ref in row["evidence"] if ref["attempt_id"] == "e1")
        self.assertAlmostEqual(second["weight"], first["weight"] * 0.5, places=3)

    def test_multi_skill_splits_weight_and_flags_attribution(self):
        data = minimal_data()
        data["attempts"] = [attempt("e0", "s1", "shared_ab", 1, "correct")]
        view = mastery_view(data, "s1")
        for skill in ("a", "b"):
            row = next(row for row in view["skill_rows"] if row["skill"] == skill)
            self.assertAlmostEqual(row["evidence_weight"], 0.5)
            self.assertIsNotNone(row["shared_weight_fraction"])

    def test_unseen_skill_is_unknown_not_failed(self):
        data = minimal_data()
        data["attempts"] = [attempt("e0", "s1", "easy_a", 1, "correct")]
        row_b = next(row for row in mastery_view(data, "s1")["skill_rows"] if row["skill"] == "b")
        self.assertEqual(row_b["label"], "unknown")
        self.assertTrue(any("未被分配" in reason for reason in row_b["uncertainties"]))

    def test_cutoff_excludes_later_answers(self):
        data = minimal_data()
        data["attempts"] = [attempt("e0", "s1", "easy_a", 1, "wrong"),
                            attempt("e1", "s1", "adv_a", 10, "correct")]
        early = mastery_view(data, "s1", {"as_of": "2026-02-05"})
        full = mastery_view(data, "s1")
        early_row = next(row for row in early["skill_rows"] if row["skill"] == "a")
        full_row = next(row for row in full["skill_rows"] if row["skill"] == "a")
        self.assertEqual(early_row["scored_attempts"], 1)
        self.assertEqual(full_row["scored_attempts"], 2)
        self.assertLess(early_row["posterior_mean"], full_row["posterior_mean"])

    def test_view_does_not_mutate_data(self):
        data = minimal_data()
        data["attempts"] = [attempt("e0", "s1", "easy_a", 1, "correct")]
        snapshot = deepcopy(data)
        mastery_view(data, "s1")
        self.assertEqual(data, snapshot)


class RecommendationTests(unittest.TestCase):
    def test_prerequisite_redirects_to_unmastered_base_skill(self):
        data = minimal_data()
        # b 有失败记录，先修 a 没有掌握证据，建议应先补 a
        data["attempts"] = [attempt("e0", "s1", "easy_b", 1, "wrong")]
        rec = mastery_view(data, "s1")["recommendation"]
        self.assertEqual(rec["target_skill"], "a")
        self.assertIn("a", rec["prerequisite_chain"])

    def test_excluded_pool_is_explained_when_empty(self):
        data = minimal_data()
        data["attempts"] = [attempt("e0", "s1", "easy_a", 1, "wrong")]
        view = mastery_view(data, "s1", {"excluded_items": ["easy_a", "adv_a", "shared_ab"]})
        rec = view["recommendation"]
        self.assertIsNone(rec["item_id"])
        self.assertIn("题池", rec["reason"])

    def test_seen_item_deprioritized_and_explained(self):
        data = minimal_data()
        data["attempts"] = [attempt("e0", "s1", "easy_a", 1, "wrong")]
        rec = mastery_view(data, "s1")["recommendation"]
        self.assertNotEqual(rec["item_id"], "easy_a")
        self.assertTrue(any(item["item_id"] == "easy_a" for item in rec["rejected"]))

    def test_predicted_outcome_sums_to_one(self):
        data = minimal_data()
        data["attempts"] = [attempt("e0", "s1", "easy_a", 1, "correct")]
        probs = mastery_view(data, "s1")["recommendation"]["predicted_outcome"]
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=3)
        json.dumps(probs, allow_nan=False)

    def test_recommendation_carries_evidence_and_uncertainty(self):
        data = minimal_data()
        data["attempts"] = [attempt("e0", "s1", "easy_b", 1, "wrong")]
        rec = mastery_view(data, "s1")["recommendation"]
        self.assertTrue(rec["evidence_basis"])
        self.assertTrue(rec["uncertainties"])

    def test_all_mastered_returns_hold_advice(self):
        data = minimal_data()
        data["attempts"] = [attempt("a%d" % i, "s1", "adv_a", i + 1, "correct") for i in range(6)]
        data["attempts"] += [attempt("b%d" % i, "s1", "mix_b", i + 8, "correct") for i in range(8)]
        view = mastery_view(data, "s1")
        rec = view["recommendation"]
        labels = {row["skill"]: row["label"] for row in view["skill_rows"]}
        self.assertEqual(labels, {"a": "mastered", "b": "mastered"})
        self.assertIsNone(rec["item_id"])


class BacktestTests(unittest.TestCase):
    def setUp(self):
        self.data = load()

    def test_both_modes_have_no_leakage_and_match_record_count(self):
        for mode in ("leave_one_student_out", "incremental_known_learners"):
            report = backtest(self.data, mode=mode)
            self.assertEqual(report["leakage_check"]["violations"], 0)
            self.assertEqual(report["leakage_check"]["checked_records"], len(self.data["attempts"]))
            self.assertEqual(report["overall"]["model"]["records"], len(self.data["attempts"]))
            json.dumps(report, allow_nan=False)

    def test_modes_are_kept_separate(self):
        loso = backtest(self.data, mode="leave_one_student_out")
        inc = backtest(self.data, mode="incremental_known_learners")
        self.assertNotEqual(loso["mode"], inc["mode"])
        self.assertIn("cold_start", loso)
        self.assertEqual(set(loso["per_learner"]), set(inc["per_learner"]))

    def test_baseline_is_reported_alongside_model(self):
        report = backtest(self.data)
        for section in ("overall", "cold_start"):
            self.assertIn("population_baseline", report[section])
        self.assertIsNotNone(report["overall"]["population_baseline"]["brier"])

    def test_first_attempt_is_marked_cold_start_once_per_learner(self):
        report = backtest(self.data)
        learners = {a["learner_id"] for a in self.data["attempts"]}
        self.assertEqual(report["cold_start"]["records"], len(learners))

    def test_later_attempt_uses_only_earlier_evidence(self):
        data = minimal_data()
        data["attempts"] = [attempt("e0", "s1", "easy_a", 1, "correct"),
                            attempt("e1", "s2", "easy_a", 2, "correct"),
                            attempt("e2", "s1", "adv_a", 3, "wrong")]
        report = backtest(data, mode="leave_one_student_out")
        self.assertEqual(report["leakage_check"]["violations"], 0)
        # 直接验证 e2 预测点只能看到 e0（s1 自己的更早记录），看不到 e1 之后的任何答卷
        from learning_path.mastery import _skill_states, normalize_policy
        policy = normalize_policy(data)
        states, events = _skill_states(data, "s1", policy, cutoff="2026-02-03T08:00:00Z")
        self.assertEqual([event["attempt_id"] for event in events], ["e0"])
        states_after, events_after = _skill_states(data, "s1", policy)
        self.assertEqual(len(events_after), 2)

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            backtest(self.data, mode="tomorrow")


class ValidationTests(unittest.TestCase):
    def test_bad_difficulty_rejected(self):
        data = minimal_data()
        data["items"][0]["difficulty_hint"] = "legendary"
        with self.assertRaises(ValueError):
            validate(data)

    def test_bad_dates_rejected(self):
        data = minimal_data()
        data["items"][0]["retired_at"] = "2025-12-31"
        with self.assertRaises(ValueError):
            validate(data)

    def test_fixture_still_valid(self):
        validate(load())


class HttpAdviceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.server = make_server(ROOT, self.temporary.name, port=0)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.temporary.cleanup()

    def post(self, path, payload):
        request = Request(self.base + path, data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
        return urlopen(request)

    def test_mastery_backtest_and_published_advice_roundtrip(self):
        with self.post("/api/mastery", {"learner": "student_01"}) as response:
            mastery = json.load(response)
        self.assertEqual(mastery["view"]["model_version"], MODEL_VERSION)
        with self.post("/api/backtest", {"mode": "leave_one_student_out"}) as response:
            report = json.load(response)
        self.assertEqual(report["report"]["leakage_check"]["violations"], 0)
        with self.post("/api/advice", {"title": "给学生01的建议", "learner": "student_01"}) as response:
            published = json.load(response)
            self.assertEqual(response.status, 201)
        with urlopen(self.base + "/api/advice") as response:
            catalog = json.load(response)
        self.assertTrue(any(item["id"] == published["id"] for item in catalog))
        with urlopen(self.base + "/api/advice/" + str(published["id"])) as response:
            reopened = json.load(response)
        self.assertEqual(reopened["payload"], published["payload"])

    def test_policy_changes_recommendation_and_is_validated(self):
        baseline = json.load(self.post("/api/mastery", {"learner": "student_01"}))
        changed = json.load(self.post("/api/mastery", {
            "learner": "student_01",
            "policy": {"excluded_items": [baseline["view"]["recommendation"]["item_id"]]}}))
        self.assertNotEqual(changed["view"]["recommendation"]["item_id"],
                            baseline["view"]["recommendation"]["item_id"])
        with self.assertRaises(HTTPError) as caught:
            self.post("/api/mastery", {"learner": "student_01",
                                       "policy": {"excluded_items": ["ghost"]}})
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()

    def test_published_advice_survives_new_dataset(self):
        published = json.load(self.post("/api/advice", {"title": "冻结建议", "learner": "student_02"}))
        data = load()
        data["context_note"] = "后续补充"
        self.post("/api/datasets", {"label": "新数据", "data": data}).close()
        reopened = json.load(urlopen(self.base + "/api/advice/" + str(published["id"])))
        self.assertEqual(reopened["payload"], published["payload"])
        self.assertEqual(reopened["revision"], published["revision"])

    def test_non_integer_identifier_is_400_not_500(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + "/api/advice/abc")
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()

    def test_published_advice_survives_server_restart(self):
        from learning_path.service import Workbench
        published = json.load(self.post("/api/advice", {"title": "重启保留", "learner": "student_04"}))
        restarted = Workbench(ROOT, self.temporary.name)
        reopened = restarted.advice(published["id"])
        self.assertEqual(reopened["payload"], published["payload"])
        self.assertTrue(any(item["id"] == published["id"]
                            for item in restarted.store.advice_publications()))


if __name__ == "__main__":
    unittest.main()
