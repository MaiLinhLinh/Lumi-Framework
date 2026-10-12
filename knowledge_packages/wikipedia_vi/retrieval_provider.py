"""Runtime-bound retrieval provider for the Wikipedia Vietnamese package."""

from final_framework.knowledge.chroma_store import ChromaKnowledgeStore
from final_framework.knowledge.media_store import FilesystemMediaStore
from final_framework.knowledge.retrieval_provider import (
    MediaRetrievalProvider,
    TextRetrievalProvider,
)


class WikipediaRetrievalProvider:
    def __init__(
        self,
        knowledge_store: ChromaKnowledgeStore,
        media_store: FilesystemMediaStore,
    ) -> None:
        self._text = TextRetrievalProvider(knowledge_store)
        self._media = MediaRetrievalProvider(knowledge_store, media_store)

    def search_text(self, request):
        return self._text.search(request)

    def search_media(self, request):
        return self._media.search(request)


def create_provider(runtime_context):
    if not isinstance(runtime_context.knowledge_store, ChromaKnowledgeStore):
        raise TypeError("wikipedia_vi requires ChromaKnowledgeStore.")
    if not isinstance(runtime_context.media_store, FilesystemMediaStore):
        raise TypeError("wikipedia_vi requires FilesystemMediaStore.")
    return WikipediaRetrievalProvider(
        runtime_context.knowledge_store,
        runtime_context.media_store,
    )
