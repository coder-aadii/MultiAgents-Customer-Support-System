from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import DocumentStatus, IngestionStatus, KnowledgeDocument, SourceType
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.security.password import hash_password
from app.services.retrieval_service import retrieve_knowledge_chunks
from app.tenancy.context import TenantContext


@pytest_asyncio.fixture
async def async_db():
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def test_author(async_db: AsyncSession) -> User:
    user = User(
        email=f"author-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("Pass123!"),
        display_name="Test Author",
        role=UserRole.admin,
        is_active=True,
    )
    async_db.add(user)
    await async_db.commit()
    return user


@pytest_asyncio.fixture
async def two_orgs(async_db: AsyncSession) -> tuple[Organization, Organization]:
    org_a = Organization(
        slug=f"org-vector-a-{uuid.uuid4().hex[:6]}",
        name="Vector Org Alpha",
        is_active=True,
    )
    org_b = Organization(
        slug=f"org-vector-b-{uuid.uuid4().hex[:6]}",
        name="Vector Org Beta",
        is_active=True,
    )
    async_db.add_all([org_a, org_b])
    await async_db.commit()
    return org_a, org_b


@pytest.mark.asyncio
async def test_vector_similarity_search_returns_only_tenant_chunks(
    async_db: AsyncSession, two_orgs: tuple[Organization, Organization], test_author: User
):
    """Verify that vector similarity search strictly isolates knowledge chunks by organization."""
    org_a, org_b = two_orgs

    # Standard vector (1536 dims)
    dummy_vector = [0.05] * 1536

    # Doc and Chunk for Org A
    doc_a = KnowledgeDocument(
        organization_id=org_a.id,
        created_by_user_id=test_author.id,
        title="Alpha Refund Policy",
        source_type=SourceType.text,
        content="Alpha refunds are 100% money back within 30 days.",
        status=DocumentStatus.published,
        ingestion_status=IngestionStatus.ready,
    )
    async_db.add(doc_a)
    await async_db.flush()

    chunk_a = KnowledgeChunk(
        organization_id=org_a.id,
        document_id=doc_a.id,
        ordinal=0,
        content="Alpha refunds are 100% money back within 30 days.",
        token_estimate=12,
        embedding=dummy_vector,
    )
    async_db.add(chunk_a)

    # Doc and Chunk for Org B
    doc_b = KnowledgeDocument(
        organization_id=org_b.id,
        created_by_user_id=test_author.id,
        title="Beta Refund Policy",
        source_type=SourceType.text,
        content="Beta refunds are store credit only within 7 days.",
        status=DocumentStatus.published,
        ingestion_status=IngestionStatus.ready,
    )
    async_db.add(doc_b)
    await async_db.flush()

    chunk_b = KnowledgeChunk(
        organization_id=org_b.id,
        document_id=doc_b.id,
        ordinal=0,
        content="Beta refunds are store credit only within 7 days.",
        token_estimate=12,
        embedding=dummy_vector,
    )
    async_db.add(chunk_b)
    await async_db.commit()

    # Search as Org A
    res_a = await retrieve_knowledge_chunks(
        db=async_db,
        query="What is the refund policy?",
        organization_id=org_a.id,
        min_score=0.0,
    )
    retrieved_chunk_ids_a = [item.chunk_id for item in res_a.results]
    assert chunk_a.id in retrieved_chunk_ids_a
    assert chunk_b.id not in retrieved_chunk_ids_a

    # Search as Org B
    res_b = await retrieve_knowledge_chunks(
        db=async_db,
        query="What is the refund policy?",
        organization_id=org_b.id,
        min_score=0.0,
    )
    retrieved_chunk_ids_b = [item.chunk_id for item in res_b.results]
    assert chunk_b.id in retrieved_chunk_ids_b
    assert chunk_a.id not in retrieved_chunk_ids_b


@pytest.mark.asyncio
async def test_identical_embeddings_in_different_orgs_remain_isolated(
    async_db: AsyncSession, two_orgs: tuple[Organization, Organization], test_author: User
):
    """Even if two organizations upload the exact same document and vector, retrieval stays 100% isolated."""
    org_a, org_b = two_orgs
    identical_vector = [0.1] * 1536

    doc_a = KnowledgeDocument(
        organization_id=org_a.id,
        created_by_user_id=test_author.id,
        title="Shared FAQ",
        source_type=SourceType.text,
        content="Common question answer.",
        status=DocumentStatus.published,
        ingestion_status=IngestionStatus.ready,
    )
    doc_b = KnowledgeDocument(
        organization_id=org_b.id,
        created_by_user_id=test_author.id,
        title="Shared FAQ",
        source_type=SourceType.text,
        content="Common question answer.",
        status=DocumentStatus.published,
        ingestion_status=IngestionStatus.ready,
    )
    async_db.add_all([doc_a, doc_b])
    await async_db.flush()

    chunk_a = KnowledgeChunk(
        organization_id=org_a.id,
        document_id=doc_a.id,
        ordinal=0,
        content="Common question answer.",
        token_estimate=5,
        embedding=identical_vector,
    )
    chunk_b = KnowledgeChunk(
        organization_id=org_b.id,
        document_id=doc_b.id,
        ordinal=0,
        content="Common question answer.",
        token_estimate=5,
        embedding=identical_vector,
    )
    async_db.add_all([chunk_a, chunk_b])
    await async_db.commit()

    # Contextvar switch
    tokens = TenantContext.set_tenant(org_a.id, org_a.slug)
    try:
        res = await retrieve_knowledge_chunks(
            db=async_db,
            query="Common question",
            min_score=0.0,
        )
        found_ids = [item.chunk_id for item in res.results]
        assert chunk_a.id in found_ids
        assert chunk_b.id not in found_ids
    finally:
        TenantContext.reset(tokens)
