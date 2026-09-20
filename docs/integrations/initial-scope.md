# Initial integration scope

These are implementation-specific details, not universal Showrun contract promises.
The general [caller contract](../../contracts/caller-interaction.v1.md) governs
intent, authority, identity and evidence. This document does not expand execution
authority or claim that generic interaction has already been implemented.

## Stories fixture and comment integration

The initial managed Stories integration imports caller-supplied exported content
through the installed public import API into a fresh, empty caller-owned store.
It validates exact story, revision and content identity before launch. Arbitrary
existing stores, symlinks, changed fixtures and wrong revisions are rejected before
launch or model use. This is accident prevention, not a sandbox against the owner.

Its bounded comment grant names one prepared story, selected revision and exact
text. It covers necessary draft saves and one submission, without authorizing
feedback/model work or other mutations. Drafts and retained comments are review
state rather than changes to presentation identity. Submitted-comment verification
uses independent public-library readback of the exact annotation, revision and
status; input text is insufficient. Preserve prior fixture hash rules and request
fingerprints when inspecting or retrying retained takes.

Navigation-label filtering and application-specific comment controls describe the
initial implementation, not the intended generic operator. They must not become
a requirement for each additional application. Lifecycle, access and verification
integration can remain application-specific without hardcoding UI choreography.

## Delivery sequence

Playwright interaction and browser video recording are the first execution path.
Computer use follows later with explicit surface, authority, recording and isolation
support. Existing browser restrictions do not define the product's eventual scope.
Generic URL interaction and combined recipe-step notes are now implemented;
see [verification and support limits](../generic-ui-verification.md). This legacy
integration remains available without expanding old requests' authority.
