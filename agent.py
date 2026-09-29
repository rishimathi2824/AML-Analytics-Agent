"""
Step 3 agent (refactored for Step 4 evals).

run()  -> drives the tool-use loop, returns a structured result the eval
          harness can inspect (the queries it ran, its final result, etc.).
ask()  -> thin wrapper that prints nicely for interactive use.

If a query errors, Claude sees the error and retries (the agentic loop).
A guardrail keeps it read-only and makes it clarify instead of guessing.

Setup:
    pip install anthropic python-dotenv pandas
    .env file with: ANTHROPIC_API_KEY=sk-ant-your-key-here
"""

import sqlite3
from dataclasses import dataclass, field

import pandas as pd
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()
client = Anthropic()

DB = "aml.db"
MODEL = "claude-haiku-4-5-20251001"   # swap to "claude-sonnet-4-6" if SQL quality slips

SCHEMA = """
SQLite database with two tables:

accounts(account_id TEXT, bank TEXT)
transactions(timestamp, from_bank, from_account, to_bank, to_account,
             amount_received, receiving_currency, amount_paid,
             payment_currency, payment_format, is_laundering)

Notes:
- transactions.from_account and transactions.to_account both join to accounts.account_id
- is_laundering = 1 means flagged as suspicious; 0 means clean
- amounts are numeric; timestamp is a datetime
"""

SYSTEM = (
    "You are a financial-crime data analyst assistant for an AML transaction database."
    + SCHEMA
    + "\nRules:\n"
    "- To answer a data question, call the run_sql tool with ONE SQLite SELECT query.\n"
    "- If run_sql returns an error, read it and call run_sql again with a corrected query.\n"
    "- Only run_sql can see the data. Never invent numbers.\n"
    "- A question is VAGUE if it does not say which accounts, banks, time period, or\n"
    "  metric it means (e.g. 'show me the bad stuff', 'anything suspicious?').\n"
    "  For vague questions, do NOT run SQL. Ask ONE short clarifying question first.\n"
    "- If a question cannot be answered from these two tables, say it is out of scope.\n"
    "- Report only what the data shows. Do NOT label activity as 'structuring',\n"
    "  'laundering patterns', or otherwise infer criminal intent. State the numbers;\n"
    "  the is_laundering flag is a given label, not your conclusion.\n"
    "- Once you have the result, reply in 1-3 sentences, concrete with numbers."
)

TOOLS = [{
    "name": "run_sql",
    "description": "Run a read-only SQLite SELECT query against the AML database and return the rows.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "A single SQLite SELECT query."}
        },
        "required": ["query"],
    },
}]


@dataclass
class AgentResult:
    answer: str                 # the final natural-language reply
    queries: list = field(default_factory=list)   # every SQL string it ran
    final_result: object = None # the last successful query result (DataFrame) or None

    @property
    def ran_sql(self):
        return len(self.queries) > 0


def _execute_sql(query):
    """Run the SQL. Returns (output_text, is_error, dataframe_or_None). SELECT-only."""
    if not query.lstrip().upper().startswith("SELECT"):
        return "Error: only SELECT queries are allowed.", True, None
    try:
        con = sqlite3.connect(DB)
        df = pd.read_sql(query, con)
        con.close()
        if df.empty:
            return "Query ran successfully but returned no rows.", False, df
        return df.head(50).to_string(index=False), False, df
    except Exception as e:
        return f"SQL error: {e}", True, None


def run(question, max_turns=6, verbose=False):
    """Drive the agentic loop. Returns an AgentResult."""
    messages = [{"role": "user", "content": question}]
    queries, final_df = [], None

    for _ in range(max_turns):
        msg = client.messages.create(
            model=MODEL, max_tokens=700,
            system=SYSTEM, tools=TOOLS, messages=messages,
        )
        messages.append({"role": "assistant", "content": msg.content})

        if msg.stop_reason != "tool_use":
            answer = "".join(b.text for b in msg.content if b.type == "text")
            if verbose:
                print("Answer:\n" + answer)
            return AgentResult(answer, queries, final_df)

        results = []
        for block in msg.content:
            if block.type == "tool_use":
                q = block.input["query"]
                queries.append(q)
                if verbose:
                    print("SQL:\n" + q + "\n")
                output, is_error, df = _execute_sql(q)
                if not is_error and df is not None:
                    final_df = df
                if verbose and is_error:
                    print("(error -- agent will retry)\n" + output + "\n")
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                    "is_error": is_error,
                })
        messages.append({"role": "user", "content": results})

    return AgentResult("Stopped: hit the step limit.", queries, final_df)


def ask(question, max_turns=6):
    """Interactive helper: runs the agent and prints SQL + answer."""
    result = run(question, max_turns=max_turns, verbose=True)
    return result.final_result

def continue_chat(messages, max_turns=6):
    """Like run(), but takes an existing message history and returns the
    updated history plus the agent's reply. Enables multi-turn clarification."""
    for _ in range(max_turns):
        msg = client.messages.create(
            model=MODEL, max_tokens=700,
            system=SYSTEM, tools=TOOLS, messages=messages,
        )
        messages.append({"role": "assistant", "content": msg.content})

        if msg.stop_reason != "tool_use":
            answer = "".join(b.text for b in msg.content if b.type == "text")
            return messages, answer

        results = []
        for block in msg.content:
            if block.type == "tool_use":
                output, is_error, _ = _execute_sql(block.input["query"])
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": output, "is_error": is_error})
        messages.append({"role": "user", "content": results})
    return messages, "Stopped: hit the step limit."


if __name__ == "__main__":
    ask("Which 5 banks received the most flagged money?")
