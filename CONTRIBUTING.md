# Contributing

## Branching & Pull Requests

**All changes must go through a Pull Request.** Direct pushes to `master` are not allowed.

### One feature per PR

Each PR must address a **single feature, fix, or change**. Do not bundle unrelated work.

| ✅ Good | ❌ Bad |
|---|---|
| PR: "Add --timeout flag" | PR: "Add --timeout flag, fix mail bug, update README" |
| PR: "Fix rotation threshold off-by-one" | PR: "Various fixes and improvements" |

### Branch naming

```
feature/<short-description>   # new functionality
fix/<short-description>       # bug fixes
chore/<short-description>     # tooling, deps, CI, docs
```

Examples:
- `feature/service-connection-update`
- `fix/keyvault-parse-error`
- `chore/update-poetry-lock`

### Workflow

> ⚠️ **Always start a new branch from an up-to-date `master`.**  
> Never branch from another feature branch or from a stale local `master`.

```
# 1. Update master first, then branch
git checkout master
git pull origin master
git checkout -b feature/my-feature

# 2. Make changes, commit
git add -A
git commit -m "feat: describe what and why"

# 3. Push and open PR
git push -u origin feature/my-feature
# Open PR on GitHub targeting master
```

### Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

| Prefix | Use for |
|---|---|
| `feat:` | New feature |
| `fix:` | Bug fix |
| `chore:` | CI, deps, tooling |
| `style:` | Formatting, output labels |
| `test:` | Test-only changes |
| `docs:` | Documentation only |

### PR checklist

- [ ] Branch targets `master`
- [ ] Single feature / concern
- [ ] All CI checks pass (unit tests + e2e tests)
- [ ] PR description explains *what* and *why*

### CI

The CI pipeline runs automatically on every PR:

- **Unit tests** — `pytest tests/ --ignore=tests/test_e2e.py`
- **E2E tests** — `pytest tests/test_e2e.py`

Both must be green before merging.
