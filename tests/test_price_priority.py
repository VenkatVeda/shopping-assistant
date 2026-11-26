"""apply_price_priority: with a budget cap, good matches are ordered by price (closest to the cap
first); weaker matches never jump ahead; without a cap the order is the plain rerank order."""
import importlib.util
import os
import sys
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_rag_utils():
    # core/__init__ imports heavy dependencies; load only what rag_utils needs.
    pkg = types.ModuleType("core")
    pkg.__path__ = [os.path.join(REPO, "core")]
    sys.modules["core"] = pkg
    for name in ("models", "prompt_loader", "rag_utils"):
        spec = importlib.util.spec_from_file_location(f"core.{name}", os.path.join(REPO, "core", f"{name}.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"core.{name}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["core.rag_utils"]


ru = _load_rag_utils()


def _p(name, price, score):
    return {"id": name, "metadata": {"name": name, "price": price}, "rerank_score": score}


def test_good_matches_ordered_by_price_toward_cap():
    items = [_p("cheap", "$100.00", 95), _p("mid", 199.0, 85), _p("near_cap", "290", 82), _p("cap", 299.99, 80)]
    out = [p["id"] for p in ru.apply_price_priority(items, 300)]
    assert out == ["cap", "near_cap", "mid", "cheap"]


def test_weak_match_is_not_promoted_by_price():
    items = [_p("great_cheap", 90, 96), _p("poor_expensive", 295, 50), _p("good_mid", 150, 80)]
    out = [p["id"] for p in ru.apply_price_priority(items, 300)]
    assert out == ["good_mid", "great_cheap", "poor_expensive"]


def test_no_budget_cap_keeps_relevance_order():
    items = [_p("a", 100, 70), _p("b", 290, 95), _p("c", 50, 80)]
    out = [p["id"] for p in ru.apply_price_priority(items, None)]
    assert out == ["b", "c", "a"]


def test_missing_price_sorts_last_within_good_tier_and_threshold_is_configurable(monkeypatch):
    items = [_p("no_price", None, 90), _p("priced", 120, 78)]
    assert [p["id"] for p in ru.apply_price_priority(items, 300)] == ["priced", "no_price"]
    monkeypatch.setenv("PRICE_PRIORITY_MIN_RELEVANCE", "101")  # disabled -> pure relevance
    assert [p["id"] for p in ru.apply_price_priority(items, 300)] == ["no_price", "priced"]


def test_rerank_score_is_untouched():
    items = [_p("x", 250, 82)]
    ru.apply_price_priority(items, 300)
    assert items[0]["rerank_score"] == 82
