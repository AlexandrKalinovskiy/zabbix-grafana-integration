---
name: update-docs-after-changes
description: Ensures project documentation (README.md files) is always updated after any project changes — new services, config changes, new scripts, port changes, or architecture changes. Use this skill whenever files in a project are created or modified.
---

# Update Documentation After Changes

After **every change** to a project, check and update the relevant documentation before finishing the task.

## When to use this skill

- When adding or modifying a `docker-compose.yml` or any `docker-compose.*.yml`
- When adding a new service, container, or microservice
- When changing ports, environment variables, or volume mappings
- When creating a new script (`.py`, `.sh`, etc.)
- When adding a new directory or significant file
- When the system architecture changes in any way

## How to use it

### Step 1: Identify affected documentation

After making changes, scan all `*.md` files in the project root and relevant subdirectories.

### Step 2: Update the relevant sections

| Change Made | Section to Update |
|---|---|
| New Docker service | Architecture diagram + service table |
| New ports exposed | Access/ports table |
| New script created | Usage instructions |
| Config change | Versions table + relevant section |
| New subdirectory | Project structure section |

### Step 3: Create docs if missing

If no README exists in the affected subdirectory → create one.

## Checklist before finishing any task

- [ ] Changed any docker-compose file? → Update service list in README
- [ ] Added a new port? → Update ports/access table
- [ ] Created a new script? → Add usage instructions
- [ ] Added a new directory? → Update project structure section
- [ ] Changed the architecture? → Update architecture diagram

## Example

When a new `yugabytedb/` directory with `docker-compose.yugabyte.yml` was added:

✅ **Correct:** Created `yugabytedb/README-yugabytedb.md` documenting ports, usage, and architecture.

❌ **Wrong:** Adding files without updating any documentation.
