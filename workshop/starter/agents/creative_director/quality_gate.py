"""The quality gate, in code: the Creative Director reads the Critic's verdict with a parser (not by "looking"),
and the Project Manager tool is blocked until the Critic has APPROVED the campaign.

How it works (two ADK tool callbacks on the orchestrator):
  after the critic tool runs     -> parse_verdict() reads POSTS / VISUALS / OVERALL and saves it in the session state
  before the project_manager tool -> if the last verdict isn't approved, the call is skipped and the orchestrator
                                     gets back what to fix and which specialist to re-run (copywriter / designer),
                                     then must ask the critic again
A quality bar (QUALITY_BAR, default 8 out of 10) is checked in code too: a part the Critic marked APPROVED but
scored below the bar counts as NEEDS_REVISION, so "good enough" drafts still get one more pass.
A limit (MAX_REVIEW_ROUNDS, default 3 reviews) stops endless loops: after the last allowed review the plan is made
from the best version so far, and the plan is marked as not approved.
"""
import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger("ai_creative_studio.creative_director.quality_gate")

MAX_REVIEW_ROUNDS = int(os.getenv("MAX_REVIEW_ROUNDS", "3"))
QUALITY_BAR = float(os.getenv("QUALITY_BAR", "8"))
STATE_VERDICT, STATE_ROUNDS = "critic_verdict", "critic_review_rounds"


def _field(text: str, section: str, name: str) -> Optional[str]:
    """The value of '- <name>: ...' inside the '**<section>:**' block (brackets and bold tolerated)."""
    block = re.search(rf"\**\s*{section}\s*:?\**(.*?)(?=\n\s*\**\s*[A-Z][A-Z ]+REVIEW|\n\s*\**\s*OVERALL|\Z)",
                      text, re.S | re.I)
    if not block:
        return None
    line = re.search(rf"-\s*\**{name}\**\s*:\s*\[?\**\s*([^\n\]]*)", block.group(1), re.I)
    return line.group(1).strip(" *[]") if line else None


def _status(value: Optional[str]) -> Optional[str]:
    for status in ("NEEDS_REVISION", "NOT_REVIEWED", "APPROVED"):
        if value and status in value.upper().replace(" ", "_"):
            return status
    return None


def _score(value: Optional[str]) -> Optional[float]:
    """'8/10', '[7.5/10]', '9' -> 8.0 / 7.5 / 9.0; 'N/A' or nothing -> None."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:/\s*10)?", value or "")
    return float(match.group(1)) if match else None


def parse_verdict(text: str, bar: Optional[float] = None) -> dict:
    """The Critic's answer -> {posts, visuals, all_approved, approved, priority, parsed}.
    'approved' is True only when nothing is NEEDS_REVISION (NOT_REVIEWED counts as approved, as the Critic's
    instruction says) and All Approved isn't NO."""
    text = text or ""
    posts = _status(_field(text, "POSTS REVIEW", "Status"))
    visuals = _status(_field(text, "VISUALS REVIEW", "Status"))
    all_approved = (_field(text, "OVERALL ASSESSMENT", "All Approved") or "").upper()
    all_approved = "YES" if all_approved.startswith("YES") else "NO" if all_approved.startswith("NO") else None
    parsed = posts is not None and visuals is not None and all_approved is not None
    bar = QUALITY_BAR if bar is None else bar
    scores = {"posts": _score(_field(text, "POSTS REVIEW", "Score")),
              "visuals": _score(_field(text, "VISUALS REVIEW", "Score")),
              "overall": _score(_field(text, "OVERALL ASSESSMENT", "Overall Score"))}
    # The bar, in code: an APPROVED part scored under it is sent back like a NEEDS_REVISION one
    below_bar = []
    if posts == "APPROVED" and scores["posts"] is not None and scores["posts"] < bar:
        posts = "NEEDS_REVISION"
        below_bar.append(f"posts scored {scores['posts']:g}/10, under the {bar:g}/10 bar")
    if visuals == "APPROVED" and scores["visuals"] is not None and scores["visuals"] < bar:
        visuals = "NEEDS_REVISION"
        below_bar.append(f"visuals scored {scores['visuals']:g}/10, under the {bar:g}/10 bar")
    approved = parsed and "NEEDS_REVISION" not in (posts, visuals) and all_approved == "YES"
    return {"posts": posts, "visuals": visuals, "all_approved": all_approved, "approved": approved,
            "scores": scores, "bar": bar, "below_bar": below_bar,
            "parsed": parsed, "priority": _field(text, "OVERALL ASSESSMENT", "Priority Revisions") or "",
            "posts_suggestions": _field(text, "POSTS REVIEW", "Suggestions") or "",
            "visuals_suggestions": _field(text, "VISUALS REVIEW", "Suggestions") or ""}


def _text(response: Any) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        return str(response.get("result") or response.get("text") or response)
    return str(response or "")


def after_tool(tool, args: dict, tool_context, tool_response: Any) -> Optional[dict]:
    """After the critic answers: parse and remember its verdict (the response itself is passed on unchanged)."""
    if tool.name != "critic":
        return None
    verdict = parse_verdict(_text(tool_response))
    rounds = tool_context.state.get(STATE_ROUNDS, 0) + 1
    tool_context.state[STATE_VERDICT] = verdict
    tool_context.state[STATE_ROUNDS] = rounds
    logger.info("Critic review %d/%d: posts=%s (%s) visuals=%s (%s) all_approved=%s, bar %g -> %s%s", rounds,
                MAX_REVIEW_ROUNDS, verdict["posts"], verdict["scores"]["posts"], verdict["visuals"],
                verdict["scores"]["visuals"], verdict["all_approved"], verdict["bar"],
                "APPROVED" if verdict["approved"] else "NEEDS_REVISION",
                f" ({'; '.join(verdict['below_bar'])})" if verdict["below_bar"] else "")
    return None


def before_tool(tool, args: dict, tool_context) -> Optional[dict]:
    """A new campaign (brand_strategist) starts the review count again. The project_manager only runs once the
    Critic approved; otherwise the call is skipped and this tells the orchestrator what to do instead."""
    if tool.name == "brand_strategist":
        tool_context.state[STATE_VERDICT], tool_context.state[STATE_ROUNDS] = None, 0
        return None
    if tool.name != "project_manager":
        return None
    verdict, rounds = tool_context.state.get(STATE_VERDICT), tool_context.state.get(STATE_ROUNDS, 0)
    if verdict and verdict["approved"]:
        return None
    if rounds >= MAX_REVIEW_ROUNDS:
        logger.warning("Quality gate: %d reviews without approval; planning with the latest version", rounds)
        args["request"] = (f"NOTE: after {rounds} Critic reviews the campaign is still NOT APPROVED "
                           f"(latest priority fix: {verdict.get('priority') if verdict else 'unknown'}). "
                           "Say so at the top of the plan and add that fix as the first task.\n\n"
                           + str(args.get("request", "")))
        return None
    if not verdict:
        return {"result": "BLOCKED by the quality gate: the critic hasn't reviewed this campaign yet. Call the "
                          "critic with the captions and every image gcs_uri first."}
    if not verdict["parsed"]:
        return {"result": "BLOCKED by the quality gate: the critic's answer didn't follow its required format "
                          "(POSTS REVIEW / VISUALS REVIEW / OVERALL ASSESSMENT). Call the critic again."}
    redo = [name for name, status in (("copywriter", verdict["posts"]), ("designer", verdict["visuals"]))
            if status == "NEEDS_REVISION"] or ["copywriter"]
    parts = (*verdict.get("below_bar", []), verdict["priority"], verdict["posts_suggestions"] if "copywriter" in redo else "",
             verdict["visuals_suggestions"] if "designer" in redo else "")
    feedback = "; ".join(p.strip().rstrip(".") for p in parts if p and p.strip().lower() != "none")
    logger.info("Quality gate: project_manager blocked (review %d/%d); re-run %s", rounds, MAX_REVIEW_ROUNDS,
                " and ".join(redo))
    return {"result": f"BLOCKED by the quality gate: the critic said NEEDS_REVISION (review {rounds} of "
                      f"{MAX_REVIEW_ROUNDS}). Re-run {' then '.join(redo)} with this feedback: {feedback}. "
                      "Then call the critic again with the revised captions and images. Only an APPROVED "
                      "campaign goes to the project_manager."}
