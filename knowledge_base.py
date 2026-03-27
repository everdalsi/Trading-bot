"""
KNOWLEDGE BASE V1 — Version légère (sans sentence-transformers)
Compatible avec tes PDFs à la racine
"""

import os
from pathlib import Path
from typing import List, Dict, Any

import chromadb
from chromadb.utils import embedding_functions
from pypdf import PdfReader

class KnowledgeBase:
    def __init__(self, db_path: str = "knowledge_db", collection_name: str = "trading_knowledge"):
        self.client = chromadb.PersistentClient(path=db_path)
        # Embedding ultra-léger intégré à ChromaDB (pas de torch ni sentence-transformers)
        self.embedding_function = embedding_functions.DefaultEmbeddingFunction()
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=self.embedding_function,
            metadata={"hnsw:space": "cosine"}
        )
        print(f"✅ KnowledgeBase initialisée (version légère)")

    def load_pdfs_from_root(self) -> int:
        root = Path(".")
        pdf_files = list(root.glob("*.pdf")) + list(root.glob("*.PDF"))
        
        if not pdf_files:
            print("⚠️ Aucun PDF trouvé à la racine")
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

                ids = [f"{pdf_path.stem}_chunk_{i}" for i in range(len(chunks))]
                metadatas = [{"source": pdf_path.name, "chunk": i} for i in range(len(chunks))]

                self.collection.add(documents=chunks, metadatas=metadatas, ids=ids)
                total_chunks += len(chunks)
                print(f"✅ {pdf_path.name} → {len(chunks)} chunks ajoutés")

            except Exception as e:
                print(f"❌ Erreur lecture {pdf_path.name} : {e}")

        print(f"🎉 Knowledge Base chargée : {len(pdf_files)} PDFs → {total_chunks} chunks")
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

    def query(self, query_text: str, n_results: int = 6) -> List[Dict[str, Any]]:
        if not query_text.strip():
            return []
        results = self.collection.query(
            query_texts=[query_text],
            n_results=n_results,
            include=["documents", "metadatas"]
        )
        formatted = []
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            formatted.append({
                "content": doc,
                "source": meta.get("source", "unknown")
            })
        return formatted

    def get_context_for_agent(self, query_text: str, max_results: int = 7) -> str:
        results = self.query(query_text, n_results=max_results)
        if not results:
            return "Aucune connaissance pertinente trouvée dans les PDFs."
        context = "📚 **Connaissances extraites de tes PDFs :**\n\n"
        for i, r in enumerate(results, 1):
            context += f"[{i}] Source: {r['source']}\n{r['content'][:750]}...\n\n"
        return context.strip()
