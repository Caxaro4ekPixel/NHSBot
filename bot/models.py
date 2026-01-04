from sqlalchemy import Column, Integer, BigInteger, String, Boolean, DateTime, Text, ForeignKey, Index, text, Enum as SQLEnum, func
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base, relationship
from typing import Dict
import enum

from config import DATABASE_URL

Base = declarative_base()


class UserRole(str, enum.Enum):
    TRANSLATOR = "translator"
    VOICE = "voice"
    TIMING = "timing"
    CURATOR = "curator"
    DESIGNER = "designer"

engine = create_async_engine(DATABASE_URL, echo=False)
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Release(Base):
    __tablename__ = "releases"
    
    id = Column(Integer, primary_key=True)
    name = Column(String)
    name_ru = Column(String)
    image = Column(String)
    kind = Column(String)
    score = Column(String)
    status = Column(String)
    episodes = Column(Integer)
    episodes_aired = Column(Integer)
    aired_on = Column(String)
    released_on = Column(String)
    chat_id = Column(BigInteger, nullable=True)
    search_prefix = Column(String, default="[Erai-raws]")
    is_completed = Column(Boolean, default=False)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    assignments = relationship("ReleaseAssignment", back_populates="release", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index("idx_releases_chat_id", "chat_id"),
        Index("idx_releases_is_completed", "is_completed"),
        Index("idx_releases_aired_on", "aired_on"),
    )
    
    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "name": self.name,
            "name_ru": self.name_ru,
            "image": self.image,
            "kind": self.kind,
            "score": self.score,
            "status": self.status,
            "episodes": self.episodes,
            "episodes_aired": self.episodes_aired,
            "aired_on": self.aired_on,
            "released_on": self.released_on,
            "chat_id": self.chat_id,
            "search_prefix": self.search_prefix,
            "is_completed": self.is_completed,
        }


class ReleaseAssignment(Base):
    __tablename__ = "release_assignments"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    release_id = Column(Integer, ForeignKey("releases.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role = Column(SQLEnum(UserRole), nullable=False)
    
    release = relationship("Release", back_populates="assignments")
    user = relationship("User", back_populates="assignments")
    
    __table_args__ = (
        Index("idx_release_assignments_release", "release_id"),
        Index("idx_release_assignments_user", "user_id"),
        Index("idx_release_assignments_release_role", "release_id", "role"),
        Index("idx_release_assignments_unique", "release_id", "user_id", "role", unique=True),
    )


class SentEpisode(Base):
    __tablename__ = "sent_episodes"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    release_id = Column(Integer, ForeignKey("releases.id"), nullable=False)
    episode = Column(Integer, nullable=False)
    quality = Column(Integer, nullable=False)
    torrent_url = Column(String, nullable=True)
    magnet_link = Column(String, nullable=True)
    page_link = Column(String, nullable=True)
    sent_at = Column(DateTime, default=func.now())
    
    __table_args__ = (
        Index("idx_sent_episodes_release_ep", "release_id", "episode"),
        Index("idx_sent_episodes_torrent", "torrent_url"),
        Index("idx_sent_episodes_magnet", "magnet_link"),
    )


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger)
    name = Column(String, nullable=False)
    username = Column(String, nullable=True)
    roles = Column(Text, nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    assignments = relationship("ReleaseAssignment", back_populates="user")
    
    def to_dict(self) -> Dict:
        import json
        roles_list = []
        if self.roles:
            try:
                roles_list = json.loads(self.roles)
                if not isinstance(roles_list, list):
                    roles_list = [roles_list] if roles_list else []
            except (json.JSONDecodeError, ValueError):
                roles_list = [self.roles.strip()] if self.roles.strip() else []
        return {
            "id": self.telegram_id or self.id,
            "name": self.name,
            "username": self.username,
            "role": roles_list
        }



async def get_session() -> AsyncSession:
    async with async_session_maker() as session:
        yield session


async def init_db() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))

