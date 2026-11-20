import datetime
import logging
import os

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.tools.google_search_tool import google_search
try:
    from .retry import GENERATE_CONTENT_CONFIG
except ImportError:
    from retry import GENERATE_CONTENT_CONFIG

load_dotenv()

logger = logging.getLogger("ai_creative_studio.brand_strategist")

# The Brand Strategist is RESEARCH ONLY: audience, 2-3 competitors, 3-5 trends, in four labelled sections.
# Today's date goes in when the service starts (the A2A agent card needs the instruction as text).
def system_instruction(context=None) -> str:
    return f"""
You are the Brand Strategist for an AI Creative Studio.

Your responsibility is RESEARCH ONLY.

Today's date is {datetime.date.today().strftime("%B %d, %Y")}.

When given a marketing campaign brief:

1. TARGET AUDIENCE RESEARCH
   - Use Google Search to research the target audience.
   - Identify audience interests, needs, and challenges.
   - Always include the current year in search queries.

2. COMPETITIVE ANALYSIS
   - Research 2-3 relevant competitor brands.
   - Identify their positioning and marketing approaches.

3. TRENDING TOPICS
   - Identify 3-5 relevant trends in the product category.
   - Use recent search findings.

4. STRATEGIC INSIGHTS
   - Summarize useful findings for the Copywriter and Designer.

Always return your response using these headings:

**Audience Insights:**
**Competitive Analysis:**
**Trending Topics:**
**Key Strategic Insights:**

IMPORTANT RULES:
- You are a RESEARCH-ONLY agent.
- DO NOT write Instagram captions or marketing copy.
- DO NOT generate images or design concepts.
- Use Google Search for research.
- Always include the current year in search queries.
- The Creative Director coordinates the next agents.
"""


SYSTEM_INSTRUCTION = system_instruction()

root_agent = Agent(
    name="brand_strategist",
    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    instruction=SYSTEM_INSTRUCTION,
    description="Researches audiences, competitors, and marketing trends for Instagram campaigns.",
    tools=[google_search],
    generate_content_config=GENERATE_CONTENT_CONFIG,
)

logger.info("Brand Strategist agent created")


if __name__ == "__main__":
    import uvicorn
    from google.adk.a2a.utils.agent_to_a2a import to_a2a

    PORT = int(os.getenv("PORT", "8082"))
    HOST = os.getenv("HOST", "0.0.0.0")
    PUBLIC_HOST = os.getenv("PUBLIC_HOST", "localhost")
    PUBLIC_PORT = int(os.getenv("PUBLIC_PORT", str(PORT)))
    PROTOCOL = os.getenv("PROTOCOL", "http")

    a2a_app = to_a2a(root_agent, host=PUBLIC_HOST, port=PUBLIC_PORT, protocol=PROTOCOL)

    logger.info(f"Starting Brand Strategist on {PROTOCOL}://{HOST}:{PORT}")
    logger.info(f"Agent card: {PROTOCOL}://{HOST}:{PORT}/.well-known/agent.json")

    uvicorn.run(a2a_app, host=HOST, port=PORT)
