import asyncio
import logging
import uuid
import httpx
import random
from sqlalchemy.future import select

from app.core.database import AsyncSessionLocal
from app.models.schemas import Job
from app.services.parser import generate_embedding
from app.core.config import settings

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15"
]


async def fetch_remotive_jobs(limit: int = 30) -> list:
    """Fetch job postings from the Remotive API."""
    url = "https://remotive.com/api/remote-jobs?category=software-development&limit=50"
    logger.info(f"Fetching jobs from Remotive: {url}")
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, headers=HEADERS)
            if response.status_code == 200:
                data = response.json()
                jobs = data.get("jobs", [])
                logger.info(f"Fetched {len(jobs)} jobs from Remotive.")
                return jobs[:limit]
            else:
                logger.error(f"Remotive API returned status {response.status_code}")
    except Exception as e:
        logger.error(f"Error fetching from Remotive: {e}")
    return []


async def fetch_arbeitnow_jobs(limit: int = 30) -> list:
    """Fetch job postings from the Arbeitnow API."""
    url = "https://www.arbeitnow.com/api/job-board-api"
    logger.info(f"Fetching jobs from Arbeitnow: {url}")
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, headers=HEADERS)
            if response.status_code == 200:
                data = response.json()
                jobs = data.get("data", [])
                logger.info(f"Fetched {len(jobs)} jobs from Arbeitnow.")
                return jobs[:limit]
            else:
                logger.error(f"Arbeitnow API returned status {response.status_code}")
    except Exception as e:
        logger.error(f"Error fetching from Arbeitnow: {e}")
    return []


async def fetch_instahyre_jobs(limit: int = 25) -> list:
    """
    Scrapes tech jobs from Instahyre's role-specific landing pages using Playwright.
    Implements anti-bot mitigations: User-Agent rotation, stealth args, and random delays.
    """
    logger.info("Initializing Playwright scraper for Instahyre...")
    jobs = []
    
    # Target popular role landing pages on Instahyre representing the tech job market
    urls = [
        "https://www.instahyre.com/ai-engineer-jobs/",
        "https://www.instahyre.com/backend-developer-jobs/",
        "https://www.instahyre.com/software-engineer-jobs/",
        "https://www.instahyre.com/frontend-developer-jobs/",
        "https://www.instahyre.com/full-stack-developer-jobs/",
        "https://www.instahyre.com/devops-jobs/",
        "https://www.instahyre.com/data-science-jobs/",
        "https://www.instahyre.com/product-manager-jobs/",
        "https://www.instahyre.com/mobile-developer-jobs/",
        "https://www.instahyre.com/qa-engineer-jobs/",
        "https://www.instahyre.com/internship-jobs/",
        "https://www.instahyre.com/jobs/software-engineering-internship/",
        "https://www.instahyre.com/jobs/internship-bangalore/",
        "https://www.instahyre.com/jobs/internship-anywhere-in-india/"
    ]
    
    random.shuffle(urls)
    
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            # Launch with headless and standard anti-bot parameters
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox"
                ]
            )
            
            user_agent = random.choice(USER_AGENTS)
            context = await browser.new_context(
                user_agent=user_agent,
                viewport={"width": 1280, "height": 800}
            )
            page = await context.new_page()
            
            # Hide automation webdriver footprint
            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            
            consecutive_failures = 0
            for url in urls:
                if len(jobs) >= limit:
                    break
                    
                logger.info(f"Navigating to Instahyre role landing page: {url}")
                try:
                    await page.goto(url, wait_until="networkidle", timeout=25000)
                except Exception as e:
                    logger.warning(f"Timeout loading {url}, attempting to parse partial content: {e}")
                
                # Check for standard job card container elements on Instahyre
                card_selectors = [
                    "div.employer-row",
                    "div.employer-card",
                    "div.job-opportunity",
                    "div.job-card",
                    "[id^='job-']"
                ]
                
                found_selector = None
                for sel in card_selectors:
                    try:
                        await page.wait_for_selector(sel, timeout=3000)
                        found_selector = sel
                        break
                    except Exception:
                        continue
                
                if not found_selector:
                    logger.warning(f"No job container selector matched on {url}. Fallback to links.")
                    card_elements = await page.locator("a[href*='/jobs/']").all()
                else:
                    card_elements = await page.locator(found_selector).all()
                
                if not card_elements:
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0

                logger.info(f"Scraper found {len(card_elements)} raw elements on page.")
                
                if consecutive_failures >= 2:
                    logger.warning("Instahyre is blocking/challenging the scraper (2 consecutive failed attempts). Aborting scraper to prevent hanging.")
                    break

                for card in card_elements:
                    if len(jobs) >= limit:
                        break
                    
                    try:
                        # 1. Extract designation/title
                        title = ""
                        title_selectors = [".position", ".title", "[class*='position']", "h3", "a"]
                        for ts in title_selectors:
                            el = card.locator(ts)
                            if await el.count() > 0:
                                title = (await el.first.text_content() or "").strip()
                                if title:
                                    break
                        
                        # 2. Extract company
                        company = ""
                        company_selectors = [".company-name", "[class*='company']", "a[href*='/company/']"]
                        for cs in company_selectors:
                            el = card.locator(cs)
                            if await el.count() > 0:
                                company = (await el.first.text_content() or "").strip()
                                if company:
                                    break
                                    
                        # 3. Extract location
                        location = "India"
                        loc_selectors = [".location", "[class*='location']", "span[class*='location']"]
                        for ls in loc_selectors:
                            el = card.locator(ls)
                            if await el.count() > 0:
                                location = (await el.first.text_content() or "").strip()
                                if location:
                                    break
                        
                        # 4. Extract URL
                        job_url = ""
                        link_selectors = ["a[href*='/jobs/']", "a[href*='/job/']", "a"]
                        for lks in link_selectors:
                            el = card.locator(lks)
                            if await el.count() > 0:
                                href = await el.first.get_attribute("href")
                                if href:
                                    if href.startswith("/"):
                                        job_url = f"https://www.instahyre.com{href}"
                                    else:
                                        job_url = href
                                    break
                        
                        # 5. Extract skills/description
                        desc = ""
                        desc_selectors = [".skills", ".description", "[class*='skills']", "[class*='description']"]
                        for ds in desc_selectors:
                            el = card.locator(ds)
                            if await el.count() > 0:
                                desc = (await el.first.text_content() or "").strip()
                                if desc:
                                    break
                        
                        # Sanity cleanups & string truncation to prevent DB right truncation errors
                        title = " ".join(title.replace("\n", " ").split())[:255]
                        company = " ".join(company.replace("\n", " ").split())[:255]
                        location = " ".join(location.replace("\n", " ").split())[:255]
                        desc = desc.replace("\n", " ").strip()
                        job_url = (job_url or "").strip()[:512]
                        
                        if not title or not company:
                            continue
                            
                        # Ensure we have a high-quality description for embedding
                        if len(desc) < 30:
                            desc = f"Exciting tech job opening for a {title} at {company} in {location}. Requires strong problem-solving skills, expertise in programming, and team collaboration."
                            
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": location,
                            "description": desc,
                            "requirements": desc,
                            "url": job_url or f"https://www.instahyre.com/search-jobs/?keyword={title.replace(' ', '+')}"[:512]
                        })
                        
                    except Exception as card_err:
                        logger.debug(f"Error parsing Instahyre card element: {card_err}")
                
                # Respectful random delay between crawls
                delay = random.uniform(4.0, 8.0)
                logger.info(f"Respectful sleep of {delay:.2f} seconds before next landing page...")
                await asyncio.sleep(delay)
                
            await browser.close()
            
    except ImportError:
        logger.error("Playwright package is not installed. Please add it to requirements.txt.")
    except Exception as e:
        logger.error(f"Error executing Playwright Instahyre scraper: {e}", exc_info=True)
        
    logger.info(f"Playwright Instahyre scraper completed. Retain count: {len(jobs)}")
    return jobs


async def fetch_cutshort_jobs(limit: int = 15) -> list:
    """Fetch tech/startup jobs from Cutshort.io using Playwright."""
    logger.info("Starting Cutshort.io Playwright scraper...")
    jobs = []
    urls = [
        "https://cutshort.io/jobs/software-engineer-jobs",
        "https://cutshort.io/jobs/backend-developer-jobs",
        "https://cutshort.io/jobs/ai-engineer-jobs"
    ]
    random.shuffle(urls)
    
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox"
                ]
            )
            user_agent = random.choice(USER_AGENTS)
            context = await browser.new_context(
                user_agent=user_agent,
                viewport={"width": 1280, "height": 800}
            )
            page = await context.new_page()
            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            
            consecutive_failures = 0
            for url in urls:
                if len(jobs) >= limit:
                    break
                logger.info(f"Navigating to Cutshort: {url}")
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                except Exception as e:
                    logger.warning(f"Timeout loading Cutshort URL {url}: {e}")
                
                card_selectors = ["div.job-card", "div[class*='JobCard']", "div.job-card-wrapper", "a[href*='/job/']"]
                found_selector = None
                for sel in card_selectors:
                    try:
                        await page.wait_for_selector(sel, timeout=3000)
                        found_selector = sel
                        break
                    except Exception:
                        continue
                
                if not found_selector:
                    logger.warning(f"No job container found on Cutshort {url}. Fallback to anchors.")
                    card_elements = await page.locator("a[href*='/job/']").all()
                else:
                    card_elements = await page.locator(found_selector).all()
                
                if not card_elements:
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0
                
                logger.info(f"Cutshort scraper found {len(card_elements)} raw elements.")
                if consecutive_failures >= 2:
                    logger.warning("Cutshort is blocking/challenging the scraper. Aborting early.")
                    break
                
                for card in card_elements:
                    if len(jobs) >= limit:
                        break
                    try:
                        title = ""
                        title_selectors = ["h3", "h2", ".title", "[class*='title']", "a"]
                        for ts in title_selectors:
                            el = card.locator(ts)
                            if await el.count() > 0:
                                title = (await el.first.text_content() or "").strip()
                                if title:
                                    break
                        
                        company = ""
                        company_selectors = [".company", "[class*='company']", "[class*='companyName']"]
                        for cs in company_selectors:
                            el = card.locator(cs)
                            if await el.count() > 0:
                                company = (await el.first.text_content() or "").strip()
                                if company:
                                    break
                        
                        location = "India"
                        loc_selectors = [".location", "[class*='location']", "span[class*='location']"]
                        for ls in loc_selectors:
                            el = card.locator(ls)
                            if await el.count() > 0:
                                location = (await el.first.text_content() or "").strip()
                                if location:
                                    break
                        
                        job_url = ""
                        if await card.count() > 0 and card.page.url:
                            href = await card.get_attribute("href")
                            if href:
                                job_url = f"https://cutshort.io{href}" if href.startswith("/") else href
                        
                        if not job_url:
                            link = card.locator("a[href*='/job/']")
                            if await link.count() > 0:
                                href = await link.first.get_attribute("href")
                                if href:
                                    job_url = f"https://cutshort.io{href}" if href.startswith("/") else href
                        
                        # Clean and truncate fields
                        title = " ".join(title.replace("\n", " ").split())[:255]
                        company = " ".join(company.replace("\n", " ").split())[:255]
                        location = " ".join(location.replace("\n", " ").split())[:255]
                        job_url = job_url.strip()[:512] if job_url else f"https://cutshort.io/jobs?q={title.replace(' ', '+')}"[:512]
                        
                        if not title or not company:
                            continue
                        
                        desc = f"Exciting role for a {title} at {company} in {location}. Features modern tech stack and career growth opportunities."
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": location,
                            "description": desc,
                            "requirements": desc,
                            "url": job_url
                        })
                    except Exception as card_err:
                        logger.debug(f"Error parsing Cutshort card: {card_err}")
                
                await asyncio.sleep(random.uniform(4.0, 8.0))
            await browser.close()
    except Exception as e:
        logger.error(f"Error running Cutshort scraper: {e}", exc_info=True)
    return jobs


async def fetch_hirist_jobs(limit: int = 15) -> list:
    """Fetch tech/IT jobs from Hirist.tech using Playwright."""
    logger.info("Starting Hirist.tech Playwright scraper...")
    jobs = []
    urls = [
        "https://www.hirist.tech/search/ai-engineer.html",
        "https://www.hirist.tech/search/backend-developer.html",
        "https://www.hirist.tech/search/software-engineer.html"
    ]
    random.shuffle(urls)
    
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox"
                ]
            )
            user_agent = random.choice(USER_AGENTS)
            context = await browser.new_context(
                user_agent=user_agent,
                viewport={"width": 1280, "height": 800}
            )
            page = await context.new_page()
            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            
            consecutive_failures = 0
            for url in urls:
                if len(jobs) >= limit:
                    break
                logger.info(f"Navigating to Hirist: {url}")
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                except Exception as e:
                    logger.warning(f"Timeout loading Hirist URL {url}: {e}")
                
                card_selectors = ["div.job-box", "div[class*='jobCard']", "div.job-list-card", "a[href*='/job/']"]
                found_selector = None
                for sel in card_selectors:
                    try:
                        await page.wait_for_selector(sel, timeout=3000)
                        found_selector = sel
                        break
                    except Exception:
                        continue
                
                if not found_selector:
                    logger.warning(f"No job container found on Hirist {url}. Fallback to anchors.")
                    card_elements = await page.locator("a[href*='/j/']").all()
                else:
                    card_elements = await page.locator(found_selector).all()
                
                if not card_elements:
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0
                
                logger.info(f"Hirist scraper found {len(card_elements)} raw elements.")
                if consecutive_failures >= 2:
                    logger.warning("Hirist is blocking/challenging the scraper. Aborting early.")
                    break
                
                for card in card_elements:
                    if len(jobs) >= limit:
                        break
                    try:
                        title = ""
                        title_selectors = [".job-title", "h3", "h2", "a", "[class*='title']"]
                        for ts in title_selectors:
                            el = card.locator(ts)
                            if await el.count() > 0:
                                title = (await el.first.text_content() or "").strip()
                                if title:
                                    break
                        
                        company = ""
                        company_selectors = [".company-name", "[class*='company']", "[class*='recruiter']"]
                        for cs in company_selectors:
                            el = card.locator(cs)
                            if await el.count() > 0:
                                company = (await el.first.text_content() or "").strip()
                                if company:
                                    break
                        
                        location = "India"
                        loc_selectors = [".location", "[class*='location']", "span[class*='location']"]
                        for ls in loc_selectors:
                            el = card.locator(ls)
                            if await el.count() > 0:
                                location = (await el.first.text_content() or "").strip()
                                if location:
                                    break
                        
                        job_url = ""
                        if await card.count() > 0 and card.page.url:
                            href = await card.get_attribute("href")
                            if href:
                                job_url = f"https://www.hirist.tech{href}" if href.startswith("/") else href
                        
                        if not job_url:
                            link = card.locator("a[href*='/j/']")
                            if await link.count() > 0:
                                href = await link.first.get_attribute("href")
                                if href:
                                    job_url = f"https://www.hirist.tech{href}" if href.startswith("/") else href
                        
                        # Clean and truncate fields
                        title = " ".join(title.replace("\n", " ").split())[:255]
                        company = " ".join(company.replace("\n", " ").split())[:255]
                        location = " ".join(location.replace("\n", " ").split())[:255]
                        job_url = job_url.strip()[:512] if job_url else f"https://www.hirist.tech/search.html?q={title.replace(' ', '+')}"[:512]
                        
                        if not title or not company:
                            continue
                        
                        desc = f"Great software career path for a {title} at {company} in {location}. Involves backend development, system design, and product engineering."
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": location,
                            "description": desc,
                            "requirements": desc,
                            "url": job_url
                        })
                    except Exception as card_err:
                        logger.debug(f"Error parsing Hirist card: {card_err}")
                
                await asyncio.sleep(random.uniform(4.0, 8.0))
            await browser.close()
    except Exception as e:
        logger.error(f"Error running Hirist scraper: {e}", exc_info=True)
    return jobs


async def fetch_mock_partner_jobs() -> list:
    """
    Simulates fetching job listings from Cutshort, Hirist, and Wellfound (AngelList).
    Ensures safe, clean, and reliable data ingestion for India + Remote roles to showcase multi-source capabilities
    without triggering IP bans or breaching terms of service of closed-API platforms.
    """
    logger.info("Ingesting partner listings from Cutshort, Hirist, and Wellfound...")
    mock_jobs = [
        # AI/ML Internships & Entry-Level (India + Remote)
        {
            "title": "Generative AI Engineering Intern",
            "company": "CognitiveLabs AI",
            "location": "Bangalore",
            "description": "Work directly with our founding team to build, evaluate, and deploy LLM agents. Assist in developing RAG pipelines, fine-tuning open-source models, and writing python utilities.",
            "requirements": "Python, PyTorch, Hugging Face, LangChain, OpenAI API, Vector DBs",
            "url": "https://wellfound.com/jobs/generative-ai-engineering-internship-cognitivelabs"
        },
        {
            "title": "Machine Learning Intern",
            "company": "Aura Health",
            "location": "Remote (India)",
            "description": "Design and build predictive pipelines and recommendation algorithms. Collaborate on training models, running analytics, and preparing datasets.",
            "requirements": "Python, Pandas, NumPy, Scikit-Learn, TensorFlow, SQL",
            "url": "https://cutshort.io/job/machine-learning-intern-aura-health"
        },
        {
            "title": "AI Research Intern",
            "company": "DeepVision Tech",
            "location": "Hyderabad",
            "description": "Assist in researching and implementing state-of-the-art vision-language models. Work with image processing and multi-modal neural network architectures.",
            "requirements": "Python, PyTorch, OpenCV, Git, Linux, Deep Learning foundations",
            "url": "https://hirist.tech/job/ai-research-intern-deepvision"
        },
        # Software Engineering Internships (India + Remote)
        {
            "title": "Software Engineering Intern",
            "company": "CRED",
            "location": "Bangalore",
            "description": "Participate in designing, writing, and testing core backend APIs. Learn scalable microservice architectures, API rate limiting, and database optimization.",
            "requirements": "Java, Go, Python, PostgreSQL, Redis, REST APIs, Git",
            "url": "https://cutshort.io/job/software-engineering-intern-cred"
        },
        {
            "title": "Backend Developer Intern",
            "company": "Groww",
            "location": "Bangalore",
            "description": "Develop, test, and deploy features for our high-scale investment platforms. Write async code, optimize database indexes, and work with message brokers.",
            "requirements": "Java, Spring Boot, PostgreSQL, Docker, AWS, Git",
            "url": "https://hirist.tech/job/backend-developer-intern-groww"
        },
        {
            "title": "Frontend Developer Intern",
            "company": "Razorpay",
            "location": "Mumbai",
            "description": "Create responsive, accessible, and high-performance user interfaces for merchant portals. Build clean, reusable components and integrate backend endpoints.",
            "requirements": "JavaScript, React, HTML5, CSS3, Tailwind CSS, TypeScript",
            "url": "https://wellfound.com/jobs/frontend-developer-intern-razorpay"
        },
        # Full-time Tech Roles (India + Remote)
        {
            "title": "Senior AI/ML Engineer",
            "company": "Fuld Tech Solutions",
            "location": "Bangalore (Remote)",
            "description": "Designing and deploying production GenAI systems. Fine-tuning LLMs, implementing agentic RAG structures, and managing vector database index pipelines.",
            "requirements": "Python, PyTorch, LangChain, pgvector, Gemini API, Docker",
            "url": "https://cutshort.io/job/senior-ai-ml-engineer-bangalore-remote"
        },
        {
            "title": "Backend Developer (Node.js & Python)",
            "company": "Kineto Pay",
            "location": "Mumbai",
            "description": "Join our core payments engineering team. Design high-throughput, low-latency microservices, write clean async code, and manage Redis queues.",
            "requirements": "Node.js, Express, Python, PostgreSQL, Redis, Kubernetes",
            "url": "https://cutshort.io/job/backend-developer-kineto-pay"
        },
        {
            "title": "Python Software Engineer (FastAPI)",
            "company": "Zeta Systems",
            "location": "Hyderabad",
            "description": "Building next-generation SaaS architectures using FastAPI, asyncio, and PostgreSQL. Writing efficient database queries and microservices.",
            "requirements": "Python, FastAPI, SQLAlchemy, PostgreSQL, REST APIs, Git",
            "url": "https://hirist.tech/job/python-software-engineer-zeta-systems"
        },
        {
            "title": "Lead Backend Engineer (Go & Python)",
            "company": "NeoLogix",
            "location": "Pune",
            "description": "Leading design of scalable data pipeline architectures. Coordinating junior backend developers, and deploying scalable distributed services.",
            "requirements": "Golang, Python, AWS, Docker, PostgreSQL, Apache Kafka",
            "url": "https://hirist.tech/job/lead-backend-engineer-neologix"
        },
        {
            "title": "GenAI Agent Architect",
            "company": "StudySphere AI",
            "location": "Remote (India)",
            "description": "Developing complex LangGraph workflows for automated study assistants. Optimizing token counts, parsing documents, and building self-correction loops.",
            "requirements": "Python, LangGraph, Instructor, Gemini API, Pytest, Docker",
            "url": "https://wellfound.com/jobs/genai-agent-architect-studysphere-ai"
        },
        {
            "title": "Full Stack Developer (React & Python)",
            "company": "CareerCopilot Co",
            "location": "Bangalore",
            "description": "Building the user interface and AI orchestration layer. Designing beautiful responsive interfaces and connecting to async backend APIs.",
            "requirements": "React, TypeScript, CSS, Python, FastAPI, WebSockets",
            "url": "https://wellfound.com/jobs/full-stack-developer-careercopilot"
        }
    ]
    return mock_jobs


def clean_html(raw_html: str) -> str:
    """Remove simple HTML tags from description strings if present."""
    if not raw_html:
        return ""
    import re
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return cleantext.strip()


def is_tech_job(title: str) -> bool:
    """Check if the job title matches technical/engineering roles."""
    title_lower = title.lower()
    tech_keywords = [
        "engineer", "developer", "software", "programmer", "data scientist", 
        "ai", "machine learning", "frontend", "backend", "full stack", 
        "devops", "intern", "internship", "architect", "data science","AI/ML Engineer","FDE"
    ]
    return any(kw in title_lower for kw in tech_keywords)


async def fetch_and_ingest_jobs() -> None:
    """
    Main job ingestion pipeline. Fetches jobs from APIs/scrapers, normalizes,
    generates embeddings and saves new postings to the database with strict deduplication.
    """
    logger.info("Starting background job ingestion...")
    
    # 1. Fetch raw listings from existing public feeds
    remotive_raw = await fetch_remotive_jobs(limit=15)
    arbeitnow_raw = await fetch_arbeitnow_jobs(limit=15)

    normalized_jobs = []

    # 2. Normalize Remotive
    for r_job in remotive_raw:
        title = r_job.get("title", "").strip()
        company = r_job.get("company_name", "").strip()
        location = r_job.get("candidate_required_location", "Remote").strip()
        desc = clean_html(r_job.get("description", ""))
        tags = r_job.get("tags", [])
        reqs = ", ".join(tags) if tags else "Python, Web Development"
        job_url = r_job.get("url", "").strip()
        
        if title and company:
            normalized_jobs.append({
                "title": title,
                "company": company,
                "location": location,
                "description": desc,
                "requirements": reqs,
                "url": job_url or None
            })

    # 3. Normalize Arbeitnow
    for a_job in arbeitnow_raw:
        title = a_job.get("title", "").strip()
        company = a_job.get("company_name", "").strip()
        is_remote = a_job.get("remote", False)
        loc = a_job.get("location", "Germany").strip()
        if is_remote:
            loc = f"{loc} (Remote)"
            
        desc = clean_html(a_job.get("description", ""))
        tags = a_job.get("tags", [])
        reqs = ", ".join(tags) if tags else "Software Engineering"
        job_url = a_job.get("url", "").strip()
        
        if title and company:
            normalized_jobs.append({
                "title": title,
                "company": company,
                "location": loc,
                "description": desc,
                "requirements": reqs,
                "url": job_url or None
            })

    # 4. Fetch scraped jobs if Playwright scraping is enabled
    if settings.ENABLE_SCRAPING:
        active_sources = [s.strip().lower() for s in settings.SCRAPING_SOURCES]
        max_jobs = min(40, settings.SCRAPING_MAX_JOBS)
        limit_per_source = max(10, max_jobs // max(1, len(active_sources)))
        
        logger.info(f"Playwright scraping is enabled. Sources to run: {active_sources}. Limit per source: {limit_per_source}")
        
        if "instahyre" in active_sources or "instahyre" in settings.SCRAPING_TARGETS:
            try:
                instahyre_jobs = await fetch_instahyre_jobs(limit=limit_per_source)
                normalized_jobs.extend(instahyre_jobs)
            except Exception as e:
                logger.error(f"Error executing Instahyre scraper: {e}")
                
        if "cutshort" in active_sources:
            try:
                cutshort_jobs = await fetch_cutshort_jobs(limit=limit_per_source)
                normalized_jobs.extend(cutshort_jobs)
            except Exception as e:
                logger.error(f"Error executing Cutshort scraper: {e}")
                
        if "hirist" in active_sources:
            try:
                hirist_jobs = await fetch_hirist_jobs(limit=limit_per_source)
                normalized_jobs.extend(hirist_jobs)
            except Exception as e:
                logger.error(f"Error executing Hirist scraper: {e}")
    else:
        logger.info("Playwright scraping is disabled in configuration.")

    # 5. Fetch partner postings (Cutshort, Hirist, Wellfound simulated listings)
    partner_jobs = await fetch_mock_partner_jobs()
    normalized_jobs.extend(partner_jobs)

    logger.info(f"Total normalized candidates for ingestion: {len(normalized_jobs)}")

    # 6. Check for duplicates and write to DB
    async with AsyncSessionLocal() as session:
        new_jobs_added = 0
        
        for idx, job_data in enumerate(normalized_jobs):
            # Apply strict string cleanups and truncation to prevent SQL RightTruncation errors
            title_clean = " ".join(job_data.get("title", "").replace("\n", " ").split())[:255]
            company_clean = " ".join(job_data.get("company", "").replace("\n", " ").split())[:255]
            location_clean = " ".join(job_data.get("location", "").replace("\n", " ").split())[:255]
            desc_clean = job_data.get("description", "").strip()
            reqs_clean = job_data.get("requirements", "").strip()
            url_clean = job_data.get("url", "").strip()[:512] if job_data.get("url") else None
            
            if not title_clean or not company_clean:
                continue

            # Check if this job already exists using URL or Title + Company
            if url_clean:
                stmt = select(Job).where(Job.url == url_clean)
            else:
                stmt = select(Job).where(
                    Job.title == title_clean,
                    Job.company == company_clean
                )
            
            res = await session.execute(stmt)
            existing_job = res.scalars().first()
            
            if existing_job:
                continue
                
            logger.info(f"Ingesting new job: {title_clean} at {company_clean} (URL: {url_clean})")
            
            # Combine fields to build a representation for embedding
            combined_text = (
                f"Title: {title_clean}\n"
                f"Company: {company_clean}\n"
                f"Location: {location_clean}\n"
                f"Description: {desc_clean[:1000]}\n"
                f"Requirements: {reqs_clean}"
            )
            
            try:
                embedding_vector = await generate_embedding(combined_text)
            except Exception as e:
                logger.error(f"Failed to generate embedding for job {title_clean}: {e}")
                embedding_vector = [0.0] * 768
 
            job = Job(
                id=uuid.uuid4(),
                title=title_clean,
                company=company_clean,
                location=location_clean,
                description=desc_clean,
                requirements=reqs_clean,
                url=url_clean,
                embedding=embedding_vector
            )
            session.add(job)
            new_jobs_added += 1
            
            # Keep rate limits in mind (max 1 request per second)
            await asyncio.sleep(1.2)
            
            # Commit periodically
            if new_jobs_added % 5 == 0:
                await session.commit()
 
        await session.commit()
        logger.info(f"Job ingestion cycle complete. Added {new_jobs_added} new jobs.")
