from pathlib import Path
import json

from .domain import explore, validate
from .mastery import (MODEL_VERSION, backtest, default_policy, mastery_view,
                      normalize_policy)
from .storage import Store, fingerprint


class Workbench:
    def __init__(self, root, state):
        self.root = Path(root)
        self.product = json.loads((self.root / "product.json").read_text(encoding="utf-8"))
        self.store = Store(Path(state) / "research.sqlite3")
        initial = json.loads((self.root / "fixtures" / "data.json").read_text(encoding="utf-8"))
        validate(initial)
        self.default_revision = self.store.put_dataset("演示材料", initial)

    def context(self):
        return {"product": self.product, "default_revision": self.default_revision,
                "datasets": self.store.datasets(), "notebooks": self.store.notebooks(),
                "advice_publications": self.store.advice_publications(),
                "model_version": MODEL_VERSION}

    def upload(self, body):
        data = body["data"]
        try:
            validate(data)
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("字段结构不完整或类型不符") from error
        return {"revision": self.store.put_dataset(body.get("label", ""), data)}

    def _resolved(self, body):
        revision = body.get("revision", self.default_revision)
        data = self.store.dataset(revision)
        selection = body.get("selection", self.product["selection"])
        if not isinstance(selection, dict):
            raise ValueError("选择条件必须是对象")
        learner = body.get("learner") or selection.get("learner")
        policy = normalize_policy(data, body.get("policy"))
        return revision, data, learner, policy

    def inspect(self, body):
        revision = body.get("revision", self.default_revision)
        selection = body.get("selection", self.product["selection"])
        if not isinstance(selection, dict):
            raise ValueError("选择条件必须是对象")
        data = self.store.dataset(revision)
        result = explore(data, selection)
        return {"revision": revision, "selection": selection, "data_sha256": fingerprint(data), "view": result}

    def mastery(self, body):
        revision, data, learner, policy = self._resolved(body)
        if not learner:
            raise ValueError("掌握判断需要指定学习者")
        return {"revision": revision, "learner_id": learner, "policy": policy,
                "data_sha256": fingerprint(data), "view": mastery_view(data, learner, policy)}

    def evaluate_backtest(self, body):
        revision = body.get("revision", self.default_revision)
        data = self.store.dataset(revision)
        mode = body.get("mode", "leave_one_student_out")
        policy = normalize_policy(data, body.get("policy"))
        return {"revision": revision, "mode": mode, "policy": policy,
                "data_sha256": fingerprint(data), "report": backtest(data, policy, mode)}

    def save(self, body):
        result = self.inspect(body)
        return self.store.save(body.get("title", ""), result["revision"], result["selection"], result)

    def publish_advice(self, body):
        """服务端按当前数据与策略重新计算后冻结，客户端无法伪造建议内容。"""
        mastery = self.mastery(body)
        return self.store.publish_advice(body.get("title", ""), mastery["revision"],
                                         mastery["learner_id"], mastery["policy"],
                                         mastery["view"])

    def advice(self, identifier):
        return self.store.advice(identifier)
