"""Dependency-free structured knowledge model shared by host and sandbox."""

KNOWLEDGE_SCHEMA_VERSION = 1
_HYPOTHESIS_STATUSES = {
    "untested", "supported", "confirmed", "contradicted",
    "rejected", "carried", "archived",
}
_PREDICTION_STATUSES = {"pending", "matched", "partial", "mismatch", "cancelled", "expired"}
_SCOPES = {"level", "game"}
_PREDICTION_KINDS = {
    "changed_cell_count", "cell_change", "color_transition",
    "no_visible_change", "terminal_state", "level_change",
}


def _clone(value):
    if isinstance(value, dict):
        return {str(key): _clone(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clone(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _level(value, default=1):
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, int(default))


def _compact_text(value, field, limit=500, required=True):
    text = " ".join(str(value or "").split())
    if required and not text:
        raise ValueError(f"{field} must be non-empty.")
    if len(text) > limit:
        raise ValueError(f"{field} exceeds {limit} characters.")
    return text


def empty_knowledge(level=1):
    return {
        "schema_version": KNOWLEDGE_SCHEMA_VERSION,
        "current_level": _level(level),
        "next_ids": {"hypothesis": 1, "prediction": 1},
        "hypotheses": [],
        "predictions": [],
        "events": [],
    }


def normalize_knowledge(value, level=1):
    source = value if isinstance(value, dict) else {}
    result = empty_knowledge(level)
    result["current_level"] = _level(source.get("current_level"), level)
    source_ids = source.get("next_ids") if isinstance(source.get("next_ids"), dict) else {}
    for key in result["next_ids"]:
        try:
            result["next_ids"][key] = max(1, int(source_ids.get(key, 1)))
        except (TypeError, ValueError):
            pass
    for key in ("hypotheses", "predictions", "events"):
        raw = source.get(key)
        if isinstance(raw, list):
            result[key] = [_clone(item) for item in raw if isinstance(item, dict)]
    return result


def transition_knowledge_level(value, new_level):
    store = normalize_knowledge(value, new_level)
    old_level = _level(store.get("current_level"), new_level)
    new_level = _level(new_level)
    if old_level == new_level:
        return store
    for item in store["hypotheses"]:
        if item.get("archived"):
            continue
        if item.get("scope") == "level":
            item["archived"] = True
            item["status"] = "archived"
            item["archive_reason"] = f"level_changed:{old_level}->{new_level}"
            item.setdefault("history", []).append({
                "level": new_level, "change": "archived_on_level_change"
            })
        elif item.get("scope") == "game" and item.get("status") not in {
            "rejected", "archived"
        }:
            item["status"] = "carried"
            item["current_level_validation"] = "pending"
            item.setdefault("history", []).append({
                "level": new_level, "change": "carried_to_new_level"
            })
    for item in store["predictions"]:
        if item.get("status") == "pending":
            item["status"] = "expired"
            item["resolution"] = f"level_changed:{old_level}->{new_level}"
    store["events"].append({"kind": "level_change", "from": old_level, "to": new_level})
    store["current_level"] = new_level
    return store


class _Hypotheses:
    def __init__(self, owner):
        self._owner = owner

    def add(self, *, subject, predicate, value, scope="level", confidence=0.5, reason=""):
        if scope not in _SCOPES:
            raise ValueError("scope must be 'level' or 'game'.")
        confidence = float(confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1].")
        item = {
            "id": self._owner._next_id("hypothesis", "H"),
            "scope": scope,
            "level_created": self._owner.level,
            "subject": _clone(subject),
            "predicate": _compact_text(predicate, "predicate", 120),
            "value": _clone(value),
            "status": "untested",
            "confidence": confidence,
            "reason": _compact_text(reason, "reason", required=False),
            "evidence": [],
            "history": [{"level": self._owner.level, "change": "created"}],
            "archived": False,
        }
        self._owner._state["hypotheses"].append(item)
        return _clone(item)

    def get(self, hypothesis_id):
        return _clone(self._owner._find("hypotheses", hypothesis_id))

    def list(self, *, scope=None, status=None, include_archived=False):
        statuses = {status} if isinstance(status, str) else set(status or [])
        result = []
        for item in self._owner._state["hypotheses"]:
            if not include_archived and item.get("archived"):
                continue
            if scope is not None and item.get("scope") != scope:
                continue
            if statuses and item.get("status") not in statuses:
                continue
            result.append(_clone(item))
        return result

    def update(self, hypothesis_id, *, status=None, confidence=None, reason=None):
        item = self._owner._find("hypotheses", hypothesis_id)
        change = {"level": self._owner.level, "change": "updated"}
        if status is not None:
            if status not in _HYPOTHESIS_STATUSES:
                raise ValueError("invalid hypothesis status")
            item["status"] = status
            change["status"] = status
            if status != "archived":
                item["archived"] = False
        if confidence is not None:
            confidence = float(confidence)
            if not 0.0 <= confidence <= 1.0:
                raise ValueError("confidence must be in [0, 1].")
            item["confidence"] = confidence
            change["confidence"] = confidence
        if reason is not None:
            item["reason"] = _compact_text(reason, "reason", required=False)
        item.setdefault("history", []).append(change)
        return _clone(item)

    def add_evidence(self, hypothesis_id, *, relation, note, transition_id=None):
        if relation not in {"supports", "contradicts"}:
            raise ValueError("relation must be 'supports' or 'contradicts'.")
        item = self._owner._find("hypotheses", hypothesis_id)
        evidence = {
            "level": self._owner.level,
            "relation": relation,
            "transition_id": None if transition_id is None else str(transition_id),
            "note": _compact_text(note, "note"),
        }
        item.setdefault("evidence", []).append(evidence)
        item.setdefault("history", []).append({
            "level": self._owner.level,
            "change": "evidence_added",
            "relation": relation,
        })
        if relation == "contradicts":
            item["status"] = "contradicted"
        elif item.get("status") in {"untested", "carried"}:
            item["status"] = "supported"
            item["current_level_validation"] = "supported"
        return _clone(item)

    def archive(self, hypothesis_id, *, reason):
        item = self._owner._find("hypotheses", hypothesis_id)
        item["archived"] = True
        item["status"] = "archived"
        item["archive_reason"] = _compact_text(reason, "reason")
        item.setdefault("history", []).append({
            "level": self._owner.level, "change": "archived"
        })
        return _clone(item)

    def promote(self, hypothesis_id, *, reason):
        item = self._owner._find("hypotheses", hypothesis_id)
        if item.get("archived"):
            raise ValueError("an archived hypothesis cannot be promoted.")
        item["scope"] = "game"
        item["promotion_reason"] = _compact_text(reason, "reason")
        item.setdefault("history", []).append({
            "level": self._owner.level, "change": "promoted_to_game"
        })
        return _clone(item)


class _Predictions:
    def __init__(self, owner):
        self._owner = owner

    def add(self, *, action, expected, hypothesis_id=None, scope="level", note=""):
        if scope not in _SCOPES:
            raise ValueError("scope must be 'level' or 'game'.")
        if not isinstance(expected, list) or not expected:
            raise ValueError("expected must be a non-empty list of conditions.")
        for condition in expected:
            if not isinstance(condition, dict) or condition.get("kind") not in _PREDICTION_KINDS:
                raise ValueError(f"prediction kind must be one of {sorted(_PREDICTION_KINDS)}")
        if hypothesis_id is not None:
            self._owner._find("hypotheses", hypothesis_id)
        item = {
            "id": self._owner._next_id("prediction", "P"),
            "scope": scope,
            "level_created": self._owner.level,
            "hypothesis_id": hypothesis_id,
            "action": _clone(action),
            "expected": _clone(expected),
            "note": _compact_text(note, "note", required=False),
            "status": "pending",
            "evaluation": None,
        }
        self._owner._state["predictions"].append(item)
        return _clone(item)

    def get(self, prediction_id):
        return _clone(self._owner._find("predictions", prediction_id))

    def list(self, *, status=None):
        return [
            _clone(item) for item in self._owner._state["predictions"]
            if status is None or item.get("status") == status
        ]

    def evaluate(self, prediction_id, *, before=None, after=None,
                 terminal_state=None, level_changed=None):
        item = self._owner._find("predictions", prediction_id)
        if item.get("status") != "pending":
            return _clone(item)
        needs_diff = any(
            condition.get("kind") not in {"terminal_state", "level_change"}
            for condition in item["expected"]
        )
        diff = {}
        if needs_diff:
            if self._owner._frame_diff is None:
                raise ValueError("prediction evaluation has no frame_diff function.")
            diff = self._owner._frame_diff(before, after)
            if not isinstance(diff, dict) or diff.get("error"):
                raise ValueError(f"prediction cannot be evaluated: {diff}")
        matched, failed = [], []
        for condition in item["expected"]:
            kind = condition["kind"]
            observed = None
            ok = False
            if kind == "changed_cell_count":
                observed = diff.get("changed_cell_count")
                ok = observed == int(condition.get("value"))
            elif kind == "no_visible_change":
                observed = diff.get("changed_cell_count")
                ok = observed == 0
            elif kind == "color_transition":
                key = f"{condition.get('before')}>{condition.get('after')}"
                observed = (diff.get("color_transitions") or {}).get(key, 0)
                ok = observed == int(condition.get("count", 1))
            elif kind == "cell_change":
                row, col = int(condition["row"]), int(condition["col"])
                observed = next((entry for entry in diff.get("changed_cells", [])
                                 if entry.get("row") == row and entry.get("col") == col), None)
                ok = observed is not None
                if ok and "before" in condition:
                    ok = observed.get("before") == condition["before"]
                if ok and "after" in condition:
                    ok = observed.get("after") == condition["after"]
            elif kind == "terminal_state":
                observed = bool(terminal_state)
                ok = observed == bool(condition.get("value", True))
            elif kind == "level_change":
                observed = bool(level_changed)
                ok = observed == bool(condition.get("value", True))
            bucket = matched if ok else failed
            bucket.append({
                "condition": _clone(condition), "observed": _clone(observed)
            })
        item["status"] = "matched" if not failed else ("partial" if matched else "mismatch")
        item["evaluation"] = {
            "level": self._owner.level,
            "matched": matched,
            "failed": failed,
            "changed_cell_count": diff.get("changed_cell_count"),
        }
        return _clone(item)

    def resolve(self, prediction_id, *, status, note):
        if status not in _PREDICTION_STATUSES - {"pending", "expired"}:
            raise ValueError("status must be matched, partial, mismatch, or cancelled.")
        item = self._owner._find("predictions", prediction_id)
        item["status"] = status
        item["evaluation"] = {
            "level": self._owner.level,
            "manual": True,
            "note": _compact_text(note, "note"),
        }
        return _clone(item)


class KnowledgeView:
    def __init__(self, state, level=1, frame_diff_fn=None):
        self._state = normalize_knowledge(state, level)
        self.level = _level(level, self._state.get("current_level", 1))
        self._state["current_level"] = self.level
        self._frame_diff = frame_diff_fn
        self.hypotheses = _Hypotheses(self)
        self.predictions = _Predictions(self)

    def _next_id(self, key, prefix):
        value = max(1, int(self._state["next_ids"].get(key, 1)))
        self._state["next_ids"][key] = value + 1
        return f"{prefix}{value}"

    def _find(self, bucket, identifier):
        for item in self._state[bucket]:
            if item.get("id") == identifier:
                return item
        raise KeyError(f"unknown {bucket[:-1]} id: {identifier}")

    def summary(self):
        active = [item for item in self._state["hypotheses"] if not item.get("archived")]
        return {
            "level": self.level,
            "active_hypotheses": len(active),
            "hypotheses_by_status": {
                status: sum(item.get("status") == status for item in active)
                for status in sorted(_HYPOTHESIS_STATUSES)
            },
            "pending_predictions": sum(
                item.get("status") == "pending" for item in self._state["predictions"]
            ),
        }

    def export(self):
        return _clone(self._state)
