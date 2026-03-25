from dotenv import load_dotenv
import os
from langchain_groq import ChatGroq

load_dotenv()

MAIN_OBJECTIVE = """
Améliore constamment le bot de trading et tous ses agents pour maximiser le profit tout en minimisant le drawdown.
Reste simple, fiable, ultra-résilient et utile pour l'utilisateur.
Priorise la sécurité du capital, l'apprentissage continu via la DB infinie, et la clarté des résumés Telegram.
"""

def get_llm(temperature=0.4):
    return ChatGroq(
        model="llama3-70b-8192",
        temperature=temperature,
        groq_api_key=os.getenv("GROQ_API_KEY"),
        max_tokens=4096
    )
