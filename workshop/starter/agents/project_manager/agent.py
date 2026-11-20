import datetime
import json
import logging
import os

from google.adk.agents import Agent
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from dotenv import load_dotenv
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from mcp import StdioServerParameters
try:
    from .retry import GENERATE_CONTENT_CONFIG
except ImportError:
    from retry import GENERATE_CONTENT_CONFIG

load_dotenv()

logger = logging.getLogger("ai_creative_studio.project_manager")

DESCRIPTION = ("Project manager: turns an approved Instagram campaign into a dated timeline, task list, budget and "
               "milestones, and syncs the project and tasks to Notion when it is configured")


def handle_notion_error(
    tool: BaseTool,
    args: dict,
    tool_context: ToolContext,
    tool_response: dict,
) -> dict | None:
    """Intercept Notion API errors and replace the raw stack trace with a clean message."""
    if not tool.name.startswith("API-"):
        return None

    content = (tool_response.get("content") or [{}])[0].get("text", "")
    try:
        data = json.loads(content)
    except Exception:
        return None

    status = data.get("status")
    if status not in (400, 404):
        return None

    message = data.get("message", "")
    code = data.get("code", "")
    logger.warning("Notion %s (%s) on %s — injecting recovery hint", status, code, tool.name)

    if status == 404 and code == "object_not_found":
        # The misleading Notion message blames sharing/permissions, but the real
        # cause is passing a database ID as page_id in the parent object.
        message = (
            "object_not_found: you passed a database ID as page_id. "
            'Use {"parent": {"database_id": "<id>"}} not {"parent": {"page_id": "<id>"}}.'
        )

    return {
        "content": [{
            "type": "text",
            "text": f"Notion {status} ({code}) on {tool.name}: {message}\n\nRetry with corrected parameters.",
        }]
    }


def get_system_instruction(project_database_id=None, tasks_database_id=None):
    # notion_section is empty when Notion is not configured, so the agent
    # receives no tool instructions for capabilities it doesn't have.
    notion_section = (
        f"""
Projects database ID: {project_database_id}
Tasks database ID: {tasks_database_id}

Also persist the project and tasks to these Notion databases using the available Notion tools.
Notion tools follow the pattern `API-<operation>` — use their exact names as listed in the tool
manifest. Use them directly — never wrap in `print()` or prefix with `default_api.`

Before creating anything, use the available tools to discover the schema of each database.
Only use property names and types that actually exist in the schema you discover.

Property rules:
- Always set the database parent using `database_id` — never `page_id`
- Never set "people" or "person" type properties — integration tokens cannot assign users; skip them
- For "relation" type properties linking tasks to the project: set ONLY {{"relation": [{{"id": "<project-page-id>"}}]}}.
  Never set sub-fields like name, state, start, lat on the relation - those are read-only rollups.
  If a task creation fails with a validation_error on a relation property, immediately retry
  creating that task WITHOUT the relation property entirely.
- Only set properties whose type you can identify from the schema response; if a property type
  is unclear after reading the schema, skip it and note it in the Notion Status

If any Notion call fails, continue — the text timeline is always the primary deliverable.
Write your complete response AFTER all Notion operations are done (or have failed).

If image HTTPS links are provided in the input (under "Generated Images" from the Creative
Director), add them to the Notion project page body as a bulleted list under a
"Generated Images" heading after creating the project page.
"""
        if project_database_id
        else ""
    )

    # The plan starts from today's date. The text timeline always comes first: Notion is only an extra copy,
    # so the plan never depends on it.
    today = datetime.date.today()
    return f"""You are the Project Manager of an AI Creative Studio. The conversation above holds an APPROVED
Instagram campaign: the Brand Strategist's research, the Copywriter's 3 captions, the Designer's images and the
Critic's review. Turn it into an actionable plan for launching that campaign.

Today's date is {today.strftime("%A, %B %d, %Y")}. Every date you write is a real calendar date counted from today.

Plan the campaign in four phases, in this order:
1. Strategy - confirm the audience, the key messages and the posting calendar
2. Creation - final edits to the captions and images (use the Critic's remaining suggestions, if any)
3. Review - brand and legal check, scheduling the posts in Instagram
4. Launch - publish the 3 posts on separate days, reply to comments, measure results after 7 days

Rules:
- Use the real campaign: name the actual captions (by their theme or first words) and images (by concept name)
  in the tasks. Do not invent new captions, images or research.
- Every task has an owner (a role: Brand Strategist, Copywriter, Designer, Social Media Manager, Project
  Manager, Analyst), a deadline (a date) and a status (To Do, unless the campaign shows it's done: captions,
  images and review are Done).
- Budget: give a realistic small-business estimate in USD by category (creative, paid promotion, tools,
  contingency) with a total; say it is an estimate.
- Your text plan is the primary deliverable: always write it in full, even if a tool fails.
- Do not rewrite the captions or create images: that is not your job.

Answer in exactly this format:

**Project Timeline:**
[each phase: name, start date - end date, what happens]

**Task List:**
| Task | Owner | Deadline | Status |
|---|---|---|---|
[one row per task, 8-14 tasks]

**Budget Breakdown:**
[category: amount, ... Total: amount (estimate)]

**Milestones:**
[key checkpoints with dates, e.g. "Captions final - <date>", "Post 1 live - <date>", "7-day results - <date>"]

**Notion Status:**
{"[what was created in Notion, or what failed and why]" if project_database_id else "Notion not configured - text timeline only"}
{notion_section}"""


def create_project_manager_agent():
    """Create the Project Manager agent, with Notion MCP if credentials are set."""
    notion_token           = os.getenv("NOTION_TOKEN")
    notion_project_db_id   = os.getenv("NOTION_PROJECT_DATABASE_ID")
    notion_tasks_db_id     = os.getenv("NOTION_TASKS_DATABASE_ID")

    if not notion_token or not notion_project_db_id or not notion_tasks_db_id:
        logger.warning("Notion credentials not set — running without Notion integration")

        # No Notion: a plain agent that writes the text timeline.
        return Agent(
            name="project_manager",
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            generate_content_config=GENERATE_CONTENT_CONFIG,
            instruction=get_system_instruction(),
            description=DESCRIPTION,
        )

    else:
        logger.info(f"Notion configured — projects database: {notion_project_db_id}, tasks database: {notion_tasks_db_id}")

        # Notion through MCP: ADK starts the ready-made Notion MCP server (installed in the Dockerfile) as a
        # subprocess and turns every Notion API operation it offers into a tool (API-post-page, ...). No custom
        # Notion code here; the token goes to the server only through its environment.
        server_params = StdioServerParameters(
            command="notion-mcp-server",
            env={"NOTION_TOKEN": notion_token, "PATH": os.environ.get("PATH", "")},
        )
        notion_toolset = McpToolset(
            connection_params=StdioConnectionParams(server_params=server_params, timeout=30.0)
        )

        return Agent(
            name="project_manager",
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            generate_content_config=GENERATE_CONTENT_CONFIG,
            after_tool_callback=handle_notion_error,    # Notion 400/404 errors -> a hint the agent can act on
            instruction=get_system_instruction(
                project_database_id=notion_project_db_id, tasks_database_id=notion_tasks_db_id),
            description=DESCRIPTION,
            tools=[notion_toolset],
        )


root_agent = create_project_manager_agent()
logger.info("Project Manager agent created")


if __name__ == "__main__":
    import uvicorn
    from google.adk.a2a.utils.agent_to_a2a import to_a2a

    PORT = int(os.getenv("PORT", "8080"))
    HOST = os.getenv("HOST", "0.0.0.0")
    PUBLIC_HOST = os.getenv("PUBLIC_HOST", "localhost")
    PUBLIC_PORT = int(os.getenv("PUBLIC_PORT", str(PORT)))
    PROTOCOL = os.getenv("PROTOCOL", "http")

    a2a_app = to_a2a(root_agent, host=PUBLIC_HOST, port=PUBLIC_PORT, protocol=PROTOCOL)

    logger.info(f"Starting Project Manager on {PROTOCOL}://{HOST}:{PORT}")
    logger.info(f"Agent card: {PROTOCOL}://{HOST}:{PORT}/.well-known/agent.json")

    uvicorn.run(a2a_app, host=HOST, port=PORT)
