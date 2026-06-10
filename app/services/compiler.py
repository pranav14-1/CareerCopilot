import logging
import typst
from typing import Dict, Any

logger = logging.getLogger(__name__)


def escape_typst(text: str) -> str:
    """Escapes any special characters for Typst markup."""
    if not text:
        return ""
    res = str(text)
    res = res.replace("\\", "\\\\")
    res = res.replace("#", "\\#")
    res = res.replace("$", "\\$")
    res = res.replace("*", "\\*")
    res = res.replace("_", "\\_")
    res = res.replace("<", "\\<")
    res = res.replace(">", "\\>")
    res = res.replace("@", "\\@")
    return res


async def inject_and_compile_typst(resume_data: Dict[str, Any]) -> bytes:
    """
    Constructs Typst source from structured resume_data, compiles it to PDF,
    and returns the compiled PDF as bytes.
    """
    logger.info("Generating Typst markup for resume...")
    
    # Extract candidate metadata with fallbacks
    name = escape_typst(resume_data.get("name", "Candidate"))
    email = escape_typst(resume_data.get("email", ""))
    phone = escape_typst(resume_data.get("phone", ""))
    summary = escape_typst(resume_data.get("summary", ""))
    
    skills = [escape_typst(s) for s in resume_data.get("skills", [])]
    skills_str = ", ".join(skills)
    
    education_list = resume_data.get("education", [])
    if isinstance(education_list, str):
        education_str = escape_typst(education_list)
    elif isinstance(education_list, list):
        edu_parts = []
        for edu in education_list:
            if isinstance(edu, dict):
                inst = escape_typst(edu.get("institution", ""))
                deg = escape_typst(edu.get("degree", ""))
                major = escape_typst(edu.get("major", ""))
                start = edu.get("start_year")
                end = edu.get("end_year")
                years = f" ({start} - {end})" if start and end else ""
                edu_parts.append(f"{deg} in {major} from {inst}{years}")
            else:
                edu_parts.append(escape_typst(str(edu)))
        education_str = "\\\n".join(edu_parts)
    else:
        education_str = ""

    # Build experience block
    exp_block = ""
    for exp in resume_data.get("experience", []):
        role = escape_typst(exp.get("role", ""))
        comp = escape_typst(exp.get("company", ""))
        dur = escape_typst(exp.get("duration", ""))
        bullets = exp.get("bullet_points", [])
        
        bullets_str = ""
        for b in bullets:
            bullets_str += f"  - {escape_typst(b)}\n"
            
        exp_block += f"""
#grid(
  columns: (1fr, auto),
  [*{role}* at *{comp}*],
  [_{dur}_]
)
#v(-0.3em)
{bullets_str}
#v(0.3em)
"""

    # Build projects block
    proj_block = ""
    for proj in resume_data.get("projects", []):
        title = escape_typst(proj.get("title", ""))
        desc = escape_typst(proj.get("description", ""))
        tech = [escape_typst(t) for t in proj.get("technologies", [])]
        tech_str = ", ".join(tech)
        
        proj_block += f"""
#grid(
  columns: (1fr, auto),
  [*{title}*],
  [_{tech_str}_]
)
#v(-0.3em)
{desc}
#v(0.3em)
"""

    # Assemble Typst template
    typst_code = f"""
#set page(paper: "us-letter", margin: (x: 1.5cm, y: 1.2cm))
#set text(font: "Liberation Sans", size: 10pt)
#set par(justify: true)

#align(center)[
  #text(size: 16pt, weight: "bold")[{name}] \\
  #text(fill: rgb("606060"), size: 9pt)[{email} | {phone}]
]

#v(0.5em)

== Professional Summary
#line(length: 100%, stroke: 0.5pt + rgb("c0c0c0"))
{summary}

#v(0.8em)
== Skills
#line(length: 100%, stroke: 0.5pt + rgb("c0c0c0"))
{skills_str}

#v(0.8em)
== Work Experience
#line(length: 100%, stroke: 0.5pt + rgb("c0c0c0"))
{exp_block}

#v(0.8em)
== Projects
#line(length: 100%, stroke: 0.5pt + rgb("c0c0c0"))
{proj_block}

#v(0.8em)
== Education
#line(length: 100%, stroke: 0.5pt + rgb("c0c0c0"))
{education_str}
"""

    logger.info("Compiling Typst source to PDF...")
    try:
        pdf_bytes = typst.compile(typst_code.encode("utf-8"))
        logger.info("Successfully compiled Typst document to PDF.")
        return pdf_bytes
    except Exception as e:
        logger.error(f"Typst compilation failed: {e}")
        raise ValueError(f"Failed to compile PDF resume using Typst: {e}")
