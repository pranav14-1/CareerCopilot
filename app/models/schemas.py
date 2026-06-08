from datetime import datetime
import uuid
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, String, Text, Index, func, literal_column
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

# Dimension for text embeddings (e.g., text-embedding-004)
EMBEDDING_DIMENSION = 768

# ==========================================
# Pydantic Schemas for Structured Ingestion
# ==========================================

class EducationSchema(BaseModel):
    """Schema representing an academic institution and degree details."""
    institution: str = Field(default="", description="Name of the university, college, or school")
    degree: str = Field(default="", description="Degree type (e.g., BTech, MS, High School)")
    major: str = Field(default="", description="Field of study / major")
    start_year: Optional[int] = Field(None, description="Start year")
    end_year: Optional[int] = Field(None, description="End year or expected graduation year")


class ExperienceSchema(BaseModel):
    """Schema representing a professional work experience entry."""
    company: str = Field(default="", description="Name of the company or organization")
    role: str = Field(default="", description="Job title / role")
    description: str = Field(default="", description="Brief description of responsibilities and achievements")
    start_date: str = Field(default="", description="Start date (e.g., MM/YYYY or Year)")
    end_date: str = Field(default="Present", description="End date or 'Present'")


class ProjectSchema(BaseModel):
    """Schema representing a personal or professional project."""
    title: str = Field(default="", description="Title of the project")
    description: str = Field(default="", description="Details of what was built and outcomes")
    technologies: List[str] = Field(default_factory=list, description="List of technologies, languages, and frameworks used")


class UserProfileSchema(BaseModel):
    """Strict schema for profile details extracted from resume raw text."""
    name: str = Field(default="", description="Full name of the candidate")
    email: str = Field(default="", description="Primary email address")
    phone: str = Field(default="", description="Contact phone number")
    skills: List[str] = Field(default_factory=list, description="Technical skills, frameworks, tools and programming languages")
    education: List[EducationSchema] = Field(default_factory=list, description="Academic history details")
    experience: List[ExperienceSchema] = Field(default_factory=list, description="Professional work history")
    projects: List[ProjectSchema] = Field(default_factory=list, description="Relevant projects listing")


# ==========================================
# SQLAlchemy Declarative Models
# ==========================================

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
    
    # Store the parsed resume structure as JSONB validated by UserProfileSchema
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
    
    __table_args__ = (
        Index(
            "hnsw_job_embedding_cosine_idx",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"}
        ),
        Index(
            "ix_jobs_fts",
            func.to_tsvector(
                literal_column("'english'"),
                func.coalesce(title, "") + " " +
                func.coalesce(company, "") + " " +
                func.coalesce(description, "") + " " +
                func.coalesce(requirements, "")
            ),
            postgresql_using="gin"
        ),
    )

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
