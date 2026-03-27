"""
🔍 KNOWLEDGE BASE V1.3 — Version Intégrale Stable + AUTO-LOAD
"""

import os
from pathlib import Path
from typing import List, Dict, Any
import chromadb
from chromadb.utils import embedding_functions
from pypdf import PdfReader
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
            logger.info("✅ [KNOWLEDGE-BASE] ChromaDB initialisée")
            
            # === AUTO-LOAD PDFs (ce que tu as demandé) ===
            # Chargement automatique si la base est vide (premier démarrage)
            # Pour les redémarrages suivants, ça saute automatiquement car ChromaDB est persistante
            try:
                if self.collection and self.collection.count() == 0:
                    logger.info("🔄 [KNOWLEDGE-BASE] Collection vide → Chargement automatique des PDFs depuis la racine...")
                    self.load_pdfs_from_root()
                else:
                    count = self.collection.count() if self.collection else 0
                    logger.info(f"📚 [KNOWLEDGE-BASE] {count} fragments déjà présents dans ChromaDB (auto-load skipped)")
            except Exception as e:
                logger.warning(f"[KNOWLEDGE-BASE] Auto-load check failed: {e}")
                
        except Exception as e:
            logger.error(f"❌ [KNOWLEDGE-BASE] Erreur initialisation : {e}")
            self.collection = None

    def load_pdfs_from_root(self) -> int:
        """Cherche les PDF dans le dossier 'knowledge' ou à la racine (Fix Railway)."""
        if self.collection is None: return 0

        # Détection hybride du chemin
        search_dirs = [Path("knowledge"), Path("/workspace/knowledge"), Path(".")]
        pdf_files = []
        
        for d in search_dirs:
            if d.exists():
                found = list(d.glob("*.pdf")) + list(d.glob("*.PDF"))
                if found:
                    pdf_files = found
                    logger.info(f"📂 [KNOWLEDGE-BASE] PDFs trouvés dans : {d.absolute()}")
                    break

        if not pdf_files:
            logger.warning("ℹ️ [KNOWLEDGE-BASE] Aucun PDF trouvé.")
            return 0

        total_chunks = 0
        for pdf_path in pdf_files:
            logger.info(f"📖 [KNOWLEDGE-BASE] Lecture de {pdf_path.name}...")
            try:
                reader = PdfReader(str(pdf_path))
                full_text = ""
                for page in reader.pages:
                    text = page.extract_text()
                    if text: full_text += text + "\n\n"

                if not full_text.strip():
                    logger.warning(f"📭 [KNOWLEDGE-BASE] {pdf_path.name} est vide.")
                    continue

                chunks = self._simple_split(full_text)
                ids = [f"{pdf_path.stem}_ch_{i}" for i in range(len(chunks))]
                metadatas = [{"source": pdf_path.name, "chunk": i} for i in range(len(chunks))]

                self.collection.add(documents=chunks, metadatas=metadatas, ids=ids)
                total_chunks += len(chunks)
                logger.info(f"✅ [KNOWLEDGE-BASE] {pdf_path.name} indexé ({len(chunks)} fragments)")

            except Exception as e:
                logger.error(f"❌ [KNOWLEDGE-BASE] Erreur indexation {pdf_path.name}: {e}")

        logger.info(f"🎉 [KNOWLEDGE-BASE] Total: {total_chunks} fragments en base.")
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
        if self.collection is None or not query_text.strip(): return []
        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=n_results,
                include=["documents", "metadatas"]
            )
            formatted = []
            if results["documents"]:
                for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
                    formatted.append({"content": doc, "source": meta.get("source", "unknown")})
            return formatted
        except Exception as e:
            logger.error(f"❌ [KNOWLEDGE-BASE] Erreur recherche : {e}")
            return []

    def get_context_for_agent(self, query_text: str, max_results: int = 5) -> str:
        results = self.query(query_text, n_results=max_results)
        if not results: return "Aucune connaissance théorique trouvée."
        context = "📚 **Connaissances théoriques (PDFs) :**\n\n"
        for i, r in enumerate(results, 1):
            context += f"[{i}] (Source: {r['source']})\n{r['content'][:600]}...\n\n"
        return context.strip()
