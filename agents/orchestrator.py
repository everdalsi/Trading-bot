from agents.analyst_agent import AnalystAgent
from agents.risk_agent import RiskAgent
from agents.trader_agent import TraderAgent
from agents.supervisor_agent import SupervisorAgent

class Orchestrator:
    def __init__(self):
        self.analyst = AnalystAgent()
        self.risk = RiskAgent()
        self.trader = TraderAgent()
        self.supervisor = SupervisorAgent()

    async def ask_all(self, question, context):
        responses = []

        for agent in [self.analyst, self.risk, self.trader]:
            res = await agent.respond(question, context)
            responses.append(res)

        context["agent_outputs"] = responses

        final = await self.supervisor.respond(question, context)

        return responses, final
