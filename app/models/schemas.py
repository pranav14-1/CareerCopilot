from datetime import datetime
import uuid
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

# Dimension for text embeddings (e.g., Google text-multilingual-embedding-002 or gemini)
# We will use 768 dimensions as specified in PROJECT_DEFS.md
EMBEDDING_DIMENSION = 768


class Base(DeclarativeBase):
    """Base declarative class for all database models."""
    pass


class User(Base):
    """
    User/Profile model storing parsed resumes, text embeddings, and Telegram IDs.
    """
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=True)
    email: Mapped[str] = mapped_column(String(255), nullable=True)
    
    # Store the parsed resume structure as JSONB validated by Pydantic
    extracted_profile: Mapped[dict] = mapped_column(JSONB, nullable=True)
    resume_text: Mapped[str] = mapped_column(Text, nullable=True)
    
    # pgvector embedding of user's overall resume/skills profile
    resume_embedding = mapped_column(Vector(EMBEDDING_DIMENSION), nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    applications: Mapped[list["Application"]] = relationship("Application", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<User telegram_id={self.telegram_id} full_name={self.full_name}>"


class Job(Base):
    """
    Job opportunity storage with full-text search capability and semantic vectors.
    """
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), index=True)
    company: Mapped[str] = mapped_column(String(255), index=True)
    location: Mapped[str] = mapped_column(String(255), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    requirements: Mapped[str] = mapped_column(Text, nullable=True)
    
    # Vector embedding of the job description/role profile
    embedding = mapped_column(Vector(EMBEDDING_DIMENSION), nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    applications: Mapped[list["Application"]] = relationship("Application", back_populates="job", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Job id={self.id} title={self.title} company={self.company}>"


class Application(Base):
    """
    Tracks user job applications natively within the system.
    """
    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    
    # Statuses: APPLIED, INTERVIEWING, OFFERED, REJECTED, ARCHIVED
    status: Mapped[str] = mapped_column(String(50), default="APPLIED", index=True)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="applications")
    job: Mapped["Job"] = relationship("Job", back_populates="applications")

    def __repr__(self) -> str:
        return f"<Application id={self.id} user_id={self.user_id} status={self.status}>"
