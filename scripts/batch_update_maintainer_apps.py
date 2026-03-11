import csv
from pathlib import Path

tracker_path = Path("applications/job_applications/application_tracker.csv")
rows = []
fields = []

with open(tracker_path, mode="r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames
    rows = list(reader)

maintainer_resume = "applications/anthropic/tailored_resumes/Igor_Ganapolsky_Open_Source_Maintainer_AI.pdf"

for row in rows:
    company = row["Company"].strip()
    role = row["Role"].strip()
    status = row["Status"].strip()

    # Target all technical/platform roles at Anthropic and Anyscale
    is_target_company = company in ["Anthropic", "Anyscale"]
    is_target_role = any(
        kw in role.lower()
        for kw in [
            "engineer",
            "infrastructure",
            "platform",
            "inference",
            "architect",
            "solutions",
        ]
    )

    if is_target_company and is_target_role and status != "Applied":
        row["Status"] = "ReadyToSubmit"
        row["Submitted Resume Path"] = maintainer_resume
        if "greenhouse" in row["Career Page URL"]:
            row["Submission Lane"] = "ci_auto:greenhouse"
        elif "ashby" in row["Career Page URL"]:
            row["Submission Lane"] = "ci_auto:ashby"
        elif "lever" in row["Career Page URL"]:
            row["Submission Lane"] = "ci_auto:lever"

with open(tracker_path, mode="w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)

print(
    "Batch updated Anthropic and Anyscale roles to ReadyToSubmit with Maintainer resume."
)
