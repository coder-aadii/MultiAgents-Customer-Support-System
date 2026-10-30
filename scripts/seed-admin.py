#!/usr/bin/env python3
"""Bootstrap the initial admin user. Refuses to overwrite existing admin."""

import asyncio
import os
import sys
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select
from passlib.hash import argon2

# Add the API directory to the import path so this script works from Compose.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "api"))

from app.config import settings
from app.models.user import User


async def seed_admin():
    """Create the initial admin user from environment variables."""
    email = settings.ADMIN_EMAIL
    password = settings.ADMIN_PASSWORD

    if not email or not password:
        print("ERROR: MCSS_ADMIN_EMAIL and MCSS_ADMIN_PASSWORD must be set in .env")
        sys.exit(1)

    if len(password) < 12:
        print("ERROR: Admin password must be at least 12 characters")
        sys.exit(1)

    engine = create_async_engine(settings.DATABASE_URL)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        # Check if admin already exists
        result = await session.execute(select(User).where(User.email == email.lower()))
        existing = result.scalar_one_or_none()

        if existing:
            print(f"Admin user {email} already exists. Refusing to overwrite.")
            await engine.dispose()
            sys.exit(0)

        # Create admin user
        password_hash = argon2.hash(password)
        admin = User(
            email=email.lower(),
            password_hash=password_hash,
            display_name="Administrator",
            role="admin",
            is_active=True,
        )
        session.add(admin)
        await session.commit()
        print(f"Created admin user: {email}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed_admin())
