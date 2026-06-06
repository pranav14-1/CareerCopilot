import io
import os
import asyncio
import logging
from typing import List, Dict, Any, Optional
import pdfplumber
import instructor
from google.genai import Client, types

from app.core.config import settings
from app.models.schemas import UserProfileSchema

logger = logging.getLogger(__name__)

# Ensure GEMINI_API_KEY is available in the environment for Instructor provider
if settings.GEMINI_API_KEY and settings.GEMINI_API_KEY != "placeholder_key":
    os.environ["GEMINI_API_KEY"] = settings.GEMINI_API_KEY

# Initialize Google GenAI client
genai_client = Client(api_key=settings.GEMINI_API_KEY)


async def parse_resume_pdf(pdf_stream: io.BytesIO) -> str:
    """
    Extracts raw text content from a PDF memory stream using pdfplumber.
    Runs inside a thread pool to avoid blocking the main event loop.
    """
    def _extract() -> str:
        text_content = []
        try:
            with pdfplumber.open(pdf_stream) as pdf:
                for page in pdf.pages:
                    extracted = page.extract_text()
                    if extracted:
                        text_content.append(extracted)
            return "\n".join(text_content)
        except Exception as e:
            logger.error(f"Error parsing PDF with pdfplumber: {e}")
            raise ValueError("Failed to extract text from the provided PDF file.")

    logger.info("Starting text extraction from PDF stream...")
    return await asyncio.to_thread(_extract)


async def extract_structured_profile(raw_text: str) -> UserProfileSchema:
    """
    Leverages Gemini and Instructor to parse raw text into structured UserProfileSchema.
    """
    logger.info("Starting structured profile extraction via Gemini and Instructor...")
    try:
        # Initialize the Instructor client for Gemini
        instructor_client = instructor.from_provider(
            "google/gemini-2.5-flash",
            async_client=True,
        )

        prompt = (
            "You are an expert ATS (Applicant Tracking System) parser.\n"
            "Analyze the following resume text and extract all profile details "
            "into the specified structured format. Extract details precisely as they appear.\n\n"
            f"Resume Text:\n{raw_text}"
        )

        profile: UserProfileSchema = await instructor_client.create(
            response_model=UserProfileSchema,
            messages=[
                {"role": "user", "content": prompt}
            ],
            # We can adjust temperature to enforce structure precision
            validation_context={"temperature": 0.0}
        )
        logger.info(f"Successfully extracted structured profile for {profile.name}")
        return profile
    except Exception as e:
        logger.error(f"Failed structured extraction: {e}")
        raise ValueError(f"Failed to parse resume content with LLM structure: {e}")


async def generate_embedding(text: str) -> List[float]:
    """
    Generates a 768-dimension text embedding vector using the Google GenAI gemini-embedding-2 model.
    Uses the asynchronous client interface and Matryoshka Representation Learning (MRL).
    """
    logger.info("Generating text embedding vector...")
    try:
        # Clean/truncate text if it is exceptionally long (model limit is typically 2048 tokens or ~8000 chars)
        truncated_text = text[:8000]
        
        async with genai_client.aio as aclient:
            response = await aclient.models.embed_content(
                model="gemini-embedding-2",
                contents=truncated_text,
                config=types.EmbedContentConfig(
                    output_dimensionality=768
                )
            )
            
        embeddings = response.embeddings
        if not embeddings or len(embeddings) == 0:
            raise ValueError("No embeddings returned by Google GenAI API.")
            
        vector = embeddings[0].values
        logger.info(f"Successfully generated embedding vector of length {len(vector)}")
        return vector
    except Exception as e:
        logger.error(f"Failed generating embedding: {e}")
        raise ValueError(f"Failed to generate vector embedding: {e}")
