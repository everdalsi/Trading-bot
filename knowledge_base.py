"""
🔍 KNOWLEDGE BASE V1.1 — Version intégrée au système de logs
Analyse les PDFs à la racine et les stocke dans ChromaDB.
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
            # Embedding léger par défaut de ChromaDB
            self.embedding_function = embedding_functions.DefaultEmbeddingFunction()
            
            self.collection = self.client.get_or_create_collection(
                name=collection_name,
                embedding_function=self.embedding_function,
                metadata={"hnsw:space": "cosine"}
            )
            logger.info("✅ KnowledgeBase initialisée avec succès (ChromaDB)")
        except Exception as e:
            logger.error(f"❌ Erreur lors de l'initialisation de KnowledgeBase : {e}")
            raise

    def load_pdfs_from_root(self) -> int:
        """Cherche et indexe les PDFs présents à la racine du projet."""
        root = Path(".")
        pdf_files = list(root.glob("*.pdf")) + list(root.glob("*.PDF"))
        
        if not pdf_files:
            logger.warning("⚠️ Aucun PDF trouvé à la racine. La base de connaissance sera vide.")
            return 0

        total_chunks = 0
        for pdf_path in pdf_files:
            logger.info(f"📖 Lecture du PDF : {pdf_path.name}...")
            try:
                reader = PdfReader(str(pdf_path))
                full_text = ""
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        full_text += text + "\n\n"

                if not full_text.strip():
                    logger.warning(f"📭 Le fichier {pdf_path.name} est vide ou illisible.")
                    continue

                chunks = self._simple_split(full_text)

                # Génération des IDs uniques pour éviter les doublons
                ids = [f"{pdf_path.stem}_ch_{i}" for i in range(len(chunks))]
                metadatas = [{"source": pdf_path.name, "chunk": i} for i in range(len(chunks))]

                self.collection.add(
                    documents=chunks, 
                    metadatas=metadatas, 
                    ids=ids
                )
                
                total_chunks += len(chunks)
                logger.info(f"✅ {pdf_path.name} indexé ({len(chunks)} fragments ajoutés).")

            except Exception as e:
                logger.error(f"❌ Erreur lors de l'indexation de {pdf_path.name} : {e}")

        logger.info(f"🎉 Base de connaissance prête : {len(pdf_files)} PDFs traitées, {total_chunks} fragments au total.")
        return total_chunks

    def _simple_split(self, text: str, chunk_size: int = 1100, overlap: int = 150) -> List[str]:
        """Découpe le texte en morceaux (chunks) pour faciliter la recherche sémantique."""
        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]
            
            # On essaie de couper proprement au dernier point pour ne pas casser les phrases
            if end < len(text):
                last_period = chunk.rfind(". ")
                if last_period > chunk_size // 2:
                    end = start + last_period + 1
                    chunk = text[start:end]
            
            chunks.append(chunk.strip())
            start = end - overlap
        return [c for c in chunks if c.strip()]

    def query(self, query_text: str, n_results: int = 5) -> List[Dict[str, Any]]:
        """Recherche les informations les plus pertinentes pour une question donnée."""
        if not query_text.strip():
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
            logger.error(f"❌ Erreur lors de la requête KnowledgeBase : {e}")
            return []

    def get_context_for_agent(self, query_text: str, max_results: int = 5) -> str:
        """Formate les résultats de recherche pour qu'un agent IA puisse les utiliser."""
        results = self.query(query_text, n_results=max_results)
        
        if not results:
            return "Aucune information théorique pertinente n'a été trouvée dans les guides de trading."

        context = "📚 **Connaissances théoriques (PDFs) :**\n\n"
        for i, r in enumerate(results, 1):
            context += f"[{i}] (Source: {r['source']})\n{r['content'][:600]}...\n\n"
        
        return context.strip()
