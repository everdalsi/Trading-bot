class BaseAgent:
    def __init__(self, name, role):
        self.name = name
        self.role = role

    async def respond(self, question, context):
        return {
            "agent": self.name,
            "summary": "Pas encore implémenté",
            "arguments": [],
            "risks": [],
            "confidence": 0.0,
            "recommendation": "N/A"
        }
