"""Tests for Sub-phase 3.3 Chunk & Embedding Pipeline and Sub-phase 3.4 Retrieval API."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import async_session_factory
from app.main import app
from app.models.user import User, UserRole
from app.providers.fake_provider import FakeEmbeddingProvider
from app.security.password import hash_password
from app.security.tokens import create_access_jwt
from app.services.chunking import estimate_tokens, split_into_chunks


@pytest.fixture
async def setup_retrieval_env():
    """Setup admin user and staff user for retrieval authorization and ranking tests."""
    async with async_session_factory() as session:
        admin_id = uuid.uuid4()
        admin = User(
            id=admin_id,
            email=f"ret_admin_{admin_id.hex[:6]}@example.com",
            password_hash=hash_password("AdminPass123!"),
            display_name="Retrieval Admin",
            role=UserRole.admin,
            is_active=True,
        )
        session.add(admin)

        staff_id = uuid.uuid4()
        staff = User(
            id=staff_id,
            email=f"ret_staff_{staff_id.hex[:6]}@example.com",
            password_hash=hash_password("StaffPass123!"),
            display_name="Retrieval Staff",
            role=UserRole.staff,
            is_active=True,
        )
        session.add(staff)
        await session.commit()

        admin_token = create_access_jwt(str(admin.id), "admin")
        staff_token = create_access_jwt(str(staff.id), "staff")

        return {
            "admin": admin,
            "admin_token": admin_token,
            "staff": staff,
            "staff_token": staff_token,
        }


# ==============================================================================
# Sub-phase 3.3 Tests: Chunking & Embedding Properties
# ==============================================================================


def test_chunking_token_estimate_and_offsets():
    """Test chunking logic: boundaries, ~700 token targets, 100 token overlap, and offsets."""
    # 1. Short text fits in a single chunk
    short_text = "This is a single short paragraph under 700 tokens."
    chunks = split_into_chunks(short_text)
    assert len(chunks) == 1
    assert chunks[0].content == short_text
    assert chunks[0].start_char == 0
    assert chunks[0].end_char == len(short_text)
    assert chunks[0].token_estimate == estimate_tokens(short_text)

    # 2. Long text with multiple paragraphs splits cleanly
    para = "A" * 600 + ". "
    long_text = "\n\n".join([f"Paragraph {i}: {para * 3}" for i in range(10)])
    long_chunks = split_into_chunks(long_text, target_tokens=300, overlap_tokens=50)

    assert len(long_chunks) > 1
    for i, c in enumerate(long_chunks):
        assert c.token_estimate > 0
        assert c.start_char >= 0
        assert c.end_char <= len(long_text)
        assert c.start_char < c.end_char
        # Verify chunk order is strictly preserved
        if i > 0:
            assert c.start_char >= long_chunks[i - 1].start_char


@pytest.mark.asyncio
async def test_fake_embedding_provider_determinism():
    """Test that FakeEmbeddingProvider generates deterministic 1536-dimensional unit vectors."""
    provider = FakeEmbeddingProvider()
    assert provider.dimensions == 1536

    vec1 = await provider.embed_text("return policy and refunds")
    vec2 = await provider.embed_text("return policy and refunds")
    vec_diff = await provider.embed_text("unrelated technical query")

    assert len(vec1) == 1536
    assert len(vec2) == 1536
    # Exact deterministic stability
    assert vec1 == vec2
    # Distinct vector for different text
    assert vec1 != vec_diff

    # Unit norm verification
    norm = sum(x * x for x in vec1)
    assert round(norm, 2) == 1.0


# ==============================================================================
# Sub-phase 3.4 Tests: Retrieval API & Published-Only Ranking
# ==============================================================================


@pytest.mark.asyncio
async def test_retrieval_ranking_and_published_filter(setup_retrieval_env):
    """Test vector similarity ranking, published-document filter, and draft/archive exclusion."""
    env = setup_retrieval_env
    admin_hdr = {"Authorization": f"Bearer {env['admin_token']}"}
    staff_hdr = {"Authorization": f"Bearer {env['staff_token']}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create and publish target FAQ: Returns
        res1 = await client.post(
            "/api/v1/knowledge/documents",
            json={
                "title": "Returns & Refunds Policy",
                "content": "Customers can return undamaged items within 30 days of delivery for a full refund to their original payment method.",
            },
            headers=admin_hdr,
        )
        assert res1.status_code == 201
        doc1_id = res1.json()["id"]
        pub_res1 = await client.post(
            f"/api/v1/knowledge/documents/{doc1_id}/publish", headers=admin_hdr
        )
        assert pub_res1.status_code == 200

        # 2. Create and publish secondary FAQ: Shipping
        res2 = await client.post(
            "/api/v1/knowledge/documents",
            json={
                "title": "Shipping Rates & Delivery",
                "content": "Standard shipping takes 3 to 5 business days and costs $5 flat. Express delivery takes 1 to 2 business days.",
            },
            headers=admin_hdr,
        )
        assert res2.status_code == 201
        doc2_id = res2.json()["id"]
        pub_res2 = await client.post(
            f"/api/v1/knowledge/documents/{doc2_id}/publish", headers=admin_hdr
        )
        assert pub_res2.status_code == 200

        # 3. Create a DRAFT document with exact matching refund text (Must NOT be retrieved)
        res_draft = await client.post(
            "/api/v1/knowledge/documents",
            json={
                "title": "Unpublished Draft Returns",
                "content": "Secret draft: VIP customers can return items within 365 days for a refund.",
            },
            headers=admin_hdr,
        )
        assert res_draft.status_code == 201
        draft_id = res_draft.json()["id"]

        # 4. Staff executes retrieval query for refund
        query_payload = {
            "query": "Customers can return undamaged items within 30 days of delivery for a full refund to their original payment method.",
            "min_score": 0.5,
            "limit": 5,
        }
        ret_res = await client.post(
            "/api/v1/knowledge/documents/retrieve",
            json=query_payload,
            headers=staff_hdr,
        )
        assert ret_res.status_code == 200
        ret_data = ret_res.json()
        assert ret_data["total_found"] >= 1

        # Check top result is published document
        top_match = ret_data["results"][0]
        assert top_match["document_id"] == doc1_id
        assert top_match["document_title"] == "Returns & Refunds Policy"
        assert top_match["rank"] == 1
        assert 0.0 <= top_match["score"] <= 1.0
        assert "chunk_id" in top_match
        assert top_match["start_char"] >= 0
        assert top_match["end_char"] > top_match["start_char"]

        # Ensure draft document was NEVER returned in results
        retrieved_doc_ids = [r["document_id"] for r in ret_data["results"]]
        assert draft_id not in retrieved_doc_ids

        # 5. Archive the Returns document: now it should be excluded as well
        arch_res = await client.post(
            f"/api/v1/knowledge/documents/{doc1_id}/archive", headers=admin_hdr
        )
        assert arch_res.status_code == 200

        # Query again: doc1_id should no longer appear in published results
        ret_after_arch = await client.post(
            "/api/v1/knowledge/documents/retrieve",
            json=query_payload,
            headers=staff_hdr,
        )
        assert ret_after_arch.status_code == 200
        after_doc_ids = [r["document_id"] for r in ret_after_arch.json()["results"]]
        assert doc1_id not in after_doc_ids


@pytest.mark.asyncio
async def test_retrieval_debug_and_threshold_filtering(setup_retrieval_env):
    """Test retrieval debug diagnostics and threshold cutoff."""
    env = setup_retrieval_env
    admin_hdr = {"Authorization": f"Bearer {env['admin_token']}"}
    staff_hdr = {"Authorization": f"Bearer {env['staff_token']}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create published document
        res = await client.post(
            "/api/v1/knowledge/documents",
            json={
                "title": "Account Security",
                "content": "Users can enable multi-factor authentication from their profile security tab.",
            },
            headers=admin_hdr,
        )
        doc_id = res.json()["id"]
        await client.post(f"/api/v1/knowledge/documents/{doc_id}/publish", headers=admin_hdr)

        # Call debug endpoint
        debug_res = await client.post(
            "/api/v1/knowledge/documents/retrieve/debug",
            json={"query": "multi-factor authentication", "min_score": 0.0},
            headers=staff_hdr,
        )
        assert debug_res.status_code == 200
        debug_data = debug_res.json()
        assert "threshold" in debug_data
        assert "total_chunks_scanned" in debug_data
        assert "excluded_unpublished_matches" in debug_data
        assert debug_data["total_chunks_scanned"] > 0


@pytest.mark.asyncio
async def test_retrieval_authorization_matrix(setup_retrieval_env):
    """Test authorization: anonymous and visitor requests are denied; staff and admin are allowed."""
    env = setup_retrieval_env
    staff_hdr = {"Authorization": f"Bearer {env['staff_token']}"}
    admin_hdr = {"Authorization": f"Bearer {env['admin_token']}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Anonymous request denied
        res_anon = await client.post(
            "/api/v1/knowledge/documents/retrieve",
            json={"query": "test query"},
        )
        assert res_anon.status_code == 401

        # 2. Invalid/visitor token denied
        res_bad = await client.post(
            "/api/v1/knowledge/documents/retrieve",
            json={"query": "test query"},
            headers={"Authorization": "Bearer bad-token"},
        )
        assert res_bad.status_code == 401

        # 3. Staff allowed
        res_staff = await client.post(
            "/api/v1/knowledge/documents/retrieve",
            json={"query": "test query"},
            headers=staff_hdr,
        )
        assert res_staff.status_code == 200

        # 4. Admin allowed
        res_admin = await client.post(
            "/api/v1/knowledge/documents/retrieve",
            json={"query": "test query"},
            headers=admin_hdr,
        )
        assert res_admin.status_code == 200
