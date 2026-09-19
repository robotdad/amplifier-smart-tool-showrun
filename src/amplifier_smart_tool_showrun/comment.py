"""Exact Stories comment capability. No mutation API calls; UI transport only."""

import re

from .errors import require
from .fixture import helper


class Comment:
    def __init__(self, target, grant, events, persist):
        self.target, self.grant = target, grant
        self.events, self.persist = events, persist
        self.opened = self.filled = self.submitting = self.sent = False
        self.draft_id = None
        self.sequence = -1
        self.drafts = 0
        self.confirmed = None

    async def preflight(self):
        state = await helper({**self.target, "operation": "review"})
        require(not state["annotations"] and not state["drafts"] and not state["feedback_grant"],
                "Comment takes require a fresh fixture with no comments, drafts or feedback grant.",
                "comment_precondition")

    def dispatch(self, kind):
        """Called only after durable UI attempt reservation, before real dispatch."""
        if kind == "open_comment":
            require(not self.opened, "Comment composer was already opened.", "comment_scope")
            self.opened = True
        elif kind == "fill_comment":
            require(self.opened and not self.filled and not self.submitting,
                    "Only one exact comment fill is authorized.", "comment_scope")
            self.filled = True
        elif kind == "submit_comment":
            require(self.filled and not self.submitting, "Submission already attempted.", "comment_scope")
            self.submitting = True

    def authorize(self, path, payload):
        """Synchronous validation + durable reservation before network dispatch.

        No awaits between checking and claiming: concurrent autosaves cannot race
        the counters or the one-submission fence. Failed/unknown effects never retry.
        """
        require(isinstance(payload, dict), "Expected exact comment payload.", "comment_scope")
        common = {"revision_id", "text", "anchor"}
        expected = common | ({"draft_id", "sequence"} if path == "save-draft" else {"request_id"})
        require(path in {"save-draft", "add-comment"} and set(payload) == expected
                and payload["revision_id"] == self.grant["revision_id"]
                and payload["anchor"] == {"kind": "story"},
                "Comment transport exceeded exact target/field authority.", "comment_scope")
        if path == "save-draft":
            draft_id, sequence = payload["draft_id"], payload["sequence"]
            require(self.opened and self.drafts < 8
                    and isinstance(draft_id, str)
                    and re.fullmatch(r"review-[0-9a-f-]{36}-" + re.escape(self.grant["revision_id"])
                                     + r'-\["story",null,null,null\]', draft_id)
                    and (self.draft_id is None or draft_id == self.draft_id)
                    and type(sequence) is int and self.sequence < sequence < 2**53
                    and payload["text"] in ({"", self.grant["text"]} if self.filled and not self.sent else {""}),
                    "Draft save exceeded bounded exact comment authority.", "comment_scope")
            self.draft_id, self.sequence = draft_id, sequence
            self.drafts += 1
        else:
            require(self.submitting and not self.sent and self.draft_id is not None
                    and payload["text"] == self.grant["text"]
                    and isinstance(payload["request_id"], str)
                    and re.fullmatch(r"[0-9a-f-]{36}", payload["request_id"]),
                    "Only one exact UI submission is authorized.", "comment_scope")
            self.sent = True
        self.events.append({"kind": "comment_transport", "route": path, "state": "dispatched",
                            "revision_id": payload["revision_id"],
                            **({"draft_id": self.draft_id, "sequence": payload["sequence"]}
                               if path == "save-draft" else {"request_id": payload["request_id"]}),
                            # Bounded allowed text only, never headers/tokens or arbitrary body.
                            "text": payload["text"]})
        self.persist()

    async def verify(self):
        if self.confirmed:
            return self.confirmed
        if not self.sent:
            return None
        state = await helper({**self.target, "operation": "review"})
        notes = state["annotations"]
        if not notes:
            return None
        require(not state["feedback_grant"] and len(notes) == 1, "Unexpected retained review state.",
                "comment_verification")
        note = notes[0]
        require(note["revision_id"] == self.grant["revision_id"] and note["text"] == self.grant["text"]
                and note["anchor"] == {"kind": "story"} and note["author"] == "user"
                and note["status"] == "awaiting_authority" and note["operation_id"] is None
                and note["result_revision"] is None and not note["responses"],
                "Exact retained comment without target generation did not verify.", "comment_verification")
        self.confirmed = {"method": "Stories.get_story public readback after real UI submission",
                          "story_id": self.grant["story_id"], "revision_id": note["revision_id"],
                          "comment_id": note["id"], "status": note["status"], "operation_id": None,
                          "text": note["text"], "count": 1}
        self.events.append({"kind": "comment_verified", **self.confirmed})
        self.persist()
        return self.confirmed