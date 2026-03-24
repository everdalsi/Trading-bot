from agents.base_agent import BaseAgent

class SupervisorAgent(BaseAgent):
    def __init__(self):
        super().__init__("supervisor", "Synthèse finale")

    async def respond(self, question, context):
        agent_outputs = context.get("agent_outputs", [])

        summary = "\n".join([f"{a['agent']}: {a['summary']}" for a in agent_outputs])

        return {
            "agent": self.name,
            "summary": "Synthèse globale",
            "arguments": [summary],
            "risks": ["dépend de la qualité des agents"],
            "confidence": 0.75,
            "recommendation": "Décision basée sur consensus"
        }
