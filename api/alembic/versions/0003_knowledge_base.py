"""Phase 3 knowledge base and retrieval schema.

Revision ID: 0003_knowledge_base
Revises: 0002_identity_and_conversations
Create Date: 2026-10-31
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

revision: str = "0003_knowledge_base"
down_revision: Union[str, None] = "0002_identity_and_conversations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Enums
    document_status = postgresql.ENUM(
        "draft", "published", "archived", name="document_status_enum", create_type=False
    )
    document_status.create(op.get_bind(), checkfirst=True)

    source_type = postgresql.ENUM(
        "text", "markdown", "pdf", name="source_type_enum", create_type=False
    )
    source_type.create(op.get_bind(), checkfirst=True)

    ingestion_status = postgresql.ENUM(
        "pending", "processing", "ready", "failed", name="ingestion_status_enum", create_type=False
    )
    ingestion_status.create(op.get_bind(), checkfirst=True)

    # 2. Table: knowledge_documents
    op.create_table(
        "knowledge_documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column(
            "source_type",
            postgresql.ENUM("text", "markdown", "pdf", name="source_type_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "draft", "published", "archived", name="document_status_enum", create_type=False
            ),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column(
            "ingestion_status",
            postgresql.ENUM(
                "pending",
                "processing",
                "ready",
                "failed",
                name="ingestion_status_enum",
                create_type=False,
            ),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("ingestion_error_code", sa.String(length=50), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_knowledge_documents_created_by_user_id_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_documents"),
    )
    op.create_index(
        "ix_knowledge_documents_status", "knowledge_documents", ["status"], unique=False
    )
    op.create_index(
        "ix_knowledge_documents_ingestion_status",
        "knowledge_documents",
        ["ingestion_status"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_documents_created_by_user_id",
        "knowledge_documents",
        ["created_by_user_id"],
        unique=False,
    )

    # 3. Table: knowledge_chunks
    op.create_table(
        "knowledge_chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            name="fk_knowledge_chunks_document_id_knowledge_documents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_chunks"),
    )
    op.create_index(
        "ix_knowledge_chunks_document_ordinal",
        "knowledge_chunks",
        ["document_id", "ordinal"],
        unique=True,
    )
    op.create_index(
        "ix_knowledge_chunks_embedding_hnsw",
        "knowledge_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_embedding_hnsw", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_document_ordinal", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")

    op.drop_index("ix_knowledge_documents_created_by_user_id", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_ingestion_status", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_status", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")

    op.execute("DROP TYPE IF EXISTS ingestion_status_enum")
    op.execute("DROP TYPE IF EXISTS source_type_enum")
    op.execute("DROP TYPE IF EXISTS document_status_enum")

