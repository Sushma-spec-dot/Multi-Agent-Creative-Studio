import logging
import os

from google.adk.agents import Agent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.plugins.logging_plugin import LoggingPlugin
from google.adk.tools import FunctionTool
from google.adk.tools.agent_tool import AgentTool
try:
    from .retry import RETRY_CONFIG
    from .display_image_tool import display_image
    from .get_image_links_tool import get_image_links
except ImportError:
    from retry import RETRY_CONFIG
    from display_image_tool import display_image
    from get_image_links_tool import get_image_links

try:
    from .quality_gate import MAX_REVIEW_ROUNDS, after_tool, before_tool
except ImportError:
    from quality_gate import MAX_REVIEW_ROUNDS, after_tool, before_tool

try:
    from .prompt import SYSTEM_INSTRUCTION_TEMPLATE
except ImportError:
    from prompt import SYSTEM_INSTRUCTION_TEMPLATE  # direct execution fallback

logger = logging.getLogger("ai_creative_studio.creative_director")
logger.setLevel(logging.INFO)


def create_creative_director():
    """
    Create the Creative Director orchestrator.
    Reads specialist URLs from environment variables at runtime.
    """
    # Read specialist URLs from environment
    copywriter_url = os.getenv("COPYWRITER_AGENT_URL")
    designer_url = os.getenv("DESIGNER_AGENT_URL")
    strategist_url = os.getenv("STRATEGIST_AGENT_URL")
    critic_url = os.getenv("CRITIC_AGENT_URL")
    pm_url = os.getenv("PM_AGENT_URL")

    available_agents_list = []
    agent_tools = [
        FunctionTool(func=display_image),
        FunctionTool(func=get_image_links),
    ]

    # Each specialist is a separate service. The Creative Director only knows its address (from the
    # environment) and reads its agent card (/.well-known/agent.json) to learn what it does. RemoteA2aAgent
    # talks to it over the A2A protocol, and AgentTool makes it a tool the orchestrator can call - "agent as a
    # tool". No specialist code lives here, so each one can be updated or scaled on its own.
    specialists = [  # (name, URL, what the orchestrator is told it does)
        ("brand_strategist", strategist_url, "Researches market trends, competitors, and audience insights"),
        ("copywriter", copywriter_url, "Writes 3 Instagram caption variations from the research"),
        ("designer", designer_url, "Creates one visual concept per caption and generates the real image (gcs_uri)"),
        ("critic", critic_url, "Reviews captions and images; returns APPROVED or NEEDS_REVISION"),
        ("project_manager", pm_url, "Builds the timeline and task list for the approved campaign"),
    ]
    for name, url, what in specialists:
        if not url:
            logger.warning("%s: no URL set, so it is not available", name)
            continue
        available_agents_list.append(f"- **{name}**: {what}")
        remote = RemoteA2aAgent(
            name=name,
            description=what,
            agent_card=f"{url.rstrip('/')}/.well-known/agent.json",
        )
        agent_tools.append(AgentTool(agent=remote))

    available_agents_text = (
        "\n".join(available_agents_list)
        if available_agents_list
        else "No specialist agents configured. Set agent URLs in environment variables."
    )

    system_instruction = SYSTEM_INSTRUCTION_TEMPLATE.format(
        available_agents=available_agents_text,
        max_review_rounds=MAX_REVIEW_ROUNDS,
    )

    from google.genai import types
    generation_config = types.GenerateContentConfig(
        max_output_tokens=20000,
        temperature=0.2,
        http_options=types.HttpOptions(
            retry_options=RETRY_CONFIG,
            timeout=120_000,  # 120 second timeout for model calls
        ),
    )

    agent = Agent(
        name="creative_director",
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        description="Creative Director orchestrator that coordinates specialist agents",
        instruction=system_instruction,
        tools=agent_tools,
        generate_content_config=generation_config,
        # The quality gate in code: read the critic's verdict, and block the project_manager until APPROVED.
        before_tool_callback=before_tool,
        after_tool_callback=after_tool,
    )

    # Five specialists send long answers (research, captions, image concepts, reviews, a plan). Event compaction
    # summarizes older turns every few steps, so the conversation stays under the model's token limit. The
    # summarizer uses the same model as the agents (from the environment, not hard-coded).
    from google.adk.apps import App
    from google.adk.apps.app import EventsCompactionConfig
    from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
    from google.adk.models import Gemini

    compaction_config = EventsCompactionConfig(
        summarizer=LlmEventSummarizer(llm=Gemini(model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))),
        compaction_interval=3,
        overlap_size=1,
    )
    app = App(
        name="creative_director",
        root_agent=agent,
        events_compaction_config=compaction_config,
        plugins=[LoggingPlugin()],
    )
    return agent, app


root_agent, root_app = create_creative_director()
