"""One-off script: recalculate ratings for all users."""
import asyncio
from sqlalchemy import select
from bot.db.session import AsyncSessionFactory
from bot.db.models import User
from bot.services.rating import update_user_rating


async def main() -> None:
    async with AsyncSessionFactory() as session:
        user_ids = list(await session.scalars(select(User.id)))
        print(f"Recalculating ratings for {len(user_ids)} users...")
        for uid in user_ids:
            await update_user_rating(uid, session)
        await session.commit()
        print("Done.")


asyncio.run(main())
