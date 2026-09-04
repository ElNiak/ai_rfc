---
title: "$title"
abbrev: "$abbrev"
docname: $draft_name-latest
category: info

ipr: trust200902
area: General
workgroup: Individual Submission
keyword: Internet-Draft

stand_alone: yes
smart_quotes: no
pi: [toc, sortrefs, symrefs]

author:
 -
    ins: C. Crochet
    name: Christophe Crochet
    organization: UCLouvain
    email: christophe.cr.dev@gmail.com

normative:

informative:
  SOURCE:
    title: "The $target implementation, as pinned in the reconstruction workspace"
    author:
      -
        org: "The $target developers"


--- abstract

This document reconstructs the specification of $target from its
implementation history. Each revision reflects the implementation as it
stood at one cluster of its development timeline; every normative statement
cites a claim in the accompanying evidence manifest, whose status is
adjudicated from anchored evidence rather than asserted.

{::comment}
Replace the paragraph above once the system is understood: what $target is,
what problem it solves, and who would implement this specification. The
sentence "Each revision reflects the implementation as it stood at one
cluster" is how the lint recognises an unwritten abstract.
{:/comment}


--- middle

# Introduction

{::comment}
What the system is and does, for a reader who has never seen it. Three
paragraphs at most. No cluster ordinals, no counts of statements added or
withdrawn — that history belongs in the Change Log appendix.
{:/comment}

## Scope

{::comment}
Which behaviours this document specifies and which it leaves out.
{:/comment}

## Reconstruction Method

This specification was reconstructed from the implementation's repository
history, one timeline cluster at a time. Each cluster's evidence was mined
into claims, each claim was anchored to code, decision records, papers,
interviews or test runs, and each claim's evidential status was adjudicated
from those anchors rather than asserted. Every normative statement below
cites the claim that supports it, and the manifest checkpointed beside each
revision is what the citation gate verifies those citations against.

## Organization

{::comment}
One sentence per major section, in order.
{:/comment}

# Conventions and Definitions

{::boilerplate bcp14-tagged}

Claim citations are backticked `ai_rfc` tokens that name a claim id in the
evidence manifest checkpointed beside this revision; the citation gate
verifies that every cited claim exists there.

## Terminology

{::comment}
A definition list of the system's own terms, each on first use.
{:/comment}

# Architecture Overview

{::comment}
The components and how they interact, with one figure whose caption cites
the claims it depicts. See the figures skill.
{:/comment}

# Data Model and Structures

{::comment}
Records, messages, enumerations and state machines, as tables and figures.
Structure blocks rendered by the substrate are pasted here verbatim.
{:/comment}

# Protocol Operation

{::comment}
Behaviour, organised by concern (one subsection per concern), never by the
order clusters were processed. Each normative sentence carries one keyword
and one citation.
{:/comment}

# Configuration and Defaults

{::comment}
A table: key, type, default, effect, citation.
{:/comment}

# Error Handling

{::comment}
What the system does on each class of failure, with citations.
{:/comment}

# Observed Accidental Behaviour

{::comment}
Claims with intent accidental — defects the history marks as unintended —
described, never as requirements.
{:/comment}

# Security Considerations

{::comment}
The threats the interface is exposed to and what the implementation does or
does not do about each: authentication, authorization, confidentiality,
integrity, resource exhaustion. "No claim mentions authentication" is a
finding about the system worth stating, not a reason to leave this empty.
{:/comment}

# IANA Considerations

This document has no IANA actions.


--- back

# Change Log

{::comment}
One entry per revision tag: the tag, the cluster, whether the change was
normative, and what moved. The per-cluster narration lives here.
{:/comment}

# Implementation Notes

{::comment}
Implementation facts that are not requirements: class paths, test doubles,
packaging, file locations. Moved here from normative sections, never dropped.
{:/comment}

# Open Questions

{::comment}
Questions from the register that block a section, quoting the claim.
{:/comment}

# Acknowledgements

{::comment}
The developers and authors whose work was reconstructed.
{:/comment}
