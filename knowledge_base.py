"""
🔍 KNOWLEDGE BASE V1.1 — Version stable
Analyse les PDFs et les stocke dans ChromaDB sans interrompre le bot en cas d'erreur.
"""

import os
from pathlib import Path
from typing import List, Dict, Any

import chromadb
from chromadb.utils import embedding_functions
from pypdf import PdfReader

# Import du logger centralisé
from logging_config import logger

class KnowledgeBase:
    def __init__(self, db_path: str = "knowledge_db", collection_name: str = "trading_knowledge"):
        try:
            self.client = chromadb.PersistentClient(path=db_path)
            self.embedding_function = embedding_functions.DefaultEmbeddingFunction()
            
            self.collection = self.client.get_or_create_collection(
                name=collection_name,
                embedding_function=self.embedding_function,
                metadata={"hnsw:space": "cosine"}
            )
            logger.info("✅ KnowledgeBase initialisée (ChromaDB)")
        except Exception as e:
            # On log l'erreur mais on ne fait pas de 'raise' pour permettre au bot de démarrer
            logger.error(f"⚠️ Erreur critique ChromaDB : {e}. Le bot fonctionnera sans RAG.")
            self.collection = None

    def load_pdfs_from_root(self) -> int:
        if self.collection is None:
            return 0

        root = Path(".")
        pdf_files = list(root.glob("*.pdf")) + list(root.glob("*.PDF"))
        
        if not pdf_files:
            logger.info("ℹ️ Aucun PDF théorique trouvé à la racine.")
            return 0

        total_chunks = 0
        for pdf_path in pdf_files:
            try:
                reader = PdfReader(str(pdf_path))
                full_text = ""
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        full_text += text + "\n\n"

                if not full_text.strip():
                    continue

                chunks = self._simple_split(full_text)
                ids = [f"{pdf_path.stem}_ch_{i}" for i in range(len(chunks))]
                metadatas = [{"source": pdf_path.name, "chunk": i} for i in range(len(chunks))]

                self.collection.add(documents=chunks, metadatas=metadatas, ids=ids)
                total_chunks += len(chunks)
                logger.info(f"✅ indexé : {pdf_path.name} ({len(chunks)} fragments)")

            except Exception as e:
                # Si un PDF est mal formé, on passe au suivant au lieu de crasher
                logger.warning(f"❌ Impossible de lire {pdf_path.name} : {e}")

        return total_chunks

    def _simple_split(self, text: str, chunk_size: int = 1100, overlap: int = 150) -> List[str]:
        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]
            if end < len(text):
                last_period = chunk.rfind(". ")
                if last_period > chunk_size // 2:
                    end = start + last_period + 1
                    chunk = text[start:end]
            chunks.append(chunk.strip())
            start = end - overlap
        return [c for c in chunks if c.strip()]

    def query(self, query_text: str, n_results: int = 5) -> List[Dict[str, Any]]:
        if self.collection is None or not query_text.strip():
            return []
        
        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=n_results,
                include=["documents", "metadatas"]
            )
            formatted = []
            for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
                formatted.append({
                    "content": doc,
                    "source": meta.get("source", "source inconnue")
                })
            return formatted
        except Exception as e:
            logger.error(f"❌ Erreur requête KnowledgeBase : {e}")
            return []

    def get_context_for_agent(self, query_text: str, max_results: int = 5) -> str:
        results = self.query(query_text, n_results=max_results)
        if not results:
            return "Aucune connaissance théorique disponible."

        context = "📚 **Connaissances théoriques (Extraits) :**\n\n"
        for i, r in enumerate(results, 1):
            context += f"[{i}] (Source: {r['source']})\n{r['content'][:600]}...\n\n"
        return context.strip()
