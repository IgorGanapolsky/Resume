import csv
from pathlib import Path

tracker_path = Path("applications/job_applications/application_tracker.csv")
rows = []
fields = []

with open(tracker_path, mode="r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames
    rows = list(reader)

# Update Anthropic role
for row in rows:
    if (
        "Anthropic" in row["Company"]
        and "Autonomous Agent Infrastructure" in row["Role"]
    ):
        row["Status"] = "Draft"
        row["Submitted Resume Path"] = (
            "resumes/Igor_Ganapolsky_Open_Source_Maintainer_AI.html"
        )
        row["Submission Lane"] = "ci_auto:greenhouse"
        row["Notes"] = "Retrying with Maintainer-focused resume via Anchor Browser CI."

# Add Anyscale role
anyscale_row = {f: "" for f in fields}
anyscale_row["Company"] = "Anyscale"
anyscale_row["Role"] = "AI / ML Solutions Engineer"
anyscale_row["Location"] = "Remote"
anyscale_row["Status"] = "Draft"
anyscale_row["Submission Lane"] = "ci_auto:ashby"
anyscale_row["Career Page URL"] = (
    "https://jobs.ashbyhq.com/anyscale/23930b20-6d9b-466d-9654-9457f5979f4a"  # Found earlier
)
anyscale_row["Submitted Resume Path"] = (
    "resumes/Igor_Ganapolsky_Open_Source_Maintainer_AI.html"
)
anyscale_row["Remote Policy"] = "remote"
anyscale_row["Remote Likelihood Score"] = "100"

rows.append(anyscale_row)

with open(tracker_path, mode="w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)

print("Updated application_tracker.csv successfully.")
