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

# Job sites a run collects from; `main.py scrape --source` overrides it for one run.
# cv.lv is Latvia's largest job board; remove it if you do not look for work in Latvia.
SOURCES = ["LinkedIn", "cv.lv"]

# cv.lv: every active ad in these categories is read through the site's public API.
CVLV_CATEGORIES = ["INFORMATION_TECHNOLOGY"]
# cv.lv has no search by role, so IT ads that are not engineering work are skipped by their title
# (each word also matches longer words that start with it).
CVLV_SKIP_TITLE_WORDS = [
    "sales", "pārdošan", "продаж", "marketing", "mārketing", "маркетинг", "grāmatved", "accountant",
    "бухгалтер", "recruit", "personāl", "talent acquisition", "dizain", "designer", "дизайн",
    "customer success", "account manager", "business development", "biznesa attīstīb",
]

# LinkedIn locations in order of priority: jobs from the first one are applied to first, then the next,
# and the remote search across Europe (added in main.py) only fills the rest of the daily limit.
SEARCH_LOCATIONS = ["Latvia", "Estonia", "Lithuania"]
# Local markets post few jobs a day, so these searches look back further than the remote one.
LOCAL_LOOKBACK_HOURS = 72

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
