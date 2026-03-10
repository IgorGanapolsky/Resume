import csv
from pathlib import Path

tracker_path = Path("applications/job_applications/application_tracker.csv")
rows = []
fields = []

with open(tracker_path, mode='r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames
    rows = list(reader)

for row in rows:
    if "Anthropic" in row["Company"] and "Autonomous Agent Infrastructure" in row["Role"]:
        row["Status"] = "ReadyToSubmit"
        row["Submitted Resume Path"] = "applications/anthropic/tailored_resumes/Igor_Ganapolsky_Open_Source_Maintainer_AI.pdf"
        row["Submission Lane"] = "ci_auto:greenhouse"
        row["Notes"] = "Retrying with Maintainer-focused PDF resume via Anchor Browser CI."
    elif "Anyscale" in row["Company"] and "AI / ML Solutions Engineer" in row["Role"]:
        row["Status"] = "ReadyToSubmit"
        row["Submitted Resume Path"] = "applications/anthropic/tailored_resumes/Igor_Ganapolsky_Open_Source_Maintainer_AI.pdf" # Using the same PDF for now to test
        row["Submission Lane"] = "ci_auto:ashby"

with open(tracker_path, mode='w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)

print("Updated application_tracker.csv successfully with PDF paths.")
