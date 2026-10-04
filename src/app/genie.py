"""Genie Conversation API client for the Ask tab (FR-12). Runs as the app's service principal."""

import datetime as dt
import os
from dataclasses import dataclass, field

import pandas as pd
from databricks.sdk import WorkspaceClient

TIMEOUT = dt.timedelta(seconds=120)


@dataclass
class Answer:
    conversation_id: str | None
    text: str = ""
    description: str = ""
    sql: str = ""
    table: pd.DataFrame | None = None
    suggestions: list[str] = field(default_factory=list)
    error: str = ""


def answer_from_message(msg, fetch_result):
    """Build an Answer from a GenieMessage. `fetch_result(attachment_id)` returns a StatementResponse."""
    answer = Answer(conversation_id=msg.conversation_id)
    if msg.error is not None:
        answer.error = getattr(msg.error, "error", None) or str(msg.error)
    for att in msg.attachments or []:
        if att.text is not None and att.text.content:
            answer.text = att.text.content
        if att.query is not None:
            answer.sql = att.query.query or ""
            answer.description = att.query.description or ""
            answer.table = table_from_statement(fetch_result(att.attachment_id))
        if att.suggested_questions is not None:
            answer.suggestions = list(att.suggested_questions.questions or [])
    if not (answer.text or answer.sql or answer.error):
        answer.error = f"Genie returned no answer (status {msg.status})."
    return answer


def table_from_statement(resp):
    if resp is None or resp.manifest is None or resp.manifest.schema is None:
        return None
    cols = [c.name for c in resp.manifest.schema.columns or []]
    rows = (resp.result.data_array if resp.result is not None else None) or []
    return pd.DataFrame(rows, columns=cols)


class Genie:
    def __init__(self):
        self.space_id = os.environ["GENIE_SPACE_ID"]
        self.w = WorkspaceClient()

    def ask(self, question, conversation_id=None):
        if conversation_id:
            msg = self.w.genie.create_message_and_wait(
                self.space_id, conversation_id, question, timeout=TIMEOUT
            )
        else:
            msg = self.w.genie.start_conversation_and_wait(self.space_id, question, timeout=TIMEOUT)

        def fetch(attachment_id):
            return self.w.genie.get_message_attachment_query_result(
                self.space_id, msg.conversation_id, msg.message_id, attachment_id
            ).statement_response

        return answer_from_message(msg, fetch)
