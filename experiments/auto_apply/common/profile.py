"""The fictional test applicant. No real person, no real resume. The email domain is example.com (reserved, never
delivered) and the phone number is in the 555-01xx range reserved for fiction."""

from __future__ import annotations

PROFILE: dict[str, str] = {
    "first_name": "Jordan",
    "last_name": "Testcase",
    "full_name": "Jordan Testcase",
    "email": "jordan.testcase@example.com",
    "phone": "+1 555 010 0142",
    "location": "Pune, India",
    "city": "Pune",
    "country": "India",
    "current_company": "Example Labs",
    "current_title": "Software Engineer",
    "years_experience": "4",
    "website": "https://example.com/jordan-testcase",
    "linkedin": "",
    "github": "",
    "education": "B.Tech in Computer Science, Example Institute of Technology, 2021",
    "work_history": (
        "Software Engineer at Example Labs (2022 to now): built and ran Python and TypeScript services for an internal "
        "analytics product, owned its CI pipeline. Junior Developer at Sample Systems (2021 to 2022): maintained a "
        "Django web app and wrote its test suite."
    ),
    "authorized_to_work": "Yes",
    "needs_sponsorship": "No",
    "notice_period": "30 days",
    "salary_expectation": "Negotiable",
    "how_heard": "Company careers page",
    "pronouns": "",
    "demographics": "Decline to self-identify",
    "cover_letter": (
        "This is a test application from a fictional profile used to check form filling. It must not be submitted."
    ),
}

RESUME_NAME = "Jordan_Testcase_TEST_RESUME.pdf"
