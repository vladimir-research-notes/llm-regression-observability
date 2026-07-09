"""Trace importers: external trace shapes in, ``RunRecord``s out.

Milestone 10 defines — and does not depend on — the contracts by which traces from
sibling systems enter this framework. Six importers are registered:

==========================  ================================================
:mod:`.native`              ``llmreg`` — this repo's own export; lossless
:mod:`.code_agent_runtime`  a coding agent's session, as a step list
:mod:`.bioagentbench`       benchmark episodes: a task definition *and* an answer
:mod:`.bioscientist_os`     a multi-node workflow, flattened (and what that costs)
:mod:`.agent_control_bench` refuse-or-answer trials and policy violations
:mod:`.claude_api`          *recorded* Messages-API responses; never calls an API
==========================  ================================================

**None of those repositories is required to build or test this one.** Their trace
shapes are declared here and pinned against committed synthetic examples under
``data/external/``. If a real emitter differs, the importer is where the difference
is absorbed, and its example fixture is where the disagreement becomes a test
failure rather than a silent mis-score.

The contract every importer holds to — account for every field, invent no
measurement, guess no attribution, import no secret — is in :mod:`.base`. The
end-to-end proof, that an imported trace flows through the *unchanged* executor,
evaluators, comparison engine, span projection and report generator, is in
:mod:`.runs` and ``tests/test_integration_contracts.py``.

This subpackage is imported lazily (it is deliberately **not** re-exported from
``datasets/__init__``): :mod:`.runs` reaches back into :mod:`...executor`, which
imports :mod:`...datasets`, and a package that imports its own importer at import
time would close that loop.
"""

from __future__ import annotations

from .agent_control_bench import AgentControlBenchImporter
from .base import (
    APP,
    INFRA,
    LATENCY_HAZARD,
    ImportContext,
    ImportResult,
    TraceImporter,
    TraceImportError,
)
from .bioagentbench import BioAgentBenchImporter
from .bioscientist_os import BioScientistOsImporter
from .claude_api import ClaudeApiImporter
from .code_agent_runtime import CodeAgentRuntimeImporter
from .native import NativeImporter
from .registry import (
    IMPORTERS,
    SOURCES,
    get_importer,
    import_bundle,
    import_document,
    import_path,
)
from .runs import (
    IMPORT_META_JSON,
    ImportedRun,
    ImportedTraceAdapter,
    ImportMeta,
    adapter_name,
    import_run,
)

__all__ = [
    # Contract
    "TraceImporter",
    "TraceImportError",
    "ImportContext",
    "ImportResult",
    "APP",
    "INFRA",
    "LATENCY_HAZARD",
    # Registry
    "IMPORTERS",
    "SOURCES",
    "get_importer",
    "import_document",
    "import_bundle",
    "import_path",
    # Importers
    "NativeImporter",
    "CodeAgentRuntimeImporter",
    "BioAgentBenchImporter",
    "BioScientistOsImporter",
    "AgentControlBenchImporter",
    "ClaudeApiImporter",
    # Executing an imported trace
    "ImportedTraceAdapter",
    "ImportedRun",
    "ImportMeta",
    "IMPORT_META_JSON",
    "adapter_name",
    "import_run",
]
