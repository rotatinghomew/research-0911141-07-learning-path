from collections import Counter, defaultdict
from statistics import mean, median
import math

from .validation import require_tables, unique, finite, references


def validate(data):
    require_tables(data, ["items", "attempts", "skills", "prerequisites"])
    unique(data["items"], ["item_id"])
    unique(data["attempts"], ["attempt_id"])
    references(data["attempts"], "item_id", data["items"], "item_id")
    for item in data["items"]:
        if not item["skills"] or not set(item["skills"]) <= set(data["skills"]):
            raise ValueError("题目知识点无效")
    for attempt in data["attempts"]:
        if attempt["result"] not in {"correct", "wrong", "skipped"}:
            raise ValueError("未知作答状态")
        if attempt["observed_at"] < attempt["presented_at"]:
            raise ValueError("作答早于题目分配")
    for relation in data["prerequisites"]:
        if relation["before"] not in data["skills"] or relation["after"] not in data["skills"]:
            raise ValueError("先修关系引用未知知识点")


def explore(data, selection):
    learner = selection.get("learner", "student_01")
    attempts = sorted([record for record in data["attempts"] if record["learner_id"] == learner], key=lambda record: record["observed_at"])
    if not attempts:
        raise ValueError("此学习者没有作答记录")
    items = {item["item_id"]: item for item in data["items"]}
    counts = {skill: Counter() for skill in data["skills"]}
    for attempt in attempts:
        for skill in items[attempt["item_id"]]["skills"]:
            counts[skill][attempt["result"]] += 1
    rows = []
    for skill, count in counts.items():
        scored = count["correct"] + count["wrong"]
        rows.append({"skill": skill, "correct": count["correct"], "wrong": count["wrong"],
                     "skipped": count["skipped"], "observed_accuracy": count["correct"] / scored if scored else None})
    return {"summary": {"作答次数": len(attempts), "不同题目": len({record["item_id"] for record in attempts}),
                        "跳过": sum(record["result"] == "skipped" for record in attempts)},
            "rows": rows, "series": [{"label": row["skill"], "y": row["observed_accuracy"]} for row in rows],
            "history": attempts, "notes": ["作答正确率不是知识掌握概率，多知识点题会出现在多行。", "跳过不计作答错；同题重做单独记录。"]}
