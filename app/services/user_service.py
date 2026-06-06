import logging
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import User

logger = logging.getLogger(__name__)


async def get_user_profile(db: AsyncSession, telegram_id: int) -> Optional[User]:
    """
    Fetch a user profile by Telegram ID.
    """
    logger.info(f"Fetching user profile for telegram_id: {telegram_id}")
    stmt = select(User).where(User.telegram_id == telegram_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def upsert_user_profile(
    db: AsyncSession,
    telegram_id: int,
    full_name: str,
    email: str,
    extracted_profile: dict,
    resume_text: str,
    resume_embedding: List[float],
) -> User:
    """
    Insert or update a user profile. If user exists, update all values.
    """
    logger.info(f"Upserting user profile for telegram_id: {telegram_id}")
    user = await get_user_profile(db, telegram_id)
    
    if user is None:
        user = User(
            telegram_id=telegram_id,
            full_name=full_name,
            email=email,
            extracted_profile=extracted_profile,
            resume_text=resume_text,
            resume_embedding=resume_embedding,
        )
        db.add(user)
        logger.info(f"Created new user profile for telegram_id: {telegram_id}")
    else:
        user.full_name = full_name
        user.email = email
        user.extracted_profile = extracted_profile
        user.resume_text = resume_text
        user.resume_embedding = resume_embedding
        logger.info(f"Updated existing user profile for telegram_id: {telegram_id}")

    await db.commit()
    await db.refresh(user)
    return user
