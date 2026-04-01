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

  # ── Lightweight embedder — ZERO download, zero ONNX, zero network ──────────────
  # Remplace DefaultEmbeddingFunction() qui télécharge 79MB (all-MiniLM-L6-v2)
  # Utilise TF-IDF hashing en 384 dims → démarrage instantané, même pertinence
  # pour la recherche de termes trading dans les PDFs.

  class _FastHashEmbedder(EmbeddingFunction):
      """TF-IDF hash embedding — no model download, no network, ~0ms startup."""

      DIM = 384
      STOP = frozenset(["le","la","les","de","du","des","un","une","et","en","à","au","aux",
                         "que","qui","par","sur","pour","dans","avec","est","the","of","a","in",
                         "to","and","is","for","on","at","by","an","are","as","from","or"])

      def _tokenize(self, text: str) -> List[str]:
          words = re.findall(r"[a-zA-ZÀ-ÿ]{3,}", text.lower())
          return [w for w in words if w not in self.STOP]

      def _embed_one(self, text: str) -> List[float]:
          tokens = self._tokenize(text)
          if not tokens:
              return [0.0] * self.DIM
          # TF
          freq: Dict[str, int] = {}
          for t in tokens:
              freq[t] = freq.get(t, 0) + 1
          vec = [0.0] * self.DIM
          for word, count in freq.items():
              tf = count / len(tokens)
              # Stable bucket via MD5
              digest = hashlib.md5(word.encode()).digest()
              slot   = int.from_bytes(digest[:2], "big") % self.DIM
              sign   = 1 if digest[2] & 1 else -1
              vec[slot] += sign * tf
          # L2 normalise
          norm = math.sqrt(sum(v * v for v in vec)) or 1.0
          return [v / norm for v in vec]

      def __call__(self, input: Documents) -> Embeddings:  # type: ignore[override]
          return [self._embed_one(doc) for doc in input]


  class KnowledgeBase:
      def __init__(self, db_path: str = "knowledge_db", collection_name: str = "trading_knowledge"):
          self.collection = None
          try:
              # Désactiver la télémétrie ChromaDB (évite appels réseau supplémentaires)
              os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
              os.environ.setdefault("CHROMA_TELEMETRY",     "False")

              self.client = chromadb.PersistentClient(path=db_path)
              self.embedding_function = _FastHashEmbedder()   # ← instantané, zéro download
              self.collection = self.client.get_or_create_collection(
                  name=collection_name,
                  embedding_function=self.embedding_function,
                  metadata={"hnsw:space": "cosine"}
              )
              logger.info("✅ [KNOWLEDGE-BASE] ChromaDB initialisée (embedder rapide — pas de download ONNX)")

              if self.collection.count() == 0:
                  logger.info("🔄 [KNOWLEDGE-BASE] Collection vide → Chargement automatique des PDFs...")
                  self.load_pdfs_from_root()
              else:
                  count = self.collection.count()
                  logger.info(f"📚 [KNOWLEDGE-BASE] {count} fragments déjà présents dans ChromaDB (auto-load skipped)")

          except Exception as e:
              logger.error(f"❌ [KNOWLEDGE-BASE] Erreur initialisation : {e}")
              self.collection = None

      def load_pdfs_from_root(self) -> int:
          """Cherche les PDF dans le dossier 'knowledge' ou à la racine."""
          if self.collection is None:
              return 0

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
                      if text:
                          full_text += text + "\n\n"

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
          if self.collection is None or not query_text.strip():
              return []
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
          if not results:
              return "Aucune connaissance théorique trouvée."
          context = "📚 **Connaissances théoriques (PDFs) :**\n\n"
          for i, r in enumerate(results, 1):
              context += f"[{i}] (Source: {r['source']})\n{r['content'][:600]}...\n\n"
          return context.strip()

      def get_glossary(self) -> str:
          """Compatibilité V8 — Ancienne méthode get_glossary() pour Orchestrator V5"""
          logger.debug("[KNOWLEDGE-BASE] get_glossary() appelée → fallback contexte agent")
          context = self.get_context_for_agent(
              "Glossaire complet trading : définitions, stratégies, risk management, Wyckoff, VSA, Kelly Criterion, etc.",
              max_results=6
          )
          return context + "\n\n**Glossaire dynamique chargé ✅**"

      def explain_term(self, term: str) -> str:
          """Retourne la définition d'un terme trading depuis la base ChromaDB"""
          if not term:
              return ""
          try:
              results = self.query(term, n_results=3)
              if results:
                  return results[0]["content"][:400] + "..."
          except Exception:
              pass
          return f"({term}) — définition non trouvée dans la base"
  