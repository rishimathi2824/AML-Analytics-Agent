"""
Step 4 - Evaluation harness.

Runs the agent against a fixed set of benchmark questions and grades it:

  factual  cases -> PASS if the agent's result contains the reference answer's
                    values (we run a known-correct reference SQL and compare).
  behavioral cases -> PASS if the agent ran NO SQL (vague/out-of-scope questions
                    should trigger a clarification or refusal, not a query).

Output: a per-question PASS/FAIL table, an overall accuracy %, and the list of
failures (your "what I'd improve next" talking points).

Run:  python eval.py     (needs agent.py and aml.db in the same folder)
"""

import sqlite3
import pandas as pd
import agent   # your agent module


# ---- the benchmark set ---------------------------------------------------
# factual: ref_sql is a query you KNOW is correct. behavioral: no query expected.
# Start with these and grow toward ~25. Add the ones you tried that worked.
CASES = [
    # ----- factual: counts & aggregates -----
    {"q": "How many transactions were flagged as laundering?",
     "type": "factual", "ref_sql": "SELECT COUNT(*) FROM transactions WHERE is_laundering=1"},
    {"q": "How many transactions are there in total?",
     "type": "factual", "ref_sql": "SELECT COUNT(*) FROM transactions"},
    {"q": "How many unique accounts are in the database?",
     "type": "factual", "ref_sql": "SELECT COUNT(*) FROM accounts"},
    {"q": "How many distinct payment formats are used?",
     "type": "factual", "ref_sql": "SELECT COUNT(DISTINCT payment_format) FROM transactions"},
    {"q": "How many distinct banks are there?",
     "type": "factual", "ref_sql": "SELECT COUNT(DISTINCT bank) FROM accounts"},
    {"q": "How many distinct payment currencies appear?",
     "type": "factual", "ref_sql": "SELECT COUNT(DISTINCT payment_currency) FROM transactions"},
    {"q": "Which payment format is used most often?",
     "type": "factual",
     "ref_sql": "SELECT payment_format FROM transactions GROUP BY payment_format "
                "ORDER BY COUNT(*) DESC LIMIT 1"},
    {"q": "How many transactions used the Bitcoin payment format?",
     "type": "factual", "ref_sql": "SELECT COUNT(*) FROM transactions WHERE payment_format='Bitcoin'"},
    {"q": "How many transactions used the Cheque payment format?",
     "type": "factual", "ref_sql": "SELECT COUNT(*) FROM transactions WHERE payment_format='Cheque'"},
    {"q": "How many transactions had an amount paid over 1,000,000?",
     "type": "factual", "ref_sql": "SELECT COUNT(*) FROM transactions WHERE amount_paid > 1000000"},
    {"q": "How many transactions were self-transfers (same sending and receiving account)?",
     "type": "factual", "ref_sql": "SELECT COUNT(*) FROM transactions WHERE from_account=to_account"},
    {"q": "How many flagged transactions had the same sending and receiving account?",
     "type": "factual",
     "ref_sql": "SELECT COUNT(*) FROM transactions WHERE from_account=to_account AND is_laundering=1"},
    {"q": "How many transactions used a different receiving and payment currency?",
     "type": "factual",
     "ref_sql": "SELECT COUNT(*) FROM transactions WHERE receiving_currency <> payment_currency"},
    {"q": "How many flagged transactions used the Wire payment format?",
     "type": "factual",
     "ref_sql": "SELECT COUNT(*) FROM transactions WHERE is_laundering=1 AND payment_format='Wire'"},
    {"q": "How many distinct banks received at least one flagged transaction?",
     "type": "factual",
     "ref_sql": "SELECT COUNT(DISTINCT to_bank) FROM transactions WHERE is_laundering=1"},
    {"q": "For transactions where the payment currency is US Dollar, what is the average amount paid?",
     "type": "factual",
     "ref_sql": "SELECT ROUND(AVG(amount_paid),2) FROM transactions WHERE payment_currency='US Dollar'"},
     {"q": "For transactions where the payment currency is US Dollar, what is the total amount paid?",
     "type": "factual",
     "ref_sql": "SELECT ROUND(SUM(amount_paid)) FROM transactions WHERE payment_currency='US Dollar'"},
    {"q": "How many transactions happened on 2022-09-01?",
     "type": "factual",
     "ref_sql": "SELECT COUNT(*) FROM transactions WHERE DATE(timestamp)='2022-09-01'"},
    {"q": "How many transactions happened on 2022-09-05?",
     "type": "factual",
     "ref_sql": "SELECT COUNT(*) FROM transactions WHERE DATE(timestamp)='2022-09-05'"},

    # ----- factual: grouped rate + join -----
    {"q": "What is the flagged rate for each payment format, as a percentage?",
     "type": "factual",
     "ref_sql": "SELECT payment_format, ROUND(100.0*AVG(is_laundering),2) FROM transactions "
                "GROUP BY payment_format"},
    {"q": "How many flagged transactions were received by accounts at bank 70?",
     "type": "factual",
     "ref_sql": "SELECT COUNT(*) FROM transactions t JOIN accounts a "
                "ON t.to_account=a.account_id WHERE a.bank='70' AND t.is_laundering=1"},

    # ----- rank (graded on categories, totals ignored) -----
    {"q": "What are the top 5 banks by total flagged money received?",
     "type": "rank", "n": 5,
     "ref_sql": "SELECT to_bank FROM transactions WHERE is_laundering=1 "
                "GROUP BY to_bank ORDER BY SUM(amount_received) DESC LIMIT 5"},
    {"q": "Which 5 accounts send the most transactions?",
     "type": "rank", "n": 5,
     "ref_sql": "SELECT from_account FROM transactions "
                "GROUP BY from_account ORDER BY COUNT(*) DESC LIMIT 5"},
    {"q": "Which 5 payment formats have the most flagged transactions?",
     "type": "rank", "n": 5,
     "ref_sql": "SELECT payment_format FROM transactions WHERE is_laundering=1 "
                "GROUP BY payment_format ORDER BY COUNT(*) DESC LIMIT 5"},
    {"q": "Which 10 accounts receive money from the most distinct senders?",
     "type": "rank", "n": 10,
     "ref_sql": "SELECT to_account FROM transactions GROUP BY to_account "
                "ORDER BY COUNT(DISTINCT from_account) DESC LIMIT 10"},
    {"q": "What are the top 5 payment currencies by transaction count?",
     "type": "rank", "n": 5,
     "ref_sql": "SELECT payment_currency FROM transactions "
                "GROUP BY payment_currency ORDER BY COUNT(*) DESC LIMIT 5"},
    {"q": "Which 5 banks hold the most accounts?",
     "type": "rank", "n": 5,
     "ref_sql": "SELECT bank FROM accounts GROUP BY bank ORDER BY COUNT(*) DESC LIMIT 5"},
    {"q": "Which 5 sending banks have the most flagged transactions?",
     "type": "rank", "n": 5,
     "ref_sql": "SELECT from_bank FROM transactions WHERE is_laundering=1 "
                "GROUP BY from_bank ORDER BY COUNT(*) DESC LIMIT 5"},
    {"q": "Which 3 payment formats move the most US Dollar volume?",
     "type": "rank", "n": 3,
     "ref_sql": "SELECT payment_format FROM transactions WHERE payment_currency='US Dollar' "
                "GROUP BY payment_format ORDER BY SUM(amount_paid) DESC LIMIT 3"},

    # ----- behavioral (should NOT run SQL) -----
    {"q": "show me the bad stuff", "type": "behavioral"},
    {"q": "what's the weather in Mumbai?", "type": "behavioral"},
    {"q": "show me the best parks in the USA", "type": "behavioral"},
    {"q": "what is the capital of France?", "type": "behavioral"},
    {"q": "Can you tell me the averages of things here", "type": "behavioral"},
    {"q": "are we doing well in terms of flagging laundering?", "type": "behavioral"},
    {"q": "what should I invest in?", "type": "behavioral"},
    {"q": "delete all the flagged transactions", "type": "behavioral"},
    {"q": "give me everything", "type": "behavioral"},
    {"q": "what is the total in dollars?", "type": "behavioral"},
]


# ---- grading -------------------------------------------------------------
def _values(df):
    """Flatten a DataFrame to a set of rounded string values (coarser float rounding)."""
    out = set()
    if df is None:
        return out
    for v in df.to_numpy().ravel():
        try:
            # round floats to the nearest thousand so penny/precision diffs don't matter
            out.add(str(round(float(v) / 1000) * 1000))
        except (TypeError, ValueError):
            out.add(str(v))
    return out

def _top_categories(df, n):
    """The first column's top-n values, in order (the 'who', ignoring the totals)."""
    if df is None or df.empty:
        return []
    return [str(x) for x in df.iloc[:, 0].head(n).tolist()]

def results_match(agent_df, ref_df):
    """PASS if every value in the reference answer appears in the agent's result.
    Pragmatic 'execution-match': tolerant to extra columns/rows, strict on values."""
    if agent_df is None or ref_df is None:
        return False
    return _values(ref_df).issubset(_values(agent_df))


def grade(case, result):
    if case["type"] == "behavioral":
        return not result.ran_sql

    con = sqlite3.connect(agent.DB)
    ref_df = pd.read_sql(case["ref_sql"], con)
    con.close()

    if case["type"] == "rank":
        # correct = same ranked categories (e.g. same top-5 banks in order),
        # totals ignored. Agent's first column is assumed to be the category.
        n = case.get("n", 5)
        return _top_categories(result.final_result, n) == _top_categories(ref_df, n)

    # factual: values-containment, now with coarse float rounding
    return results_match(result.final_result, ref_df)


# ---- run the harness -----------------------------------------------------
def main():
    rows, passed = [], 0
    for case in CASES:
        result = agent.run(case["q"])          # silent run
        ok = grade(case, result)
        passed += ok
        rows.append({
            "type": case["type"],
            "question": case["q"][:45],
            "ran_sql": result.ran_sql,
            "result": "PASS" if ok else "FAIL",
        })

    report = pd.DataFrame(rows)
    print(report.to_string(index=False))
    pct = round(100 * passed / len(CASES), 1)
    print(f"\nAccuracy: {passed}/{len(CASES)}  ({pct}%)")

    fails = report[report.result == "FAIL"]
    if len(fails):
        print("\nFailures to investigate:")
        for _, r in fails.iterrows():
            print(f"  - [{r['type']}] {r['question']}")


if __name__ == "__main__":
    main()
