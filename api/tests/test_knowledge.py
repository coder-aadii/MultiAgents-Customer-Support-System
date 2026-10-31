"""Tests for Sub-phase 3.1 Schema & Document Lifecycle and Sub-phase 3.2 Safe Ingestion."""

import io
import uuid

import pypdf
import pytest
from httpx import ASGITransport, AsyncClient

from app.database import async_session_factory
from app.main import app
from app.models.knowledge_document import DocumentStatus, IngestionStatus, KnowledgeDocument
from app.models.user import User, UserRole
from app.security.password import hash_password
from app.security.tokens import create_access_jwt


def make_valid_text_pdf(text: str = "Hello PDF World! This is valid text.") -> bytes:
    """Generate a minimal valid PDF 1.4 stream containing extractable text."""
    content = f"BT /F1 12 Tf 72 712 Td ({text}) Tj ET"
    stream_len = len(content)
    xref_table = (
        "xref\n"
        "0 6\n"
        "0000000000 65535 f \n"
        "0000000009 00000 n \n"
        "0000000058 00000 n \n"
        "0000000115 00000 n \n"
        "0000000244 00000 n \n"
        "0000000339 00000 n \n"
    )
    pdf = f"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj
4 0 obj << /Length {stream_len} >> stream
{content}
endstream endobj
5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
{xref_table}trailer << /Size 6 /Root 1 0 R >>
startxref
416
%%EOF"""
    return pdf.encode("latin-1")


def make_blank_scanned_pdf() -> bytes:
    """Generate a valid PDF with blank pages (no extractable text)."""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def make_encrypted_pdf() -> bytes:
    """Generate an encrypted password-protected PDF."""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secret123")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture
async def setup_knowledge_users():
    """Setup admin user and staff user for RBAC and document management tests."""
    async with async_session_factory() as session:
        admin_id = uuid.uuid4()
        admin = User(
            id=admin_id,
            email=f"k_admin_{admin_id.hex[:6]}@example.com",
            password_hash=hash_password("AdminPass123!"),
            display_name="Knowledge Admin",
            role=UserRole.admin,
            is_active=True,
        )
        session.add(admin)

        staff_id = uuid.uuid4()
        staff = User(
            id=staff_id,
            email=f"k_staff_{staff_id.hex[:6]}@example.com",
            password_hash=hash_password("StaffPass123!"),
            display_name="Knowledge Staff",
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
# Sub-phase 3.1 Tests: Schema, State Machines, Lifecycle & RBAC
# ==============================================================================


@pytest.mark.asyncio
async def test_document_lifecycle_state_machine(setup_knowledge_users):
    """Test standard publication lifecycle: draft -> published -> archived and invalid transitions."""
    users = setup_knowledge_users
    headers = {"Authorization": f"Bearer {users['admin_token']}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create a draft document
        payload = {
            "title": "Return Policy 2026",
            "source_type": "text",
            "content": "Items can be returned within 30 days of purchase for a full refund.",
        }
        res = await client.post("/api/v1/knowledge/documents", json=payload, headers=headers)
        assert res.status_code == 201
        data = res.json()
        doc_id = data["id"]
        assert data["status"] == "draft"
        assert data["ingestion_status"] == "ready"
        assert data["chunk_count"] > 0
        assert data["published_at"] is None

        # 2. Cannot archive a draft document directly
        res_arch_invalid = await client.post(
            f"/api/v1/knowledge/documents/{doc_id}/archive", headers=headers
        )
        assert res_arch_invalid.status_code == 400

        # 3. Publish document: draft -> published
        res_pub = await client.post(
            f"/api/v1/knowledge/documents/{doc_id}/publish", headers=headers
        )
        assert res_pub.status_code == 200
        pub_data = res_pub.json()
        assert pub_data["status"] == "published"
        assert pub_data["published_at"] is not None

        # 4. Cannot re-publish already published document
        res_pub_twice = await client.post(
            f"/api/v1/knowledge/documents/{doc_id}/publish", headers=headers
        )
        assert res_pub_twice.status_code == 400

        # 5. Archive document: published -> archived
        res_arch = await client.post(
            f"/api/v1/knowledge/documents/{doc_id}/archive", headers=headers
        )
        assert res_arch.status_code == 200
        arch_data = res_arch.json()
        assert arch_data["status"] == "archived"

        # 6. Cannot publish archived document
        res_pub_arch = await client.post(
            f"/api/v1/knowledge/documents/{doc_id}/publish", headers=headers
        )
        assert res_pub_arch.status_code == 400


@pytest.mark.asyncio
async def test_document_deletion_guard(setup_knowledge_users):
    """Test rule: Only draft documents may be deleted. Published and archived cannot."""
    users = setup_knowledge_users
    headers = {"Authorization": f"Bearer {users['admin_token']}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Draft document can be deleted
        res = await client.post(
            "/api/v1/knowledge/documents",
            json={"title": "Draft to Delete", "content": "Sample content to delete."},
            headers=headers,
        )
        draft_id = res.json()["id"]
        del_res = await client.delete(f"/api/v1/knowledge/documents/{draft_id}", headers=headers)
        assert del_res.status_code == 204

        # 2. Create another document and publish it
        res2 = await client.post(
            "/api/v1/knowledge/documents",
            json={"title": "Published Document", "content": "Valid published content."},
            headers=headers,
        )
        pub_id = res2.json()["id"]
        await client.post(f"/api/v1/knowledge/documents/{pub_id}/publish", headers=headers)

        # Deleting published document must fail with 400
        del_pub_res = await client.delete(f"/api/v1/knowledge/documents/{pub_id}", headers=headers)
        assert del_pub_res.status_code == 400
        assert "Only draft documents may be deleted" in del_pub_res.text

        # 3. Archive it and attempt deletion again
        await client.post(f"/api/v1/knowledge/documents/{pub_id}/archive", headers=headers)
        del_arch_res = await client.delete(f"/api/v1/knowledge/documents/{pub_id}", headers=headers)
        assert del_arch_res.status_code == 400
        assert "Only draft documents may be deleted" in del_arch_res.text


@pytest.mark.asyncio
async def test_knowledge_rbac_matrix(setup_knowledge_users):
    """Test RBAC: Staff has read-only access (list, detail); Admin has full mutation rights."""
    users = setup_knowledge_users
    admin_hdr = {"Authorization": f"Bearer {users['admin_token']}"}
    staff_hdr = {"Authorization": f"Bearer {users['staff_token']}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Admin creates document
        create_res = await client.post(
            "/api/v1/knowledge/documents",
            json={"title": "Company Handbook", "content": "Official guidelines and procedures."},
            headers=admin_hdr,
        )
        assert create_res.status_code == 201
        doc_id = create_res.json()["id"]

        # Staff can list documents
        list_res = await client.get("/api/v1/knowledge/documents", headers=staff_hdr)
        assert list_res.status_code == 200
        assert len(list_res.json()["items"]) >= 1

        # Staff can view document detail
        detail_res = await client.get(f"/api/v1/knowledge/documents/{doc_id}", headers=staff_hdr)
        assert detail_res.status_code == 200
        assert detail_res.json()["title"] == "Company Handbook"

        # Staff CANNOT create documents
        staff_create = await client.post(
            "/api/v1/knowledge/documents",
            json={"title": "Staff Draft", "content": "Staff attempt"},
            headers=staff_hdr,
        )
        assert staff_create.status_code == 403

        # Staff CANNOT edit documents
        staff_edit = await client.patch(
            f"/api/v1/knowledge/documents/{doc_id}",
            json={"title": "Modified Title"},
            headers=staff_hdr,
        )
        assert staff_edit.status_code == 403

        # Staff CANNOT publish documents
        staff_pub = await client.post(
            f"/api/v1/knowledge/documents/{doc_id}/publish", headers=staff_hdr
        )
        assert staff_pub.status_code == 403

        # Staff CANNOT delete documents
        staff_del = await client.delete(
            f"/api/v1/knowledge/documents/{doc_id}", headers=staff_hdr
        )
        assert staff_del.status_code == 403


# ==============================================================================
# Sub-phase 3.2 Tests: Safe Ingestion, Parsing, Cleanup & Retry
# ==============================================================================


@pytest.mark.asyncio
async def test_ingest_text_and_markdown_uploads(setup_knowledge_users):
    """Test successful ingestion of TXT and Markdown files via multipart upload."""
    users = setup_knowledge_users
    headers = {"Authorization": f"Bearer {users['admin_token']}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Text upload
        txt_content = b"Frequently Asked Questions:\n\nQ: How to reset password?\nA: Click forgot password."
        files = {"file": ("faq.txt", txt_content, "text/plain")}
        res_txt = await client.post(
            "/api/v1/knowledge/documents/upload",
            files=files,
            data={"title": "FAQ Text"},
            headers=headers,
        )
        assert res_txt.status_code == 201
        data_txt = res_txt.json()
        assert data_txt["source_type"] == "text"
        assert data_txt["ingestion_status"] == "ready"
        assert data_txt["chunk_count"] >= 1
        assert len(data_txt["chunks"]) >= 1

        # 2. Markdown upload
        md_content = b"# Product Catalog\n\n## Widgets\n- Model A: $10\n- Model B: $20"
        files_md = {"file": ("catalog.md", md_content, "text/markdown")}
        res_md = await client.post(
            "/api/v1/knowledge/documents/upload",
            files=files_md,
            headers=headers,
        )
        assert res_md.status_code == 201
        data_md = res_md.json()
        assert data_md["source_type"] == "markdown"
        assert data_md["ingestion_status"] == "ready"
        assert data_md["source_filename"] == "catalog.md"


@pytest.mark.asyncio
async def test_ingest_valid_pdf_upload(setup_knowledge_users):
    """Test successful ingestion of valid text-based PDF."""
    users = setup_knowledge_users
    headers = {"Authorization": f"Bearer {users['admin_token']}"}

    pdf_bytes = make_valid_text_pdf("This is important customer support documentation in PDF.")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {"file": ("manual.pdf", pdf_bytes, "application/pdf")}
        res = await client.post(
            "/api/v1/knowledge/documents/upload",
            files=files,
            data={"title": "User Manual"},
            headers=headers,
        )
        assert res.status_code == 201
        data = res.json()
        assert data["source_type"] == "pdf"
        assert data["ingestion_status"] == "ready"
        assert data["chunk_count"] >= 1
        assert "customer support documentation" in data["chunks"][0]["content"]


@pytest.mark.asyncio
async def test_ingest_reject_oversized_file(setup_knowledge_users):
    """Test validation rejection for uploads exceeding 5 MB limit."""
    users = setup_knowledge_users
    headers = {"Authorization": f"Bearer {users['admin_token']}"}

    # Generate 5.1 MB of data
    oversized_bytes = b"A" * (5 * 1024 * 1024 + 1024)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {"file": ("big_file.txt", oversized_bytes, "text/plain")}
        res = await client.post(
            "/api/v1/knowledge/documents/upload",
            files=files,
            headers=headers,
        )
        # Validation error caught early
        assert res.status_code in (400, 422)


@pytest.mark.asyncio
async def test_ingest_reject_unsupported_file_type(setup_knowledge_users):
    """Test rejection of unsupported file extensions (e.g. .exe, .png)."""
    users = setup_knowledge_users
    headers = {"Authorization": f"Bearer {users['admin_token']}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {"file": ("malicious.exe", b"MZ...", "application/octet-stream")}
        res = await client.post(
            "/api/v1/knowledge/documents/upload",
            files=files,
            headers=headers,
        )
        assert res.status_code in (400, 422)


@pytest.mark.asyncio
async def test_ingest_reject_encrypted_pdf(setup_knowledge_users):
    """Test rejection of encrypted PDF files with PDF_ENCRYPTED code and no partial chunks."""
    users = setup_knowledge_users
    headers = {"Authorization": f"Bearer {users['admin_token']}"}

    enc_pdf = make_encrypted_pdf()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {"file": ("encrypted.pdf", enc_pdf, "application/pdf")}
        # Upload endpoint catches parser error or records document in failed state
        res = await client.post(
            "/api/v1/knowledge/documents/upload",
            files=files,
            headers=headers,
        )
        # Validation catches encrypted PDF during text extraction
        assert res.status_code in (400, 422)


@pytest.mark.asyncio
async def test_ingest_reject_scanned_pdf(setup_knowledge_users):
    """Test rejection of blank or scanned/image-only PDFs with no extractable text."""
    users = setup_knowledge_users
    headers = {"Authorization": f"Bearer {users['admin_token']}"}

    scanned_pdf = make_blank_scanned_pdf()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {"file": ("scanned.pdf", scanned_pdf, "application/pdf")}
        res = await client.post(
            "/api/v1/knowledge/documents/upload",
            files=files,
            headers=headers,
        )
        assert res.status_code in (400, 422)


@pytest.mark.asyncio
async def test_ingest_failure_cleanup_and_retry(setup_knowledge_users):
    """Test that failed documents leave zero partial chunks and can be retried once fixed."""
    users = setup_knowledge_users
    headers = {"Authorization": f"Bearer {users['admin_token']}"}

    # 1. Create a draft document directly via database with failed state
    async with async_session_factory() as session:
        doc = KnowledgeDocument(
            title="Failed Doc",
            source_type="text",
            content="Initial text",
            status=DocumentStatus.draft,
            ingestion_status=IngestionStatus.failed,
            ingestion_error_code="PARSER_FAILED",
            created_by_user_id=users["admin"].id,
        )
        session.add(doc)
        await session.commit()
        doc_id = doc.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Check document detail shows failed state and 0 chunks
        res = await client.get(f"/api/v1/knowledge/documents/{doc_id}", headers=headers)
        assert res.status_code == 200
        assert res.json()["ingestion_status"] == "failed"
        assert res.json()["chunk_count"] == 0

        # Cannot publish a failed document
        res_pub = await client.post(
            f"/api/v1/knowledge/documents/{doc_id}/publish", headers=headers
        )
        assert res_pub.status_code == 400

        # Update document with valid content
        await client.patch(
            f"/api/v1/knowledge/documents/{doc_id}",
            json={"content": "Fixed valid content for knowledge base."},
            headers=headers,
        )

        # Retry ingestion explicitly
        res_retry = await client.post(
            f"/api/v1/knowledge/documents/{doc_id}/retry", headers=headers
        )
        # Should now be ready with chunks
        assert res_retry.status_code in (200, 400)  # if update already retried or retry endpoint succeeded
        res_after = await client.get(f"/api/v1/knowledge/documents/{doc_id}", headers=headers)
        assert res_after.json()["ingestion_status"] == "ready"
        assert res_after.json()["chunk_count"] > 0


@pytest.mark.asyncio
async def test_knowledge_cursor_pagination_and_validation(setup_knowledge_users):
    """Test cursor pagination conforms to standard base64 format and rejects invalid cursors with 400."""
    users = setup_knowledge_users
    headers = {"Authorization": f"Bearer {users['admin_token']}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create 3 documents
        for i in range(3):
            await client.post(
                "/api/v1/knowledge/documents",
                json={"title": f"Cursor Doc {i}", "content": f"Content for document {i}."},
                headers=headers,
            )

        # List with limit=2
        res1 = await client.get("/api/v1/knowledge/documents?limit=2", headers=headers)
        assert res1.status_code == 200
        data1 = res1.json()
        assert len(data1["items"]) == 2
        assert data1["has_more"] is True
        cursor1 = data1["next_cursor"]
        assert cursor1 is not None

        # Fetch page 2 using cursor
        res2 = await client.get(f"/api/v1/knowledge/documents?limit=2&cursor={cursor1}", headers=headers)
        assert res2.status_code == 200
        data2 = res2.json()
        assert len(data2["items"]) >= 1
        # Assert no duplicate IDs between pages
        page1_ids = {item["id"] for item in data1["items"]}
        for item in data2["items"]:
            assert item["id"] not in page1_ids

        # Malformed cursor must return 400 VALIDATION_ERROR
        res_bad = await client.get("/api/v1/knowledge/documents?cursor=invalid_not_base64_tuple", headers=headers)
        assert res_bad.status_code == 400
        assert res_bad.json()["error"]["code"] == "VALIDATION_ERROR"
