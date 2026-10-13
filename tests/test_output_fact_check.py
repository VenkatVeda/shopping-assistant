"""Deterministic output fact check: names, brands and prices of the described products.
No network, no Databricks access, and the chat model must never be called.
usage: python test_output_fact_check.py"""
import os
import sys
import types

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)

# core/guardrails.py imports databricks_langchain at module level; load it without core/__init__ (workflow).
_stub = types.ModuleType("databricks_langchain")
_stub.ChatDatabricks = object
sys.modules.setdefault("databricks_langchain", _stub)
_pkg = types.ModuleType("core")
_pkg.__path__ = [os.path.join(_ROOT, "core")]
sys.modules["core"] = _pkg

import core.guardrails as g  # noqa: E402

g.log_guardrail = lambda **kw: None


class NoModel:
    def invoke(self, *a, **k):
        raise AssertionError("the output check must not call the model")


guard = g.OutputGuardrail(NoModel())


def product(name, brand, price):
    return {"metadata": {"name": name, "brand": brand, "price": str(price)}}


PRODUCTS = [
    product("Yale Black Tote Bag in Black", "Fossil", 169.0),
    product("Blair Tote Bag in Black", "Kate Spade", 199.0),
    product("Misty Tote Bag in Black", "Coach", 84.95),
]
INTRO = "You're looking for a black tote bag for the office, so I picked three roomy and professional options."


def reply(*blocks, intro=INTRO, outro=""):
    return intro + "\n\n" + "\n\n".join(blocks) + (("\n\n" + outro) if outro else "")


def block(name, brand, price, text="Spacious main compartment with a zip closure for daily use."):
    return f"**{name}** by {brand}\n**${price}**\n{text}"


GOOD = reply(
    block("Yale Black Tote Bag in Black", "Fossil", "169.00"),
    block("Blair Tote Bag in Black", "Kate Spade", "199.00"),
    block("Misty Tote Bag in Black", "Coach", "84.95"),
)


def check(response, query="black tote bag for office", products=PRODUCTS):
    return guard._check_product_facts(response, query, products)


results = []


def case(label, ok, detail=""):
    results.append(ok)
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"  -> {detail}"))


# 1. a correct reply passes untouched
r = check(GOOD)
case("correct reply passes unchanged", r["status"] == "pass" and r["safe_response"] == GOOD, r)

# 2. a wrong price is rewritten from the data, intro kept
bad = GOOD.replace("**$199.00**", "**$149.00**")
r = check(bad)
case("wrong price -> rewritten with the real price",
     r["status"] == "warning" and r["corrections_made"] and "$199.00" in r["safe_response"]
     and "$149.00" not in r["safe_response"] and r["safe_response"].startswith(INTRO), r)

# 3. an invented product is removed
fake = reply(block("Yale Black Tote Bag in Black", "Fossil", "169.00"),
             block("Phantom Luxe Satchel", "Gucci", "999.00"))
r = check(fake)
case("invented product -> rewritten from real products only",
     r["corrections_made"] and "Phantom" not in r["safe_response"] and "Gucci" not in r["safe_response"]
     and "Misty Tote Bag in Black" in r["safe_response"], r)

# 4. wrong brand
r = check(GOOD.replace("by Coach", "by Gucci"))
case("wrong brand -> rewritten", r["corrections_made"] and "Gucci" not in r["safe_response"], r)

# 5. a shortened name still matches; case and punctuation do not matter
short = reply(block("Yale Black Tote", "Fossil", "169.00"),
              block("BLAIR tote bag in black!", "kate spade", "199"))
r = check(short)
case("shortened / differently cased names still match", r["status"] == "pass", r)

# 6. a price swapped between products is caught
swapped = reply(block("Yale Black Tote Bag in Black", "Fossil", "199.00"),
                block("Blair Tote Bag in Black", "Kate Spade", "169.00"))
r = check(swapped)
case("prices swapped between products -> rewritten", r["corrections_made"] and "$169.00" in r["safe_response"], r)

# 7. a budget the user typed is not treated as a wrong price
budget = GOOD.replace(INTRO, "Here are options under $250 that suit your office needs, all in a roomy size.")
r = check(budget, query="black tote under $250")
case("budget from the query in the intro is allowed", r["status"] == "pass", r)

# 8. an invented price in the intro is only a warning; the reply is left as written
odd = GOOD.replace(INTRO, "These start at just $12.34, which makes them great value for the office today.")
r = check(odd)
case("unknown price in the intro -> warning, text untouched",
     r["status"] == "warning" and not r["corrections_made"] and r["safe_response"] == odd, r)

# 9. a price range in the closing sentence (beyond the price window) is ignored
outro = GOOD + "\n\n" + ("These bags are all very well made and ship quickly. " * 4) + "Prices range from $84.95 to $279.00."
r = check(outro)
case("price range in the closing text is ignored", r["status"] == "pass", r)

# 10. free-form replies (product discussion): correct price passes, wrong price is a warning only
one = [product("Yale Black Tote Bag in Black", "Fossil", 169.0)]
chat_ok = "This tote is $169.00 and has a zip closure, so it keeps your things secure on the commute."
chat_bad = "This tote is $159.00 and has a zip closure, so it keeps your things secure on the commute."
r1, r2 = check(chat_ok, "tell me about this bag", one), check(chat_bad, "tell me about this bag", one)
case("free-form reply: correct price passes", r1["status"] == "pass", r1)
case("free-form reply: wrong price is a warning and is not rewritten",
     r2["status"] == "warning" and not r2["corrections_made"] and r2["safe_response"] == chat_bad, r2)

# 11. nothing to compare against -> pass
case("no usable product data -> pass", check(GOOD, products=[{}])["status"] == "pass")

# 12. end to end through validate_response: no model call, rewritten reply returned
out = guard.validate_response(bad, "black tote bag for office", PRODUCTS)
case("validate_response uses the fact check and never calls the model",
     out["status"] == "warning" and "$199.00" in out["safe_response"], out)
out = guard.validate_response(GOOD, "black tote bag for office", PRODUCTS)
case("validate_response passes a correct reply", out["status"] == "pass" and out["safe_response"] == GOOD, out)

print("\nRESULT: all fact checks ok" if all(results) else "\nFAIL: some fact checks failed")
sys.exit(0 if all(results) else 1)
