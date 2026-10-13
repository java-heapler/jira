# Repository instructions

Maintain standalone src-layout/hatchling/uv architecture shared with apple-books and anki. Keep CLI, README and skills/jira/SKILL.md aligned. Use synthetic HTTP fixtures, never personal Jira issues or credentials. Writes require explicit session authorization and --confirm; never blindly retry uncertain writes. Do not add billing, trial, paid add-on or destructive operations. Keep credentials and private issue data out of git. Run uv run --extra dev pytest --cov=jira_agent --cov-branch and uv build after code changes. Do not change superai-skills.
