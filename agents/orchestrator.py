class Orchestrator:
    def __init__(self):
        self.analyst = AnalystAgent()
        self.risk = RiskAgent()
        self.trader = TraderAgent()
        self.supervisor = SupervisorAgent()

    async def run(self, market_data, memory):

        context = {
    "symbol": market_data.get("symbol"),
    "market_data": market_data,
    "memory": memory
}

        # 1. ANALYST
        analysis = await self.analyst.respond("analyze", context)
        context["analysis"] = analysis

        # 2. RISK
        risk = await self.risk.respond("assess_risk", context)
        context["risk"] = risk

        # 3. TRADER
        decision = await self.trader.respond("decide", context)
        context["decision"] = decision

        # 4. SUPERVISOR 🔥
        final = await self.supervisor.respond("validate", context)

        return final
