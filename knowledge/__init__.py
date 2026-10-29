"""Independent knowledge-package loading and retrieval contracts."""

from .access import (
    KNOWLEDGE_TOKEN_HEADER,
    KnowledgeAccessContext,
    KnowledgeAccessError,
    KnowledgeGrant,
    KnowledgeGrantRegistry,
)
from .gateway import KnowledgeGateway

from .registry import (
    KnowledgeManifestError,
    KnowledgePackageManifest,
    KnowledgeProviderRuntimeContext,
    KnowledgeRegistry,
    KnowledgeRegistryError,
    LoadedKnowledgePackage,
)
from .chroma_store import (
    COLLECTION_NAMES,
    DEFAULT_CHROMA_PATH,
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    ChromaKnowledgeStore,
    ChromaSearchHit,
    GeminiEmbeddingProvider,
    KnowledgeStoreError,
)
from .media_store import DEFAULT_MEDIA_PATH, FilesystemMediaStore, MediaStoreError
from .media_reference_store import MediaReference, MediaReferenceStore, MediaReferenceStoreError
from .retrieval_provider import (
    ChromaRetrievalProvider,
    MediaRetrievalProvider,
    RetrievalProviderError,
    TextRetrievalProvider,
)
from .tools import (
    KNOWLEDGE_TOOL_REGISTRY,
    SEARCH_LOCAL_MEDIA,
    SEARCH_LOCAL_TEXT,
    KnowledgeToolArgumentsError,
    knowledge_tool_definition,
    knowledge_tool_definitions,
    validate_knowledge_tool_arguments,
)

__all__ = [
    "KNOWLEDGE_TOKEN_HEADER",
    "KnowledgeAccessContext",
    "KnowledgeAccessError",
    "KnowledgeGateway",
    "KnowledgeGrant",
    "KnowledgeGrantRegistry",
    "KnowledgeManifestError",
    "KnowledgePackageManifest",
    "KnowledgeProviderRuntimeContext",
    "KnowledgeRegistry",
    "KnowledgeRegistryError",
    "LoadedKnowledgePackage",
    "COLLECTION_NAMES",
    "DEFAULT_CHROMA_PATH",
    "EMBEDDING_DIMENSION",
    "EMBEDDING_MODEL",
    "ChromaKnowledgeStore",
    "ChromaSearchHit",
    "GeminiEmbeddingProvider",
    "KnowledgeStoreError",
    "DEFAULT_MEDIA_PATH",
    "FilesystemMediaStore",
    "MediaStoreError",
    "MediaReference",
    "MediaReferenceStore",
    "MediaReferenceStoreError",
    "ChromaRetrievalProvider",
    "MediaRetrievalProvider",
    "RetrievalProviderError",
    "TextRetrievalProvider",
    "KNOWLEDGE_TOOL_REGISTRY",
    "KnowledgeToolArgumentsError",
    "SEARCH_LOCAL_MEDIA",
    "SEARCH_LOCAL_TEXT",
    "knowledge_tool_definition",
    "knowledge_tool_definitions",
    "validate_knowledge_tool_arguments",
]
