"""Shared knowledge base and glossary for all agents."""

import os
import math
import hashlib
import re
from pathlib import Path
from typing import List, Dict, Any
import chromadb
from chromadb import Documents, EmbeddingFunction, Embeddings
from pypdf import PdfReader
from logging_config import logger

# Lightweight embedder: remplace DefaultEmbeddingFunction qui telecharge 79MB (all-MiniLM-L6-v2 ONNX)
# Utilise TF-IDF hashing en 384 dims — demarrage instantane, zero download, zero reseau.

class _FastHashEmbedder(EmbeddingFunction):
    """TF-IDF hash embedding — no model download, no network, ~0ms startup."""

    DIM = 384
    STOP = frozenset([
        "le","la","les","de","du","des","un","une","et","en","a","au","aux",
        "que","qui","par","sur","pour","dans","avec","est","the","of","a","in",
        "to","and","is","for","on","at","by","an","are","as","from","or"
    ])

    def _tokenize(self, text: str) -> list:
        words = re.findall(r"[a-zA-Z\xc0-\xff]{3,}", text.lower())
        return [w for w in words if w not in self.STOP]

    def _embed_one(self, text: str) -> list:
        tokens = self._tokenize(text)
        if not tokens:
            return [0.0] * self.DIM
        freq = {}
        for t in tokens:
            freq[t] = freq.get(t, 0) + 1
        vec = [0.0] * self.DIM
        for word, count in freq.items():
            tf = count / len(tokens)
            digest = hashlib.md5(word.encode()).digest()
            slot = int.from_bytes(digest[:2], "big") % self.DIM
            sign = 1 if digest[2] & 1 else -1
            vec[slot] += sign * tf
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def __call__(self, input: Documents) -> Embeddings:
        return [self._embed_one(doc) for doc in input]


class KnowledgeBase:
    def __init__(self, db_path: str = "knowledge_db", collection_name: str = "trading_knowledge"):
        self.collection = None
        try:
            os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
            os.environ.setdefault("CHROMA_TELEMETRY", "False")
            self.client = chromadb.PersistentClient(path=db_path)
            self.embedding_function = _FastHashEmbedder()
            self.collection = self.client.get_or_create_collection(
                name=collection_name,
                embedding_function=self.embedding_function,
                metadata={"hnsw:space": "cosine"}
            )
            logger.info("KB ChromaDB initialisee (hash embedder — zero download ONNX)")
            if self.collection.count() == 0:
                logger.info("KB Collection vide — chargement PDFs...")
                self.load_pdfs_from_root()
            else:
                logger.info(f"KB {self.collection.count()} fragments en base")
        except Exception as e:
            logger.error(f"KB Erreur init: {e}")
            self.collection = None

    def load_pdfs_from_root(self) -> int:
        if self.collection is None:
            return 0
        search_dirs = [Path("knowledge"), Path("/workspace/knowledge"), Path(".")]
        pdf_files = []
        for d in search_dirs:
            if d.exists():
                found = list(d.glob("*.pdf")) + list(d.glob("*.PDF"))
                if found:
                    pdf_files = found
                    break
        if not pdf_files:
            logger.warning("KB Aucun PDF trouve")
            return 0
        total_chunks = 0
        for pdf_path in pdf_files:
            try:
                reader = PdfReader(str(pdf_path))
                full_text = "".join(
                    (page.extract_text() or "") + "\n\n"
                    for page in reader.pages
                )
                if not full_text.strip():
                    continue
                chunks = self._simple_split(full_text)
                ids = [f"{pdf_path.stem}_ch_{i}" for i in range(len(chunks))]
                metas = [{"source": pdf_path.name, "chunk": i} for i in range(len(chunks))]
                self.collection.add(documents=chunks, metadatas=metas, ids=ids)
                total_chunks += len(chunks)
                logger.info(f"KB {pdf_path.name} indexe ({len(chunks)} fragments)")
            except Exception as e:
                logger.error(f"KB Erreur {pdf_path.name}: {e}")
        return total_chunks

    def _simple_split(self, text: str, chunk_size: int = 1100, overlap: int = 150) -> list:
        chunks, start = [], 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]
            if end < len(text):
                lp = chunk.rfind(". ")
                if lp > chunk_size // 2:
                    end = start + lp + 1
                    chunk = text[start:end]
            chunks.append(chunk.strip())
            start = end - overlap
        return [c for c in chunks if c.strip()]

    def query(self, query_text: str, n_results: int = 5) -> list:
        if self.collection is None or not query_text.strip():
            return []
        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=n_results,
                include=["documents", "metadatas"]
            )
            out = []
            if results["documents"]:
                for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
                    out.append({"content": doc, "source": meta.get("source", "unknown")})
            return out
        except Exception as e:
            logger.error(f"KB Erreur recherche: {e}")
            return []

    def get_context_for_agent(self, query_text: str, max_results: int = 5) -> str:
        results = self.query(query_text, n_results=max_results)
        if not results:
            return "Aucune connaissance theorique trouvee."
        ctx = "Connaissances theoriques (PDFs):\n\n"
        for i, r in enumerate(results, 1):
            ctx += f"[{i}] (Source: {r['source']})\n{r['content'][:600]}...\n\n"
        return ctx.strip()

    def get_glossary(self) -> str:
        ctx = self.get_context_for_agent(
            "Glossaire trading: strategies, risk management, Wyckoff, VSA, Kelly Criterion",
            max_results=6
        )
        return ctx + "\n\nGlossaire dynamique charge"

    def explain_term(self, term: str) -> str:
        if not term:
            return ""
        try:
            results = self.query(term, n_results=3)
            if results:
                return results[0]["content"][:400] + "..."
        except Exception:
            pass
        return f"({term}) — definition non trouvee dans la base"