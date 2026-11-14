from collections import Counter, defaultdict
from datetime import datetime
from statistics import mean, median
import math

from .validation import require_tables, unique, finite, references
from .mastery import DIFFICULTIES


def validate(data):
    require_tables(data, ["items", "attempts", "skills", "prerequisites"])
    unique(data["items"], ["item_id"])
    unique(data["attempts"], ["attempt_id"])
    references(data["attempts"], "item_id", data["items"], "item_id")
    for item in data["items"]:
        if not item["skills"] or not set(item["skills"]) <= set(data["skills"]):
            raise ValueError("题目知识点无效")
        if item.get("difficulty_hint") not in DIFFICULTIES:
            raise ValueError("题目难度标记无效：" + str(item.get("difficulty_hint")))
        active_from = _parse_day(item.get("active_from"), "题目启用时间")
        retired_at = _parse_day(item.get("retired_at"), "题目退休时间")
        if active_from and retired_at and retired_at <= active_from:
            raise ValueError("题目退休时间不早于启用时间")
    for attempt in data["attempts"]:
        if attempt["result"] not in {"correct", "wrong", "skipped"}:
            raise ValueError("未知作答状态")
        if attempt["observed_at"] < attempt["presented_at"]:
            raise ValueError("作答早于题目分配")
        if attempt.get("selection_reason") not in {"adaptive", "introduction"}:
            raise ValueError("未知分配原因")
    for relation in data["prerequisites"]:
        if relation["before"] not in data["skills"] or relation["after"] not in data["skills"]:
            raise ValueError("先修关系引用未知知识点")
        if relation["before"] == relation["after"]:
            raise ValueError("知识点不能作为自身的先修")
    if _prerequisite_cycle(data["prerequisites"]):
        raise ValueError("先修关系存在循环")


def _parse_day(value, label):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(label + "应为 YYYY-MM-DD 字符串")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as error:
        raise ValueError(label + "格式应为 YYYY-MM-DD") from error


def _prerequisite_cycle(relations):
    graph = defaultdict(list)
    nodes = set()
    for relation in relations:
        graph[relation["before"]].append(relation["after"])
        nodes.add(relation["before"])
        nodes.add(relation["after"])
    state = {}

    def visit(node):
        state[node] = 1
        for nxt in graph.get(node, ()):
            if state.get(nxt) == 1:
                return True
            if state.get(nxt) is None and visit(nxt):
                return True
        state[node] = 2
        return False

    for node in list(nodes):
        if state.get(node) is None and visit(node):
            return True
    return False


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
