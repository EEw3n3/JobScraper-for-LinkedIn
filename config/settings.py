import os
from pathlib import Path
from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[1] / ".env")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MAX_PAGES = 6

# LinkedIn time parameters:
# &f_TPR=r604800 (Past week)
# &f_TPR=r86400  (Past 24 hours)

# Target roles: software development, Python backend, automation,
# applied AI and system administration.
SEARCH_ROLES = [
    "Software%20Developer",
    "Python%20Developer",
    "Backend%20Developer",
    "Automation%20Engineer",
    "AI%20Engineer",
    "System%20Administrator",
]

# Riga (office or hybrid) is covered by Latvia; "Europe (Remote)" is added in main.py.
SEARCH_LOCATIONS = ["Latvia"]

SITES = [
    {
        "country": location.replace("%20", " "),
        "source": "LinkedIn",
        "url": f"https://www.linkedin.com/jobs/search?keywords={role}&location={location}&f_TPR=r3600",
    }
    for role in SEARCH_ROLES
    for location in SEARCH_LOCATIONS
]

SITES.extend(
    {
        "country": "Europe (Remote)",
        "source": "LinkedIn",
        "url": f"https://www.linkedin.com/jobs/search?keywords={role}&location=Europe&f_WT=2&f_TPR=r3600",
    }
    for role in SEARCH_ROLES
)

# LinkedIn system links to ignore
STOP_LINK_TEXTS = [
    "about", "accessibility", "user agreement", "privacy policy",
    "cookie policy", "copyright policy", "brand policy",
    "guest controls", "community guidelines"
]

# Target keywords for the requested roles
TARGET_KEYWORDS = [
    "ai & automation", "ai and automation",
    "business systems + ai", "business systems and ai",
    "technical business analysis", "technical business analyst",
    "solution architect", "solutions architect",
    "devops engineer",
]

# Stop roles
STOP_ROLES = []

# QA keywords (allowed only if automation is mentioned)
QA_WORDS = ["qa", "quality assurance", "test", "tester"]

# Stop words for senior roles and management
STOP_SENIOR_WORDS = [
    "senior", "lead", "principal", "architect", "head of", "staff", "manager", "director",
    "vecākais", "vecākā", "vadošais", "vadošā", "vadītājs", "vadītāja",
]

CLOUD_SIGNALS = [
    "sre", "site reliability", "platform engineer", "cloud",
    "devops", "kubernetes", "k8s", "aws", "gcp", "azure",
    "docker", "terraform", "ci/cd", "infrastructure"
]
