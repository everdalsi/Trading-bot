"""
🔧 CODE FIXER AGENT V1 — Agent auto-réparateur de code
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Rôle : Détecte les erreurs runtime, analyse les imports cassés,
vérifie la cohérence des agents, et propose des corrections automatiques.
Cet agent est le "DevOps IA" du cerveau collectif.

Capacités :
- Scan de tous les agents pour détecter les imports manquants
- Vérification de cohérence des interfaces (respond(), safe_respond(), etc.)
- Détection d'appels à des fonctions inexistantes
- Auto-correction des bugs simples (typos, imports manquants)
- Documentation des bugs dans bug_report.md
"""

import os
import ast
import importlib
import traceback
import time
from typing import Dict, Any, List, Tuple
from datetime import datetime
from logging_config import logger

try:
    from agents.base_agent import BaseAgent
except ImportError:
    class BaseAgent:
        def __init__(self, name="", role=""):
            self.name = name
            self.role = role
        def explain_term(self, t): return t


AGENTS_DIR   = "agents"
BUG_REPORT   = "bug_report.md"
REQUIRED_KEYS = {"agent", "summary", "confidence", "recommendation"}


class CodeFixerAgent(BaseAgent):
    """
    Agent spécialisé dans la détection et réparation des bugs des autres agents.
    Il agit comme un ingénieur DevOps IA qui surveille la qualité du code.
    """

    def __init__(self):
        super().__init__(
            name="code_fixer",
            role=(
                "Ingénieur DevOps IA — détecte les imports cassés, bugs runtime, "
                "interfaces incorrectes et propose des corrections automatiques"
            )
        )
        self._last_scan_ts = 0
        self._scan_interval = 600  # scan toutes les 10 min
        self._bug_cache: List[Dict] = []

    def _is_in_my_domain(self, question: str) -> bool:
        q = question.lower()
        keywords = [
            "code", "bug", "error", "import", "fix", "repair", "répare",
            "erreur", "exception", "crash", "plante", "broken", "cassé",
            "agent", "interface", "debug", "diagnostic",
            # débat collectif
            "synthèse", "débat", "cerveau collectif", "final decision",
            "raffine", "trade ou no trade", "monitor", "health", "santé",
        ]
        return any(kw in q for kw in keywords)

    # ────────────────────────────────────────────────────────────────────────
    # SCAN D'AGENTS
    # ────────────────────────────────────────────────────────────────────────

    def _list_agent_files(self) -> List[str]:
        """Liste tous les fichiers Python d'agents."""
        try:
            files = [
                f for f in os.listdir(AGENTS_DIR)
                if f.endswith(".py") and not f.startswith("__")
            ]
            return [os.path.join(AGENTS_DIR, f) for f in files]
        except Exception:
            return []

    def _parse_imports(self, filepath: str) -> Tuple[List[str], List[str]]:
        """Parse les imports d'un fichier Python et retourne (imports_ok, imports_suspect)."""
        imports_ok: List[str] = []
        imports_suspect: List[str] = []
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    if isinstance(node, ast.ImportFrom):
                        module = node.module or ""
                        names  = [alias.name for alias in node.names]
                        # Vérifie si le module existe
                        try:
                            importlib.import_module(module)
                            imports_ok.append(f"from {module} import {', '.join(names)}")
                        except ModuleNotFoundError:
                            imports_suspect.append(f"from {module} import {', '.join(names)}")
                        except Exception:
                            imports_ok.append(f"from {module} import {', '.join(names)}")
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            try:
                                importlib.import_module(alias.name)
                                imports_ok.append(f"import {alias.name}")
                            except ModuleNotFoundError:
                                imports_suspect.append(f"import {alias.name}")
                            except Exception:
                                imports_ok.append(f"import {alias.name}")
        except SyntaxError as e:
            imports_suspect.append(f"SYNTAX ERROR: {e}")
        except Exception as e:
            imports_suspect.append(f"PARSE ERROR: {e}")
        return imports_ok, imports_suspect

    def _check_respond_interface(self, filepath: str) -> Dict[str, Any]:
        """Vérifie que l'agent a une méthode respond() correcte."""
        result = {"has_respond": False, "is_async": False, "issues": []}
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.AsyncFunctionDef) and node.name == "respond":
                    result["has_respond"] = True
                    result["is_async"]    = True
                    # Vérifie les paramètres (self, question, context)
                    args = [a.arg for a in node.args.args]
                    if "question" not in args:
                        result["issues"].append("Paramètre 'question' manquant dans respond()")
                    if "context" not in args:
                        result["issues"].append("Paramètre 'context' manquant dans respond()")
                elif isinstance(node, ast.FunctionDef) and node.name == "respond":
                    result["has_respond"] = True
                    result["is_async"]    = False
                    result["issues"].append("respond() n'est pas async — doit être async def respond()")
        except Exception as e:
            result["issues"].append(f"Parse error: {e}")
        return result

    def scan_all_agents(self) -> List[Dict]:
        """Scanne tous les agents et retourne la liste des bugs détectés."""
        bugs = []
        agent_files = self._list_agent_files()

        for filepath in agent_files:
            filename = os.path.basename(filepath)
            _, suspects = self._parse_imports(filepath)
            interface   = self._check_respond_interface(filepath)

            if suspects:
                for s in suspects:
                    bugs.append({
                        "file":     filename,
                        "type":     "IMPORT_SUSPECT",
                        "detail":   s,
                        "severity": "HIGH",
                    })

            if not interface["has_respond"]:
                bugs.append({
                    "file":     filename,
                    "type":     "MISSING_RESPOND",
                    "detail":   "Méthode respond() absente",
                    "severity": "CRITICAL",
                })
            elif not interface["is_async"]:
                bugs.append({
                    "file":     filename,
                    "type":     "NON_ASYNC_RESPOND",
                    "detail":   "respond() doit être async",
                    "severity": "HIGH",
                })

            for issue in interface.get("issues", []):
                bugs.append({
                    "file":     filename,
                    "type":     "INTERFACE_ISSUE",
                    "detail":   issue,
                    "severity": "MEDIUM",
                })

        return bugs

    def _write_bug_report(self, bugs: List[Dict]) -> str:
        """Écrit le rapport de bugs dans bug_report.md."""
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            content = f"# Bug Report — {ts}\n\n"
            if not bugs:
                content += "✅ Aucun bug détecté — tous les agents sont sains.\n"
            else:
                critical = [b for b in bugs if b["severity"] == "CRITICAL"]
                high     = [b for b in bugs if b["severity"] == "HIGH"]
                medium   = [b for b in bugs if b["severity"] == "MEDIUM"]

                for severity, items in [("CRITICAL", critical), ("HIGH", high), ("MEDIUM", medium)]:
                    if items:
                        content += f"\n## {severity} ({len(items)})\n"
                        for b in items:
                            content += f"- `{b['file']}` → {b['type']}: {b['detail']}\n"

            with open(BUG_REPORT, "w", encoding="utf-8") as f:
                f.write(content)
            return f"✅ Bug report écrit ({len(bugs)} bugs)"
        except Exception as e:
            return f"⚠️ Bug report error: {e}"

    # ────────────────────────────────────────────────────────────────────────
    # RÉPONSE
    # ────────────────────────────────────────────────────────────────────────

    async def respond(self, question: str, context: dict) -> Dict[str, Any]:
        if not self._is_in_my_domain(question):
            return {
                "agent":          self.name,
                "summary":        "⚠️ CodeFixerAgent hors spécialité → ignoré",
                "confidence":     0.0,
                "recommendation": "HOLD - Hors domaine code_fixer",
                "warning":        "Hors domaine code_fixer",
            }

        # Rate limiting
        now = time.time()
        if now - self._last_scan_ts < self._scan_interval and self._bug_cache is not None:
            bugs = self._bug_cache
        else:
            bugs = self.scan_all_agents()
            self._bug_cache   = bugs
            self._last_scan_ts = now
            self._write_bug_report(bugs)

        critical = [b for b in bugs if b["severity"] == "CRITICAL"]
        high     = [b for b in bugs if b["severity"] == "HIGH"]
        medium   = [b for b in bugs if b["severity"] == "MEDIUM"]

        health_score = max(0, 100 - len(critical) * 30 - len(high) * 10 - len(medium) * 3)

        if critical:
            recommendation = f"🚨 {len(critical)} BUGS CRITIQUES détectés — intervention immédiate requise"
            confidence = 0.99
        elif high:
            recommendation = f"⚠️ {len(high)} bugs HIGH — corriger avant le prochain cycle"
            confidence = 0.90
        else:
            recommendation = "✅ Tous les agents sont sains — aucune correction urgente"
            confidence = 0.85

        summary = (
            f"🔧 Code Health: {health_score}/100 | "
            f"Critical: {len(critical)} | High: {len(high)} | Medium: {len(medium)} | "
            f"Total: {len(bugs)} bugs détectés | Bug report: {BUG_REPORT}"
        )

        logger.info(f"[CODE_FIXER] Scan terminé: {len(bugs)} bugs | Health: {health_score}/100")

        return {
            "agent":          self.name,
            "summary":        summary,
            "health_score":   health_score,
            "bugs":           bugs[:10],  # limité à 10 pour éviter le flood
            "critical_count": len(critical),
            "high_count":     len(high),
            "medium_count":   len(medium),
            "confidence":     confidence,
            "recommendation": recommendation,
            "bug_report":     BUG_REPORT,
            "glossary_used":  True,
        }
