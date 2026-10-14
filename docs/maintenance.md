# Maintenance and documentation publication

Use the current owner-authorized task and [repository contract](../AGENTS.md) for scope. The
[product guide](product/Home.md) describes current capabilities; [historical records](history/README.md)
preserve dated delivery evidence and do not authorize new work.

## Before making a change

1. Verify current source, relevant effective rulesets, and any deployed workflow pin involved.
2. Identify the requested behavior, approved files/responsibilities, exclusions, evidence, and stop.
3. Use an isolated checkout and preserve unrelated work. Do not reactivate a closed project to
   manufacture a current milestone.
4. Preserve immutable Standard/roadmap bytes, canonical traceability, and independent source
   protection. Owner authorization cannot waive deterministic product checks.

Run the complete source-proof commands in [AGENTS.md](../AGENTS.md), with Python 3.12 and the exact
lock. Keep generated caches/build outputs in owned temporary paths where practical. Refresh the
current source-bound review evidence. The owner authorizes the work; current Gate runs do not
require an owner technical-review comment.
Deliver through the normal protected PR path and verify final-head checks, merge, and scope.

## Capability documentation checklist

For each affected capability, review its purpose, supported profiles/paths, inputs and commands,
evidence class, actual rule, failure/repair guidance, assurance limit, source reference, and diagram.

- [ ] Current source/tests/protection support every public claim.
- [ ] Generic examples match production parsers and the existing CLI; no real consumer incident
      or closed-project execution directive defines a feature.
- [ ] Any known limitation is current and described at the strength its evidence supports.
- [ ] Links, headings, historical compatibility anchors, code fences, navigation, and diagrams work.
- [ ] Historical observations and retained artifacts are preserved without relabeling failure as PASS.
- [ ] Exact immutable hashes and the authorized executable/workflow boundary are verified.
- [ ] All required checks pass on the final documentation head and the PR merges normally.

The frozen roadmap remains a historical foundation record at its protected original path. Do not
edit it to make current reader documentation generic; explain its status through the history index.

## One source per wiki page

Canonical pages live in `docs/product/`; their filenames match wiki page slugs. Keep stable URLs
and compatibility anchors when moving a detailed subject to its primary reference page.
Author content in the repository first. Do not repair wording only in the wiki.

Publication is manual and follows a protected source merge:

1. Record the documentation merge's full SHA and publication date. Read canonical pages from
   that exact commit, rather than an unmerged working tree or a moving branch.
2. Copy each canonical page to the wiki's matching filename. Convert a link to another product
   `.md` page into its wiki slug, retaining any fragment. Convert all other repository-relative
   links into full GitHub `blob/<merge-sha>/...` links (or `tree` for directories).
3. Use a stable shared footer linking to canonical documentation and this maintenance guide.
   Group the sidebar into practical use, configuration, and technical references; include every
   product page. Keep source commit IDs, dates, delivery narratives, and publication records out
   of reader-facing prose and footers. Retain publication identities in the delivery evidence.
4. Validate the projected content against the canonical pages: only links and shared publication
   furniture should differ. Commit and push one normal wiki commit on `master`; never force-push.
5. Open published pages and verify links, tables, navigation, code blocks, and both diagrams. Read
   back the published wiki commit and compare each page with the exact source used for publication.
6. Retain the PR/merge/wiki identities and verification results. A merged source PR with an
   unpublished or unverified wiki is partial delivery when the task includes wiki refresh.

No wiki publishing service, secret, scheduled automation, or new CI workflow is required. If
publication needs correction, fix the canonical source first when the content is wrong; correct
projection-only mistakes with a normal wiki commit. Never rewrite historical wiki commits.

## Write product documentation

Describe implemented behavior in the present tense. Do not append release notes, "now supports"
claims, before/after policy narratives, or dated status to product pages. Use exact schema names
and Git identities where needed to explain a technical contract, not as publication announcements.
Put detailed subjects in one primary page and link from the others.

Keep the README a concise entry point. Verify that a non-technical owner can find a PR result and
ask for a repair, and that an implementer can reach configuration, source references, and command
instructions. Explain where commands run, required replacements, expected outputs, and limits.
After checking accuracy, remove repetition. Follow this checklist for each documentation change;
the wiki remains a projection of the canonical pages.

## Preserve links while retiring documents

Move editable old records into a date-stamped archive with original paths and byte hashes in its
manifest. Original entrypoints retain explicit historical anchors linked to their archived
sections. Publish current corrections in the archive index rather than editing original claims.
Keep retained qualification artifact bytes and original paths. Raw snapshot links can also be
resolved at the manifest's immutable source commit.

## Evidence and stopping condition

Record the actual source changes, validations, exact protected delivery, published wiki commit
when applicable, and remaining limitations. Documentation-only work does not require a new Gate
deployment pin when executable/workflow blobs match the deployed commit. Do not enroll another
repository, change rulesets, create a project, or repair consumer code as incidental documentation
work. Stop when the task's complete requested outcome is verified.
