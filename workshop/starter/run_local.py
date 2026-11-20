"""Runs the Creative Director in-process against local specialists (see run_local.sh) and prints each A2A call,
the quality-gate decisions and the final campaign."""
import asyncio, logging, os, sys, time
sys.path.insert(0, os.path.join(os.getcwd(), "agents"))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.getcwd(), ".env"), override=False)
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(message)s")
logging.getLogger("ai_creative_studio.creative_director.quality_gate").setLevel(logging.INFO)
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from creative_director.agent import root_agent

BRIEF = """Create a complete Instagram campaign for:
- Product: EcoFlow Smart Water Bottle (tracks hydration, keeps drinks cold 24h)
- Target Audience: Health-conscious millennials, 25-35 years old
- Platform: Instagram
- Goal: Brand awareness + drive website traffic
- Brand Voice: Motivational, clean, science-backed
- Budget: $3,000
- Timeline: Launch in 2 weeks"""

async def main():
    sessions = InMemorySessionService()
    runner = Runner(agent=root_agent, app_name="studio", session_service=sessions)
    s = await sessions.create_session(app_name="studio", user_id="u")
    t0 = time.time(); final = ""
    async for ev in runner.run_async(user_id="u", session_id=s.id,
                                     new_message=types.Content(role="user", parts=[types.Part(text=BRIEF)])):
        for part in (ev.content.parts if ev.content else []) or []:
            if part.function_call:
                print(f"[{time.time()-t0:5.0f}s] -> {part.function_call.name}", flush=True)
            elif part.function_response:
                r = str(part.function_response.response)
                print(f"[{time.time()-t0:5.0f}s] <- {part.function_response.name}: {r[:160]!r}", flush=True)
            elif part.text and ev.is_final_response():
                final = part.text
    print(f"\n=== DONE in {time.time()-t0:.0f}s ===\n{final[:3000]}")
    st = (await sessions.get_session(app_name="studio", user_id="u", session_id=s.id)).state
    print("verdict:", st.get("critic_verdict"), "rounds:", st.get("critic_review_rounds"))

asyncio.run(main())
