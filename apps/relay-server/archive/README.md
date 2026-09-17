# Archived repository files

These files come from the root of the standalone repository that used to host
this workspace. They are kept for the record only, and are deliberately parked
here rather than in their original locations:

- `workflows/` - the build and release workflows that produced the published
  image. GitHub only executes workflows from `.github/workflows/` at the root of
  a repository, so these copies are inert. They are reference material for
  wiring this component into this repository's own release pipeline.
- `issue-templates/` - the issue forms used for bug and feature reports against
  this component. Inert for the same reason; this repository keeps its own
  templates.
- `SECURITY.md`, `SUPPORT.md` - the policies of the retired repository. This
  repository's own `SECURITY.md` and `SUPPORT.md` at the root are the ones in
  force; these copies are history and their links point at a repository that no
  longer exists.

Nothing here is read by any tool. Do not treat these as active configuration.
