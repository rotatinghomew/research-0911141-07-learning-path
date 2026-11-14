"""按知识点的掌握追踪、练习建议与历史回测。

只使用 Python 标准库。所有口径都显式区分：

* 难度：简单题答对的证据强度低于综合题，但简单题答错仍是强负信号；
* 多知识点：一次作答在多个知识点间均分权重，并标注归属不唯一；
* 重复尝试：同一题目的第二次及以后作答降权；
* 跳过：不计入错误，只作为单独的跳过倾向证据；
* 未分配：学生没有做过的题目不产生任何证据，不能解释为答错。

模型对每个知识点维护 Beta 后验，输出概率与可信区间而不是无法校验的总分。
"""
from collections import defaultdict
from datetime import date, datetime
import math

MODEL_VERSION = "beta-bernoulli-skill-trace/v1"

DIFFICULTIES = ("easy", "mixed", "advanced")
DIFFICULTY_WEIGHT = {"easy": 0.7, "mixed": 1.0, "advanced": 1.3}
# 对数几率上的难度校准：简单题更容易答对，综合题更难。
DIFFICULTY_LOGIT_OFFSET = {"easy": 0.6, "mixed": 0.0, "advanced": -0.6}
REPEAT_FACTOR = 0.5
PRIOR_STRENGTH_CAP = 8.0
MASTERY_LOWER_BOUND = 0.65
PRACTICE_UPPER_BOUND = 0.60
CREDIBLE_LEVEL = 0.80
SKIP_RATE_FLOOR = 0.02
SKIP_RATE_CAP = 0.60


# ---------------------------------------------------------------------------
# Beta 分布：不完整 Beta 函数与分位数（Numerical Recipes 公共算法）
# ---------------------------------------------------------------------------

def _beta_cf(a, b, x):
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-12:
            break
    return h


def beta_incomplete(a, b, x):
    """正则化不完整 Beta 函数 I_x(a,b)。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_beta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(log_beta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_cf(a, b, x) / a
    return 1.0 - front * _beta_cf(b, a, 1.0 - x) / b


def beta_quantile(a, b, q):
    """二分法求 Beta 分位数，区间端点用闭式边界处理。"""
    if q <= 0.0:
        return 0.0
    if q >= 1.0:
        return 1.0
    low, high = 1e-9, 1.0 - 1e-9
    for _ in range(60):
        middle = (low + high) / 2.0
        if beta_incomplete(a, b, middle) < q:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


# ---------------------------------------------------------------------------
# 策略（助教可在页面调整：先修关系、可用题池、截止日期）
# ---------------------------------------------------------------------------

def _parse_day(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("日期应为 YYYY-MM-DD 字符串")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as error:
        raise ValueError("日期格式应为 YYYY-MM-DD") from error


def _has_cycle(pairs):
    graph = defaultdict(list)
    nodes = set()
    for pair in pairs:
        graph[pair["before"]].append(pair["after"])
        nodes.add(pair["before"])
        nodes.add(pair["after"])
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


def default_policy(data):
    return {"prerequisites": [dict(pair) for pair in data["prerequisites"]],
            "excluded_items": [], "as_of": None}


def normalize_policy(data, policy=None):
    """校验并规范化策略；策略是查询参数，不回写源数据。"""
    policy = policy or {}
    if not isinstance(policy, dict):
        raise ValueError("策略必须是对象")
    skills = set(data["skills"])
    item_ids = {item["item_id"] for item in data["items"]}

    raw_prerequisites = policy.get("prerequisites", data["prerequisites"])
    if not isinstance(raw_prerequisites, list):
        raise ValueError("先修条件必须是数组")
    pairs = []
    seen = set()
    for pair in raw_prerequisites:
        if not isinstance(pair, dict) or "before" not in pair or "after" not in pair:
            raise ValueError("先修关系需要 before 与 after 两个知识点")
        before, after = pair["before"], pair["after"]
        if before not in skills or after not in skills:
            raise ValueError("先修关系引用了未知知识点")
        if before == after:
            raise ValueError("知识点不能作为自身的先修")
        if (before, after) in seen:
            raise ValueError("先修关系重复：%s -> %s" % (before, after))
        seen.add((before, after))
        pairs.append({"before": before, "after": after})
    if _has_cycle(pairs):
        raise ValueError("先修关系存在循环，无法确定学习顺序")

    excluded = policy.get("excluded_items", [])
    if not isinstance(excluded, list) or not all(isinstance(value, str) for value in excluded):
        raise ValueError("排除题池必须是题目编号数组")
    unknown = sorted(set(excluded) - item_ids)
    if unknown:
        raise ValueError("题池排除引用了未知题目：" + ",".join(unknown))

    as_of = _parse_day(policy.get("as_of"))
    if as_of is None:
        as_of = max(datetime.fromisoformat(a["presented_at"].replace("Z", "+00:00")).date()
                    for a in data["attempts"]) if data["attempts"] else date.today()
    return {"prerequisites": pairs, "excluded_items": sorted(set(excluded)),
            "as_of": as_of.isoformat()}


def effective_item_pool(data, policy):
    """返回截止日可用且未被策略排除的题目。"""
    as_of = _parse_day(normalize_policy(data, policy)["as_of"])
    excluded = set(policy.get("excluded_items", []))
    pool = []
    for item in data["items"]:
        if item["item_id"] in excluded:
            continue
        active_from = _parse_day(item["active_from"])
        retired_at = _parse_day(item.get("retired_at"))
        if active_from and active_from > as_of:
            continue
        if retired_at and retired_at <= as_of:
            continue
        pool.append(item)
    return pool


# ---------------------------------------------------------------------------
# 事件流与证据加权
# ---------------------------------------------------------------------------

def _join_events(data, learner=None, cutoff=None):
    items = {item["item_id"]: item for item in data["items"]}
    events = []
    for attempt in data["attempts"]:
        if learner is not None and attempt["learner_id"] != learner:
            continue
        if cutoff is not None and attempt["observed_at"] >= cutoff:
            continue
        item = items[attempt["item_id"]]
        events.append({**attempt, "item": item})
    events.sort(key=lambda event: (event["observed_at"], event["attempt_id"]))
    return events


def _evidence_weight(event, seen_items):
    """单次作答对每个知识点贡献的权重。"""
    weight = DIFFICULTY_WEIGHT[event["item"]["difficulty_hint"]]
    if event["item_id"] in seen_items:
        weight *= REPEAT_FACTOR
    return weight / len(event["item"]["skills"])


def _population_prior(data, policy, exclude_learner=None, cutoff=None):
    """用其他学生在截止时间之前的证据构造收缩先验。

    截止时间之后、被留出学生的记录一律不可见；总强度封顶，避免题量大的
    知识点淹没个人证据（做得多不等于学得好）。
    """
    totals = defaultdict(lambda: [0.0, 0.0])
    seen_items = defaultdict(set)
    for event in _join_events(data, cutoff=cutoff):
        if exclude_learner is not None and event["learner_id"] == exclude_learner:
            continue
        if event["result"] == "skipped":
            continue
        learner_seen = seen_items[event["learner_id"]]
        weight = _evidence_weight(event, learner_seen)
        learner_seen.add(event["item_id"])
        for skill in event["item"]["skills"]:
            if event["result"] == "correct":
                totals[skill][0] += weight
            else:
                totals[skill][1] += weight
    prior = {}
    for skill in data["skills"]:
        alpha, beta = totals[skill]
        strength = alpha + beta
        if strength > PRIOR_STRENGTH_CAP:
            scale = PRIOR_STRENGTH_CAP / strength
            alpha, beta = alpha * scale, beta * scale
        # 单位伪计数保证无证据知识点退化为均匀先验。
        prior[skill] = (1.0 + alpha, 1.0 + beta)
    return prior


def _skip_rate(events, population):
    """个人跳过倾向，证据不足时向全体率收缩。"""
    total = len(events)
    skips = sum(event["result"] == "skipped" for event in events)
    pop_total = len(population)
    pop_skips = sum(event["result"] == "skipped" for event in population)
    pop_rate = (pop_skips + 1.0) / (pop_total + 2.0) if pop_total else 1.0 / 3.0
    rate = (skips + pop_rate * 2.0) / (total + 2.0)
    return min(SKIP_RATE_CAP, max(SKIP_RATE_FLOOR, rate))


def _skill_states(data, learner, policy, cutoff=None, prior=None):
    if prior is None:
        prior = _population_prior(data, policy, exclude_learner=learner, cutoff=cutoff)
    events = _join_events(data, learner=learner, cutoff=cutoff)
    states = {skill: {"alpha": prior[skill][0], "beta": prior[skill][1],
                      "correct": 0, "wrong": 0, "skipped": 0,
                      "difficulty": {d: {"correct": 0, "wrong": 0} for d in DIFFICULTIES},
                      "repeat_attempts": 0, "shared_weight": 0.0, "evidence_weight": 0.0,
                      "non_easy_correct": False, "evidence": []} for skill in data["skills"]}
    seen_items = set()
    for event in events:
        skills = event["item"]["skills"]
        if event["item_id"] in seen_items:
            for skill in skills:
                states[skill]["repeat_attempts"] += 1
        if event["result"] == "skipped":
            for skill in skills:
                states[skill]["skipped"] += 1
                states[skill]["evidence"].append(_evidence_ref(event, 0.0))
            seen_items.add(event["item_id"])
            continue
        weight = _evidence_weight(event, seen_items)
        for skill in skills:
            state = states[skill]
            state[event["result"]] += 1
            state["evidence_weight"] += weight
            state["difficulty"][event["item"]["difficulty_hint"]][event["result"]] += 1
            if len(skills) > 1:
                state["shared_weight"] += weight
            if event["result"] == "correct" and event["item"]["difficulty_hint"] != "easy":
                state["non_easy_correct"] = True
            if event["result"] == "correct":
                state["alpha"] += weight
            else:
                state["beta"] += weight
            state["evidence"].append(_evidence_ref(event, weight))
        seen_items.add(event["item_id"])
    return states, events


def _evidence_ref(event, weight):
    return {"attempt_id": event["attempt_id"], "item_id": event["item_id"],
            "difficulty": event["item"]["difficulty_hint"], "result": event["result"],
            "weight": round(weight, 4), "skills": list(event["item"]["skills"]),
            "observed_at": event["observed_at"], "presented_at": event["presented_at"]}


def _label(state):
    if state["correct"] + state["wrong"] == 0:
        return "unknown"
    low = beta_quantile(state["alpha"], state["beta"], (1 - CREDIBLE_LEVEL) / 2)
    high = beta_quantile(state["alpha"], state["beta"], 1 - (1 - CREDIBLE_LEVEL) / 2)
    if low >= MASTERY_LOWER_BOUND and state["non_easy_correct"]:
        return "mastered"
    if high <= PRACTICE_UPPER_BOUND:
        return "needs_practice"
    return "emerging"


def _uncertainties(state):
    reasons = []
    scored = state["correct"] + state["wrong"]
    low = beta_quantile(state["alpha"], state["beta"], (1 - CREDIBLE_LEVEL) / 2)
    high = beta_quantile(state["alpha"], state["beta"], 1 - (1 - CREDIBLE_LEVEL) / 2)
    if scored == 0:
        reasons.append("该生未被分配含此知识点的题目，自适应未覆盖不能当作答错")
        return reasons
    if state["evidence_weight"] < 3 or scored < 2:
        reasons.append("有效证据偏少，后验主要受先验影响")
    if high - low > 0.35:
        reasons.append("可信区间较宽（%.2f–%.2f），判断不稳定" % (low, high))
    if not state["non_easy_correct"]:
        reasons.append("缺少中等或综合难度答对证据，简单题连胜不足以证明掌握")
    if state["shared_weight"] > 0.5 * state["evidence_weight"]:
        reasons.append("多数证据来自多知识点综合题，知识点归属不唯一")
    if state["repeat_attempts"]:
        reasons.append("含同题重做 %d 次，重做证据已按 %.0f%% 降权" % (state["repeat_attempts"], REPEAT_FACTOR * 100))
    if state["skipped"]:
        reasons.append("有 %d 次跳过；跳过不计错，但提示需要继续观察" % state["skipped"])
    return reasons


# ---------------------------------------------------------------------------
# 对外视图：掌握判断
# ---------------------------------------------------------------------------

def mastery_view(data, learner, policy=None):
    effective = normalize_policy(data, policy)
    if not isinstance(learner, str) or not learner:
        raise ValueError("需要指定学习者")
    cutoff = effective["as_of"] + "T23:59:59Z"
    prior = _population_prior(data, effective, exclude_learner=learner, cutoff=cutoff)
    states, events = _skill_states(data, learner, effective, cutoff=cutoff, prior=prior)
    rows = []
    for skill in data["skills"]:
        state = states[skill]
        low = beta_quantile(state["alpha"], state["beta"], (1 - CREDIBLE_LEVEL) / 2)
        high = beta_quantile(state["alpha"], state["beta"], 1 - (1 - CREDIBLE_LEVEL) / 2)
        mean = state["alpha"] / (state["alpha"] + state["beta"])
        rows.append({"skill": skill, "label": _label(state),
                     "posterior_mean": round(mean, 4),
                     "credible_low": round(low, 4), "credible_high": round(high, 4),
                     "correct": state["correct"], "wrong": state["wrong"],
                     "skipped": state["skipped"],
                     "scored_attempts": state["correct"] + state["wrong"],
                     "evidence_weight": round(state["evidence_weight"], 4),
                     "repeat_attempts": state["repeat_attempts"],
                     "shared_weight_fraction": round(
                         state["shared_weight"] / state["evidence_weight"], 4)
                     if state["evidence_weight"] else None,
                     "difficulty_evidence": state["difficulty"],
                     "evidence": state["evidence"], "uncertainties": _uncertainties(state)})
    recommendation = _recommend(data, effective, states, events, rows)
    notes = ["掌握值是 Beta 后验概率与可信区间，不是总分；题量大只代表练习机会多，不直接代表学得好。",
             "跳过不计错误；同题重做按 %.0f%% 降权；多知识点题的证据在知识点间均分。" % (REPEAT_FACTOR * 100),
             "未被自适应分配的题目不产生证据，unknown 表示无证据而非未掌握。"]
    return {"model_version": MODEL_VERSION, "learner_id": learner,
            "as_of": effective["as_of"], "policy": effective,
            "skill_rows": rows, "recommendation": recommendation, "notes": notes}


# ---------------------------------------------------------------------------
# 下一份练习建议
# ---------------------------------------------------------------------------

def _prerequisite_map(policy):
    prereq = defaultdict(list)
    for pair in policy["prerequisites"]:
        prereq[pair["after"]].append(pair["before"])
    return prereq


def _target_skill(rows, policy):
    labels = {row["skill"]: row["label"] for row in rows}
    means = {row["skill"]: row["posterior_mean"] for row in rows}
    prereq = _prerequisite_map(policy)
    priority = {"needs_practice": 0, "unknown": 1, "emerging": 2, "mastered": 3}

    def resolve(skill, chain):
        if skill in chain:
            return skill, chain + [skill]
        unmet = sorted((pre for pre in prereq.get(skill, ()) if labels[pre] != "mastered"),
                       key=lambda s: (priority[labels[s]], means[s], s))
        if unmet:
            return resolve(unmet[0], chain + [skill])
        return skill, chain + [skill]

    candidates = sorted((row for row in rows if row["label"] != "mastered"),
                        key=lambda row: (priority[row["label"]], row["posterior_mean"], row["skill"]))
    if not candidates:
        return None, []
    target, chain = resolve(candidates[0]["skill"], [])
    return target, chain


def predicted_outcome(states, item, all_events, population_events):
    """对单题预测 correct/wrong/skipped 三类概率，和为 1。"""
    means = [states[skill]["alpha"] / (states[skill]["alpha"] + states[skill]["beta"])
             for skill in item["skills"]]
    # 多知识点题取各知识点对数几率均值（几何平均口径），再做难度校准。
    avg_logit = sum(math.log(m / (1 - m)) for m in means) / len(means)
    logit = avg_logit + DIFFICULTY_LOGIT_OFFSET[item["difficulty_hint"]]
    p_correct = 1.0 / (1.0 + math.exp(-logit))
    skip_rate = _skip_rate(all_events, population_events)
    return {"correct": round((1 - skip_rate) * p_correct, 4),
            "wrong": round((1 - skip_rate) * (1 - p_correct), 4),
            "skipped": round(skip_rate, 4)}


def _recommend(data, policy, states, events, rows):
    target, chain = _target_skill(rows, policy)
    labels = {row["skill"]: row["label"] for row in rows}
    basis = []
    if target is None:
        return {"target_skill": None, "item_id": None,
                "reason": "所有知识点均达到掌握标准，建议安排综合题保持，不新增补救练习",
                "evidence_basis": [], "uncertainties": [], "rejected": [],
                "prerequisite_chain": []}
    chain = list(reversed(chain))
    target_row = next(row for row in rows if row["skill"] == target)
    basis.append("目标知识点 %s 当前判定为 %s（后验均值 %.2f，可信区间 %.2f–%.2f）"
                 % (target, target_row["label"], target_row["posterior_mean"],
                    target_row["credible_low"], target_row["credible_high"]))
    if len(chain) > 1:
        basis.append("先修顺序：" + " → ".join(chain) + "，先修未掌握时先补先修")
    pool = effective_item_pool(data, policy)
    seen = {event["item_id"] for event in events}
    population_events = _join_events(data, cutoff=policy["as_of"] + "T23:59:59Z")
    item_population = defaultdict(int)
    for event in population_events:
        item_population[event["item_id"]] += 1

    target_difficulty = {"needs_practice": 0, "unknown": 0, "emerging": 1, "mastered": 2}
    wanted = target_difficulty[target_row["label"]]
    ranked, rejected = [], []
    for item in pool:
        if target not in item["skills"]:
            continue
        reasons = []
        if item["item_id"] in seen:
            reasons.append("已做过，优先布置未见过的新题")
        other_skills = [skill for skill in item["skills"] if skill != target]
        blocked = [skill for skill in other_skills if labels[skill] in ("needs_practice", "unknown")]
        if blocked:
            reasons.append("同题其他知识点尚未掌握：" + ",".join(blocked))
        difficulty_distance = abs(DIFFICULTIES.index(item["difficulty_hint"]) - wanted)
        if difficulty_distance:
            reasons.append("难度 %s 与当前阶段目标难度 %s 不一致"
                           % (item["difficulty_hint"], DIFFICULTIES[wanted]))
        tier = 1 if blocked else 0
        ranked.append((tier, 0 if item["item_id"] not in seen else 1,
                       difficulty_distance, len(item["skills"]), item["item_id"], item, reasons))
    ranked.sort(key=lambda record: record[:5])
    rejected = [{"item_id": record[5]["item_id"], "reasons": record[6]}
                for record in ranked[1:9] if record[6]]
    if not ranked:
        return {"target_skill": target, "item_id": None,
                "reason": "题池中没有覆盖 %s 的可用题（可能已退休或被策略排除），请调整题池" % target,
                "evidence_basis": basis,
                "uncertainties": target_row["uncertainties"], "rejected": rejected}
    chosen = ranked[0][5]
    prediction = predicted_outcome(states, chosen, events, population_events)
    uncertainties = list(target_row["uncertainties"])
    if item_population[chosen["item_id"]] == 0:
        uncertainties.append("该题没有任何历史作答记录，难度仅按 difficulty_hint 标定，未经过实测校准")
    if target_row["label"] == "unknown":
        uncertainties.append("目标知识点无个人证据，本次建议基于全体先验，首题用于建立个人基线")
    if len(chosen["skills"]) > 1:
        uncertainties.append("该题同时考查 %s，一次作答的归因不唯一" % ",".join(chosen["skills"]))
    basis.append("选题排序：先修可衔接 > 未做过 > 难度接近 %s > 单知识点 > 编号"
                 % DIFFICULTIES[wanted])
    return {"target_skill": target, "item_id": chosen["item_id"],
            "item_difficulty": chosen["difficulty_hint"], "item_skills": chosen["skills"],
            "prerequisite_chain": chain, "predicted_outcome": prediction,
            "reason": "针对 %s 的下一份练习；%s"
                      % (target, "；".join(ranked[0][6]) if ranked[0][6] else "该题在可用题池中排序最高"),
            "evidence_basis": basis, "uncertainties": uncertainties, "rejected": rejected}


# ---------------------------------------------------------------------------
# 历史回测：当时能否预判下一次表现
# ---------------------------------------------------------------------------

def _baseline_rates(data, exclude_learner, cutoff):
    correct = wrong = skipped = 0
    for event in _join_events(data, cutoff=cutoff):
        if exclude_learner is not None and event["learner_id"] == exclude_learner:
            continue
        if event["result"] == "correct":
            correct += 1
        elif event["result"] == "wrong":
            wrong += 1
        else:
            skipped += 1
    total = correct + wrong + skipped
    if total == 0:
        return {"correct": 1 / 3, "wrong": 1 / 3, "skipped": 1 / 3}
    return {"correct": correct / total, "wrong": wrong / total, "skipped": skipped / total}


def _aggregate(records, probability_key):
    if not records:
        return {"records": 0, "brier": None, "log_loss": None, "accuracy": None,
                "calibration": []}
    brier = log_loss = correct_hits = 0.0
    bins = defaultdict(lambda: [0, 0])
    for record in records:
        probs = record[probability_key]
        y = record["observed"]
        for outcome in ("correct", "wrong", "skipped"):
            brier += (probs[outcome] - (1.0 if outcome == y else 0.0)) ** 2
        log_loss -= math.log(max(probs[y], 1e-6))
        predicted = max(probs, key=probs.get)
        correct_hits += predicted == y
        index = min(4, int(probs["correct"] * 5))
        bins[index][0] += 1
        bins[index][1] += y == "correct"
    calibration = []
    for index in range(5):
        count, hits = bins[index]
        if count:
            calibration.append({"bin": ["0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"][index],
                                "records": count, "observed_correct_rate": round(hits / count, 4)})
    return {"records": len(records), "brier": round(brier / len(records), 4),
            "log_loss": round(log_loss / len(records), 4),
            "accuracy": round(correct_hits / len(records), 4), "calibration": calibration}


def backtest(data, policy=None, mode="leave_one_student_out"):
    """两种模式分开报告，均严格使用题目分配之前已经观测到的证据。"""
    if mode not in ("leave_one_student_out", "incremental_known_learners"):
        raise ValueError("未知回测模式")
    effective = normalize_policy(data, policy)
    learners = sorted({attempt["learner_id"] for attempt in data["attempts"]})
    records = []
    leakage_violations = 0
    for learner in learners:
        own_events = _join_events(data, learner=learner)
        exclude = learner if mode == "leave_one_student_out" else None
        for event in own_events:
            cutoff = event["presented_at"]
            # 泄漏防线：只允许 observed_at 严格早于本题分配时间的证据。
            prior_states, prior_events = _skill_states(
                data, learner, effective, cutoff=cutoff,
                prior=_population_prior(data, effective, exclude_learner=exclude, cutoff=cutoff))
            population_events = [e for e in _join_events(data, cutoff=cutoff)
                                 if exclude is None or e["learner_id"] != exclude]
            if any(e["observed_at"] >= cutoff for e in prior_events + population_events):
                leakage_violations += 1
            probs = predicted_outcome(prior_states, event["item"], prior_events, population_events)
            records.append({"learner_id": learner, "attempt_id": event["attempt_id"],
                            "item_id": event["item_id"], "observed": event["result"],
                            "cutoff": cutoff, "cold_start": not prior_events,
                            "model": probs,
                            "baseline": _baseline_rates(data, exclude, cutoff)})
    cold = [record for record in records if record["cold_start"]]
    per_learner = {}
    for learner in learners:
        part = [record for record in records if record["learner_id"] == learner]
        per_learner[learner] = {"model": _aggregate(part, "model"),
                                "population_baseline": _aggregate(part, "baseline")}
    return {"model_version": MODEL_VERSION, "mode": mode, "policy": effective,
            "leakage_check": {"rule": "仅使用 observed_at < 本题 presented_at 的证据",
                              "checked_records": len(records), "violations": leakage_violations},
            "overall": {"model": _aggregate(records, "model"),
                        "population_baseline": _aggregate(records, "baseline")},
            "cold_start": {"records": len(cold),
                           "model": _aggregate(cold, "model"),
                           "population_baseline": _aggregate(cold, "baseline")},
            "per_learner": per_learner,
            "notes": ["留一模式在每个预测点排除该学生全部记录；增量模式允许其他学生的更早记录，两种模式都不使用本题及之后的答卷。",
                      "未分配的题目不进入回测；跳过作为单独结果类别参与评分。"]}
