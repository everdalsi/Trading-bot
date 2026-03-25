from crewai.tools import BaseTool
import os

def get_git_repo():
    try:
        from git import Repo
        return Repo("/workspace")
    except Exception:
        print("⚠️ Git non disponible pour l’instant (cache Railway)")
        return None

class EditBotFileTool(BaseTool):
    name: str = "EditBotFile"
    description: str = "Modifie bot.py ou tout autre fichier du projet avec du nouveau code"

    def _run(self, new_code: str, filename: str = "bot.py"):
        try:
            path = f"/workspace/{filename}"
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_code)
            return f"✅ {filename} mis à jour avec succès"
        except Exception as e:
            return f"❌ Erreur édition {filename}: {e}"

class GitPushTool(BaseTool):
    name: str = "GitPushTool"
    description: str = "Commit + push sur GitHub → déclenche redéploiement Railway automatique"

    def _run(self, commit_message: str):
        repo = get_git_repo()
        if not repo:
            return "⚠️ Git non disponible → push ignoré (cache Railway en cours)"
        try:
            repo.git.add(A=True)
            repo.index.commit(commit_message)
            origin = repo.remote(name="origin")
            origin.push()
            return f"✅ Push réussi : {commit_message} → Railway redéploie"
        except Exception as e:
            return f"❌ Git push échoué : {e}"
