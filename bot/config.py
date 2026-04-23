import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
DATABASE_URL: str = os.environ["DATABASE_URL"]
REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
