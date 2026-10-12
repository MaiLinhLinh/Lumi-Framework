"""Runtime-bound ingestion provider for the Wikipedia Vietnamese package."""

from final_framework.knowledge.chroma_store import ChromaKnowledgeStore
from final_framework.knowledge.media_store import FilesystemMediaStore
from final_framework.knowledge.wikipedia_ingestion import WikipediaIngestionProvider


def create_provider(runtime_context):
    if not isinstance(runtime_context.knowledge_store, ChromaKnowledgeStore):
        raise TypeError("wikipedia_vi requires ChromaKnowledgeStore.")
    if not isinstance(runtime_context.media_store, FilesystemMediaStore):
        raise TypeError("wikipedia_vi requires FilesystemMediaStore.")
    return WikipediaIngestionProvider(
        runtime_context.knowledge_store,
        runtime_context.media_store,
    )
